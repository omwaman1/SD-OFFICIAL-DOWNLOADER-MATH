#!/usr/bin/env python3
"""
Play & Save — Classplus Video Downloader

Flow:
  1. Play a video in the app → script detects it
  2. Script sets challenge → replay the video
  3. Keys captured → downloads, decrypts, saves in course folder structure
  4. Press ENTER → play next video

Usage:
  python play_and_save.py
"""
import os, sys, re, json, time, base64, subprocess, urllib.parse
import threading
import concurrent.futures
import requests
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH

BRIDGE = "http://127.0.0.1:8899"
API = "https://api.classplusapp.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WVD_PATH = os.path.join(SCRIPT_DIR, "device.wvd")
DOWNLOADS = os.path.join(SCRIPT_DIR, "downloads")
TEMP = os.path.join(SCRIPT_DIR, "temp")
INDEX_FILE = os.path.join(SCRIPT_DIR, "video_index.json")
TOOLS_DIR = os.path.join(SCRIPT_DIR, "CONNECT", "tools")
QUALITY = "720"
DEFAULT_COURSE = 45312

# Add local tools to PATH so mp4decrypt/ffmpeg are found
if os.path.isdir(TOOLS_DIR):
    os.environ["PATH"] = TOOLS_DIR + os.pathsep + os.environ.get("PATH", "")

# Track background pipeline threads
_bg_threads = []
_job_counter = 0
_active_jobs = set()
_jobs_lock = threading.Lock()


def _background_pipeline(job_id, tag, video_url, audio_url, keys, out_path, out_name,
                         vid_name, vid_duration, vid_folder, vid_key=None):
    """Download + decrypt + mux + cleanup, all in a background thread."""
    try:
        os.makedirs(TEMP, exist_ok=True)
        v_enc = os.path.join(TEMP, f"v_enc_{job_id}.mp4")
        a_enc = os.path.join(TEMP, f"a_enc_{job_id}.mp4")
        v_dec = os.path.join(TEMP, f"v_dec_{job_id}.mp4")
        a_dec = os.path.join(TEMP, f"a_dec_{job_id}.mp4")

        # --- Download ---
        t0 = time.time()
        v_total = _bg_download(video_url, v_enc, tag, "🎥")
        a_total = _bg_download(audio_url, a_enc, tag, "🔊")
        dl_time = time.time() - t0
        total_mb = (v_total + a_total) / (1024 * 1024)
        avg_speed = total_mb / dl_time if dl_time > 0 else 0
        print(f"  {tag} ⬇️  Done {total_mb:.0f} MB in {dl_time:.0f}s ({avg_speed:.1f} MB/s)", flush=True)

        # --- Decrypt ---
        ka = []
        for kid, key in keys.items():
            ka.extend(["--key", f"{kid}:{key}"])
        subprocess.run(["mp4decrypt"] + ka + [v_enc, v_dec], capture_output=True)
        subprocess.run(["mp4decrypt"] + ka + [a_enc, a_dec], capture_output=True)
        for f in [v_enc, a_enc]:
            try: os.remove(f)
            except: pass
        print(f"  {tag} 🔓 Decrypted", flush=True)

        # --- Mux ---
        print(f"  {tag} 🎞️  Muxing...", flush=True)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", v_dec, "-i", a_dec,
             "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart",
             out_path],
            capture_output=True
        )

        stderr = result.stderr.decode(errors='replace') if result.stderr else ''
        if result.returncode != 0:
            print(f"  {tag} ⚠️  ffmpeg exit code: {result.returncode}", flush=True)
            if stderr:
                # Show last few lines of error
                err_lines = [l for l in stderr.strip().split('\n') if l.strip()]
                for l in err_lines[-3:]:
                    print(f"  {tag}    {l.strip()}", flush=True)

        for f in [v_dec, a_dec]:
            try: os.remove(f)
            except: pass

        if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
            sz = os.path.getsize(out_path) / (1024 * 1024)
            info = vid_name if vid_name else out_name
            print(f"  {tag} ✅ SAVED {sz:.0f} MB — {info}", flush=True)
        else:
            print(f"  {tag} ❌ Mux failed — output missing or too small", flush=True)
    except Exception as e:
        print(f"  {tag} ❌ Error: {e}", flush=True)
    finally:
        if vid_key:
            with _jobs_lock:
                _active_jobs.discard(vid_key)

