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
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()[:120]


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
                print(f"  ✅ {video_count} videos saved so far")
                print("  👉 Press ENTER when ready for next video...")
                input()
                requests.get(f"{BRIDGE}/clear", timeout=5)
                requests.get(f"{BRIDGE}/clear-license", timeout=5)
                print("  👉 Now play the video in the app!")
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

            # Download
            v_enc = os.path.join(TEMP, "v_enc.mp4")
            a_enc = os.path.join(TEMP, "a_enc.mp4")
            v_total = download_with_progress(video_url, v_enc, "🎥 Video")
            a_total = download_with_progress(audio_url, a_enc, "🔊 Audio")
            print(f"    📦 Total: {(v_total+a_total)/(1024*1024):.1f} MB")

            # Decrypt
            v_dec = os.path.join(TEMP, "v_dec.mp4")
            a_dec = os.path.join(TEMP, "a_dec.mp4")
            ka = []
            for kid, key in keys.items():
                ka.extend(["--key", f"{kid}:{key}"])
            print(f"    🔓 Decrypting...")
            subprocess.run(["mp4decrypt"] + ka + [v_enc, v_dec], capture_output=True)
            subprocess.run(["mp4decrypt"] + ka + [a_enc, a_dec], capture_output=True)

            # Build output path with folder structure
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

            print(f"    🎞️  Muxing → {out_name}")
            subprocess.run(["ffmpeg", "-y", "-i", v_dec, "-i", a_dec,
                             "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart",
                             out_path], capture_output=True)

            # Cleanup
            for f in [v_enc, a_enc, v_dec, a_dec]:
                try: os.remove(f)
                except: pass
            requests.get(f"{BRIDGE}/clear-license", timeout=5)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
                sz = os.path.getsize(out_path) / (1024 * 1024)
                video_count += 1
                print(f"\n  ✅✅✅ SAVED!")
                if vid_name:
                    print(f"  📌 {vid_name} ({vid_duration or '?'})")
                if vid_folder:
                    print(f"  📁 {vid_folder}")
                print(f"  📊 {sz:.1f} MB | Total: {video_count}")
                print(f"  📂 {out_path}")
            else:
                print(f"    ❌ Mux failed")

        except KeyboardInterrupt:
            print(f"\n\n  Stopped. {video_count} videos saved to {DOWNLOADS}")
            break
        except Exception as e:
            print(f"  Error: {e}")
            import traceback; traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