_internet_lock = threading.Lock()
_internet_down = False

def wait_for_internet(tag=""):
    """Check if internet is available, pause and wait if disconnected (thread-safe)."""
    global _internet_down
    with _internet_lock:
        if _internet_down:
            # Another thread is already waiting, we just sleep and return when restored
            while _internet_down:
                time.sleep(2)
            return True
        
        # We are the first thread to detect it!
        try:
            requests.get("https://api.classplusapp.com", timeout=5)
            return True
        except:
            _internet_down = True
            sys.stdout.write(f"\n  {tag} 📡 Internet disconnected! Pausing download. Waiting for connection...\n")
            sys.stdout.flush()
            
    # Wait until connection is restored
    while True:
        try:
            requests.get("https://api.classplusapp.com", timeout=5)
            break
        except:
            time.sleep(5)
            
    with _internet_lock:
        _internet_down = False
        print(f"  {tag} 📡 Internet connection restored! Resuming...", flush=True)
    return True


def _download_single(url, path, tag, label):
    """Fallback single-threaded download with retries, internet pause, and resume support."""
    try:
        r_head = requests.head(url, timeout=15)
        total_size = int(r_head.headers.get("content-length", 0))
    except:
        total_size = 0
        
    if total_size > 0 and os.path.exists(path) and os.path.getsize(path) == total_size:
        print(f"  {tag} {label} Found fully downloaded file. Skipping download!", flush=True)
        return total_size

    max_attempts = 5
    wait_sec = 10
    attempt = 0
    while attempt < max_attempts:
        current_size = 0
        if os.path.exists(path):
            current_size = os.path.getsize(path)
            if total_size > 0 and current_size > total_size:
                try: os.remove(path)
                except: pass
                current_size = 0
                
        headers = {}
        if current_size > 0:
            headers["Range"] = f"bytes={current_size}-"
            mode = "ab"
        else:
            mode = "wb"
            
        try:
            r = requests.get(url, headers=headers, stream=True, timeout=120)
            if r.status_code not in [200, 206]:
                raise Exception(f"Bad status code: {r.status_code}")
            
            if total_size == 0:
                total_size = int(r.headers.get("content-length", 0)) + current_size
                
            total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
            downloaded = current_size
            t0 = time.time()
            if current_size > 0:
                t0 = time.time() - (current_size / (2 * 1024 * 1024))
            last_log = time.time()

            if current_size > 0:
                print(f"  {tag} {label} Resuming single-threaded from {current_size/(1024*1024):.1f} MB...", flush=True)
            else:
                print(f"  {tag} {label} Starting single-threaded ({total_mb:.0f} MB)...", flush=True)

            with open(path, mode) as f:
                for chunk in r.iter_content(1024 * 256):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_log >= 30:
                        last_log = now
                        elapsed = now - t0
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        done_mb = downloaded / (1024 * 1024)
                        if total_size > 0:
                            pct = downloaded * 100 / total_size
                            print(f"  {tag} {label} {pct:.0f}% ({done_mb:.0f}/{total_mb:.0f} MB) {speed/(1024*1024):.1f} MB/s", flush=True)
                        else:
                            print(f"  {tag} {label} {done_mb:.0f} MB {speed/(1024*1024):.1f} MB/s", flush=True)
            return downloaded
        except Exception as e:
            is_down = True
            try:
                requests.get("https://api.classplusapp.com", timeout=3)
                is_down = False
            except:
                pass
            
            if is_down:
                wait_for_internet(tag)
                continue
            
            attempt += 1
            if attempt < max_attempts:
                print(f"  {tag} ⚠️ Download attempt {attempt} failed ({e}). Retrying in {wait_sec}s...", flush=True)
                time.sleep(wait_sec)
            else:
                print(f"  {tag} ❌ Fallback download error: {e}", flush=True)
                raise e


def _bg_download(url, path, tag, label):
    """Download with 32 parallel threads, with automatic single-threaded fallback, part-level retries, and full resume support."""
    try:
        r = requests.head(url, timeout=15)
        total_size = int(r.headers.get("content-length", 0))
        accept_ranges = r.headers.get("accept-ranges", "bytes")
    except:
        try:
            r = requests.get(url, stream=True, timeout=15)
            total_size = int(r.headers.get("content-length", 0))
            accept_ranges = r.headers.get("accept-ranges", "bytes")
            r.close()
        except:
            total_size = 0
            accept_ranges = "none"

    if os.path.exists(path) and os.path.getsize(path) == total_size and total_size > 0:
        print(f"  {tag} {label} Found fully downloaded & assembled file. Skipping download!", flush=True)
        return total_size

    num_threads = 32
    if total_size < 5 * 1024 * 1024 or accept_ranges == "none" or total_size == 0:
        return _download_single(url, path, tag, label)

    total_mb = total_size / (1024 * 1024)
    print(f"  {tag} {label} Starting multi-threaded ({total_mb:.0f} MB, 32 threads)...", flush=True)

    part_size = total_size // num_threads
    ranges = []
    for i in range(num_threads):
        start = i * part_size
        end = (i + 1) * part_size - 1 if i < num_threads - 1 else total_size - 1
        ranges.append((start, end, i))

    parts_paths = [f"{path}.part_{i}" for i in range(num_threads)]
    downloaded_bytes = [0] * num_threads
    t0 = time.time()
    last_log = t0

    def download_part(item):
        start, end, idx = item
        part_path = parts_paths[idx]
        expected_size = end - start + 1
        max_part_attempts = 5
        part_wait = 10
        
        attempt = 0
        while attempt < max_part_attempts:
            current_size = 0
            if os.path.exists(part_path):
                current_size = os.path.getsize(part_path)
                if current_size == expected_size:
                    downloaded_bytes[idx] = expected_size
                    return True
                elif current_size > expected_size:
                    try: os.remove(part_path)
                    except: pass
                    current_size = 0
            
            downloaded_bytes[idx] = current_size
            req_start = start + current_size
            headers = {"Range": f"bytes={req_start}-{end}"}
            mode = "ab" if current_size > 0 else "wb"
            
            try:
                res = requests.get(url, headers=headers, stream=True, timeout=60)
                if res.status_code not in [200, 206]:
                    raise Exception(f"Bad status code: {res.status_code}")
                with open(part_path, mode) as f:
                    for chunk in res.iter_content(1024 * 64):
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes[idx] += len(chunk)
                return True
            except Exception as e:
                is_down = True
                try:
                    requests.get("https://api.classplusapp.com", timeout=3)
                    is_down = False
                except:
                    pass
                
                if is_down:
                    wait_for_internet(tag)
                    continue
                
                attempt += 1
                if attempt < max_part_attempts:
                    time.sleep(part_wait)
                else:
                    raise e

    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {executor.submit(download_part, rng): rng for rng in ranges}
        
        while futures:
            done, not_done = concurrent.futures.wait(futures.keys(), timeout=2)
            now = time.time()
            if now - last_log >= 30:
                last_log = now
                current_downloaded = sum(downloaded_bytes)
                elapsed = now - t0
                speed = current_downloaded / elapsed if elapsed > 0 else 0
                done_mb = current_downloaded / (1024 * 1024)
                pct = current_downloaded * 100 / total_size
                print(f"  {tag} {label} {pct:.0f}% ({done_mb:.0f}/{total_mb:.0f} MB) {speed/(1024*1024):.1f} MB/s", flush=True)
            
            for fut in done:
                try:
                    fut.result()
                except Exception as e:
                    failed = True
                    break
            
            if failed:
                for fut in futures:
                    fut.cancel()
                break
                
            futures = {f: ranges for f, ranges in futures.items() if not f.done()}

    if failed:
        print(f"  {tag} ⚠️ Multi-threaded download failed. Falling back to single-threaded...", flush=True)
        # Keep the part files for fallback resume if possible!
        # Wait, if we keep them, _download_single won't use them (since it writes to path directly),
        # but let's delete them to avoid confusing fallback
        for p in parts_paths:
            try: os.remove(p)
            except: pass
        return _download_single(url, path, tag, label)

    # Concatenate all parts
    with open(path, "wb") as outfile:
        for part_path in parts_paths:
            if os.path.exists(part_path):
                with open(part_path, "rb") as infile:
                    while True:
                        chunk = infile.read(1024 * 256)
                        if not chunk:
                            break
                        outfile.write(chunk)
                try: os.remove(part_path)
                except: pass

    return total_size



def api_headers(token):
    return {
        "x-access-token": token, "User-Agent": "Mobile-Android",
        "App-Version": "1.12.1.2", "Api-Version": "56",
        "Device-Id": "4b7ed5ff3b8cadf8", "Device-Details": "samsung_SM-M346B_SDK-36",
        "region": "IN", "Content-Type": "application/json",
        "Build-Number": "56", "is-apk": "0",
    }


# ==========================================
# VIDEO INDEX: vidKey → {name, duration, path}
# ==========================================
def safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name).strip().rstrip(". ")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:120]


def build_index(token):
    """Walk entire course tree. Returns {vidKey: {name, duration, path}}."""
    index = {}
    queue = [(None, "")]  # (folder_id, path)
    scanned = 0
    while queue:
        folder_id, path = queue.pop(0)
        url = f"{API}/v2/course/content/get?courseId={DEFAULT_COURSE}&storeContentEvent=false"
        if folder_id:
            url += f"&folderId={folder_id}"
        try:
            r = requests.get(url, headers=api_headers(token), timeout=10)
            items = r.json().get("data", {}).get("courseContent", [])
            for item in items:
                ct = item.get("contentType")
                name = item.get("name", "untitled")
                sn = safe_name(name)
                if ct == 1:  # folder
                    folder_path = os.path.join(path, sn) if path else sn
                    queue.append((item.get("id"), folder_path))
                elif item.get("vidKey"):
                    index[item["vidKey"]] = {
                        "name": name,
                        "duration": item.get("duration", ""),
                        "path": path,
                    }
            scanned += 1
            sys.stdout.write(f"\r  📚 Scanning folders... {scanned} done, {len(queue)} left, {len(index)} videos  ")
            sys.stdout.flush()
        except:
            pass
    print(f"\r  ✅ Indexed {len(index)} videos in {scanned} folders                     ")
    return index


def load_or_build_index(token):
    """Load index from cache file, or build + save it."""
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                index = json.load(f)
            print(f"  ✅ Loaded {len(index)} videos from cache")
            return index
        except:
            pass
    print("  📚 First run — scanning course folders (one time only)...")
    index = build_index(token)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"  💾 Saved index to {INDEX_FILE}")
    return index


def lookup(index, mpd_url):
    """Find video info from MPD URL."""
    m = re.search(r"/drm/wv/([a-f0-9]+)/", mpd_url)
    if not m:
        return None, None, None, None
    vid_key = m.group(1)
    info = index.get(vid_key)
    if info:
        return info["name"], info["duration"], info["path"], vid_key
    return None, None, None, vid_key


def download_with_progress(url, path, label):
    r = requests.get(url, stream=True, timeout=120)
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    t0 = time.time()
    with open(path, "wb") as f:
        for chunk in r.iter_content(1024 * 256):
            f.write(chunk)
            downloaded += len(chunk)
            elapsed = time.time() - t0
            speed = downloaded / elapsed if elapsed > 0 else 0
            speed_mb = speed / (1024 * 1024)
            done_mb = downloaded / (1024 * 1024)
            if total_size > 0:
                pct = downloaded * 100 / total_size
                total_mb = total_size / (1024 * 1024)
                eta = (total_size - downloaded) / speed if speed > 0 else 0
                bar_len = 20
                filled = int(bar_len * downloaded / total_size)
                bar = "█" * filled + "░" * (bar_len - filled)
                sys.stdout.write(f"\r    {label} {bar} {pct:5.1f}% {done_mb:.1f}/{total_mb:.1f}MB {speed_mb:.1f}MB/s ETA:{int(eta)}s  ")
            else:
                sys.stdout.write(f"\r    {label} {done_mb:.1f}MB {speed_mb:.1f}MB/s  ")
            sys.stdout.flush()
    elapsed = time.time() - t0
    final_mb = downloaded / (1024 * 1024)
    avg_speed = (downloaded / elapsed / (1024 * 1024)) if elapsed > 0 else 0
    print(f"\r    {label} ✅ {final_mb:.1f} MB in {elapsed:.0f}s ({avg_speed:.1f} MB/s)                     ")
    return downloaded


def main():
    os.makedirs(DOWNLOADS, exist_ok=True)
    os.makedirs(TEMP, exist_ok=True)

    print("=" * 60)
    print("  🎬 Play & Save — Classplus Video Downloader")
    print("=" * 60)

    # Check bridge
    try:
        requests.get(f"{BRIDGE}/health", timeout=3)
        print("  ✅ Bridge OK")
    except:
        print("  ❌ Bridge not running!"); sys.exit(1)

    token = requests.get(f"{BRIDGE}/token", timeout=5).json()["token"]
    print("  ✅ Auth token OK")

    # Load or build video index (cached after first run)
    index = load_or_build_index(token)

    device = Device.load(WVD_PATH)
    print("  ✅ WVD loaded")
    print(f"  📂 Saves to: {DOWNLOADS}")

    video_count = 0
    first_run = True

    while True:
        try:
            # Clear old state
            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            requests.get(f"{BRIDGE}/clear", timeout=5)

            print("\n" + "=" * 60)
            if first_run:
                print("  👉 Play a video in the app, then come back here.")
                first_run = False
            else:
                print(f"  ✅ {video_count} videos queued so far")
                print("  👉 Play the next video in the app!")
            print("=" * 60)
            print("  ⏳ Waiting for video...")

            # Wait for new MPD
            mpd_url = None
            while not mpd_url:
                time.sleep(2)
                try:
                    cap = requests.get(f"{BRIDGE}/captured-urls", timeout=5).json()
                    for entry in cap.get("urls", []):
                        u = entry.get("url", "")
                        if ".mpd" in u:
                            mpd_url = u
                            break
                except:
                    pass

            # Look up video info
            vid_name, vid_duration, vid_folder, vid_key = lookup(index, mpd_url)
            if vid_key:
                with _jobs_lock:
                    if vid_key in _active_jobs:
                        try: requests.get(f"{BRIDGE}/clear", timeout=5)
                        except: pass
                        sys.stdout.write(f"\r  ⏭️  Skipping duplicate detection for already active job: {vid_name or vid_key}\n")
                        sys.stdout.flush()
                        continue
                    _active_jobs.add(vid_key)
            sn = safe_name(vid_name) if vid_name else None

            print(f"\n  🎬 Video detected!")
            if vid_name:
                print(f"    📌 {vid_name}")
                print(f"    ⏱️  {vid_duration or '?'}")
                if vid_folder:
                    print(f"    📁 {vid_folder}")
            else:
                print(f"    📌 ID: {vid_key or 'unknown'}")

            # Parse MPD
            mpd_text = requests.get(mpd_url, timeout=15).text
            pssh_m = re.search(r"<cenc:pssh>(.*?)</cenc:pssh>", mpd_text)
            if not pssh_m:
                print("    ❌ No PSSH"); continue
            pssh_b64 = pssh_m.group(1)

            base_urls = re.findall(r"<BaseURL>(.*?)</BaseURL>", mpd_text)
            base = mpd_url.rsplit("/", 1)[0] + "/"
            qs = "?" + mpd_url.split("?", 1)[1] if "?" in mpd_url else ""

            video_url = audio_url = None
            for u in base_urls:
                if "audio" in u.lower(): audio_url = base + u + qs
                if QUALITY in u: video_url = base + u + qs
            if not video_url:
                for q in ["720", "480", "360"]:
                    for u in base_urls:
                        if q in u and "audio" not in u.lower():
                            video_url = base + u + qs; break
                    if video_url: break
            if not video_url:
                for u in base_urls:
                    if "audio" not in u.lower():
                        video_url = base + u + qs; break
            if not video_url or not audio_url:
                print(f"    ❌ Missing tracks"); continue

            sel_q = [u for u in base_urls if "audio" not in u.lower()]
            print(f"    ✅ {sel_q[0] if sel_q else '?'} + audio")

            # Set challenge
            cdm = Cdm.from_device(device)
            sid = cdm.open()
            challenge = cdm.get_license_challenge(sid, PSSH(pssh_b64))
            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            time.sleep(0.3)
            requests.post(f"{BRIDGE}/set-challenge",
                          json={"challenge": base64.b64encode(challenge).decode()},
                          headers={"Content-Type": "application/json"}, timeout=10)

            print(f"    🔑 Challenge ready")
            print(f"\n  ⚡ NOW REPLAY THE SAME VIDEO!")
            print(f"     Waiting for license...")

            # Wait for license
            keys = None
            for i in range(120):
                time.sleep(1)
                if i % 10 == 9:
                    sys.stdout.write(f"\r    ⏳ {i+1}s...")
                    sys.stdout.flush()
                try:
                    lr = requests.get(f"{BRIDGE}/license-response", timeout=5).json()
                    if lr.get("status") == "captured" and lr.get("license"):
                        lic_data = base64.b64decode(lr["license"])
                        try:
                            cdm.parse_license(sid, lic_data)
                            keys = {}
                            for k in cdm.get_keys(sid):
                                if len(k.key.hex()) == 32:
                                    keys[k.kid.hex] = k.key.hex()
                            if keys:
                                print(f"\r    ✅ {len(keys)} keys captured!     ")
                                break
                        except:
                            requests.get(f"{BRIDGE}/clear-license", timeout=5)
                            cdm.close(sid)
                            cdm = Cdm.from_device(device)
                            sid = cdm.open()
                            ch = cdm.get_license_challenge(sid, PSSH(pssh_b64))
                            requests.post(f"{BRIDGE}/set-challenge",
                                          json={"challenge": base64.b64encode(ch).decode()},
                                          headers={"Content-Type": "application/json"}, timeout=10)
                            print(f"\r    ⚠️  Stale license, play again!     ")
                except requests.exceptions.RequestException:
                    pass

            cdm.close(sid)
            if not keys:
                print(f"\r    ❌ Timeout. Try again.     "); continue

            # Build output path
            if vid_folder:
                out_dir = os.path.join(DOWNLOADS, vid_folder)
            else:
                out_dir = DOWNLOADS
            os.makedirs(out_dir, exist_ok=True)

            if sn:
                out_name = f"{sn}.mp4"
            else:
                out_name = f"{time.strftime('%H%M%S')}_{vid_key[:12] if vid_key else 'video'}.mp4"
            out_path = os.path.join(out_dir, out_name)

            # Launch entire pipeline in background
            global _job_counter
            _job_counter += 1
            job_id = _job_counter
            video_count += 1
            tag = f"[#{job_id} {sn[:30] if sn else vid_key[:12]}]"
            print(f"    🚀 Queued as job #{job_id} — downloading in background")
            t = threading.Thread(
                target=_background_pipeline,
                args=(job_id, tag, video_url, audio_url, keys, out_path, out_name,
                      vid_name, vid_duration, vid_folder, vid_key),
                daemon=True
            )
            t.start()
            _bg_threads.append(t)

            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            print(f"    ⏩ Play next video now! ({video_count} queued)")

        except KeyboardInterrupt:
            # Wait for any background jobs to finish before exiting
            pending = [t for t in _bg_threads if t.is_alive()]
            if pending:
                print(f"\n  ⏳ Waiting for {len(pending)} background job(s) to finish...")
                for t in pending:
                    t.join()
            print(f"\n\n  Stopped. {video_count} videos saved to {DOWNLOADS}")
            break
        except Exception as e:
            print(f"  Error: {e}")
            import traceback; traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
