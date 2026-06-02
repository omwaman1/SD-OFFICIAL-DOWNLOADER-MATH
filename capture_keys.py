#!/usr/bin/env python3
"""
Key Capture Mode — Rapidly capture DRM keys without downloading.

Flow:
  1. Play a video → keys captured → stored in keys_db.json
  2. Play next video immediately (no downloading!)
  3. Repeat until all videos are captured
  4. Then use dashboard.py to select & download

Usage:
  python capture_keys.py
"""
import os, sys, re, json, time, base64
import requests
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH

BRIDGE = "http://127.0.0.1:8899"
API = "https://api.classplusapp.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WVD_PATH = os.path.join(SCRIPT_DIR, "device.wvd")
INDEX_FILE = os.path.join(SCRIPT_DIR, "video_index.json")
KEYS_DB = os.path.join(SCRIPT_DIR, "keys_db.json")
DEFAULT_COURSE = 45312


def api_headers(token):
    return {
        "x-access-token": token, "User-Agent": "Mobile-Android",
        "App-Version": "1.12.1.2", "Api-Version": "56",
        "Device-Id": "4b7ed5ff3b8cadf8", "Device-Details": "samsung_SM-M346B_SDK-36",
        "region": "IN", "Content-Type": "application/json",
        "Build-Number": "56", "is-apk": "0",
    }


def safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name).strip().rstrip(". ")
    return cleaned[:120] if cleaned else "untitled"


def load_index(token):
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            index = json.load(f)
        print(f"  ✅ Loaded {len(index)} videos from index")
        return index
    # Build index
    print("  📚 Scanning course folders...")
    index = {}
    queue = [(None, "")]
    while queue:
        fid, path = queue.pop(0)
        url = f"{API}/v2/course/content/get?courseId={DEFAULT_COURSE}&storeContentEvent=false"
        if fid: url += f"&folderId={fid}"
        try:
            r = requests.get(url, headers=api_headers(token), timeout=10)
            for item in r.json().get("data", {}).get("courseContent", []):
                ct = item.get("contentType")
                name = item.get("name", "untitled")
                sn = safe_name(name)
                if ct == 1:
                    queue.append((item.get("id"), os.path.join(path, sn) if path else sn))
                elif item.get("vidKey"):
                    index[item["vidKey"]] = {
                        "name": name, "duration": item.get("duration", ""), "path": path
                    }
            sys.stdout.write(f"\r  📚 {len(index)} videos found...  ")
            sys.stdout.flush()
        except:
            pass
    print(f"\r  ✅ Indexed {len(index)} videos                ")
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return index


def load_keys_db():
    if os.path.exists(KEYS_DB):
        with open(KEYS_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_keys_db(db):
    with open(KEYS_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 60)
    print("  🔑 Key Capture Mode — No downloads, just keys!")
    print("=" * 60)

    # Check bridge
    try:
        requests.get(f"{BRIDGE}/health", timeout=3)
        print("  ✅ Bridge OK")
    except:
        print("  ❌ Bridge not running!"); sys.exit(1)

    token = requests.get(f"{BRIDGE}/token", timeout=5).json()["token"]
    print("  ✅ Auth token OK")

    index = load_index(token)
    keys_db = load_keys_db()
    device = Device.load(WVD_PATH)
    print("  ✅ WVD loaded")

    total = len(index)
    captured = len(keys_db)
    print(f"\n  📊 {captured}/{total} videos have keys captured")
    if captured > 0:
        remaining = total - captured
        print(f"  📊 {remaining} remaining")

    capture_count = 0

    while True:
        try:
            # Clear state
            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            requests.get(f"{BRIDGE}/clear", timeout=5)

            keys_db = load_keys_db()
            captured = len(keys_db)

            print(f"\n{'=' * 60}")
            print(f"  🔑 Keys: {captured}/{total} | This session: {capture_count}")
            print(f"  👉 Play a video in the app!")
            print(f"{'=' * 60}")
            print(f"  ⏳ Waiting for video...")

            # Wait for MPD
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

            # Find video info
            m = re.search(r"/drm/wv/([a-f0-9]+)/", mpd_url)
            vid_key = m.group(1) if m else None
            info = index.get(vid_key, {}) if vid_key else {}
            vid_name = info.get("name", f"Unknown ({vid_key[:12]})" if vid_key else "Unknown")
            vid_duration = info.get("duration", "?")
            vid_folder = info.get("path", "")

            print(f"\n  🎬 {vid_name}")
            print(f"     ⏱️  {vid_duration} | 📁 {vid_folder or 'root'}")

            # Check if already captured
            if vid_key and vid_key in keys_db:
                print(f"     ✅ Keys already captured! Skipping.")
                print(f"     ⏩ Play next video...")
                requests.get(f"{BRIDGE}/clear", timeout=5)
                continue

            # Get PSSH
            mpd_text = requests.get(mpd_url, timeout=15).text
            pssh_m = re.search(r"<cenc:pssh>(.*?)</cenc:pssh>", mpd_text)
            if not pssh_m:
                print("     ❌ No PSSH"); continue
            pssh_b64 = pssh_m.group(1)

            # Set challenge
            cdm = Cdm.from_device(device)
            sid = cdm.open()
            challenge = cdm.get_license_challenge(sid, PSSH(pssh_b64))
            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            time.sleep(0.3)
            requests.post(f"{BRIDGE}/set-challenge",
                          json={"challenge": base64.b64encode(challenge).decode()},
                          headers={"Content-Type": "application/json"}, timeout=10)

            print(f"     🔑 Challenge set — REPLAY THE VIDEO NOW!")

            # Wait for license
            keys = None
            for i in range(120):
                time.sleep(1)
                if i % 10 == 9:
                    sys.stdout.write(f"\r     ⏳ {i+1}s...")
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
                                print(f"\r     ✅ {len(keys)} keys captured!              ")
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
                            print(f"\r     ⚠️  Stale — play again!              ")
                except requests.exceptions.RequestException:
                    pass

            cdm.close(sid)

            if not keys:
                print(f"\r     ❌ Timeout. Try again.              ")
                if vid_key:
                    with open(f"{KEYS_DB}.failed", "a") as f:
                        f.write(f"{vid_key} | {vid_name}\n")
                continue

            # Save to keys_db
            keys_db[vid_key] = {
                "name": vid_name,
                "duration": vid_duration,
                "folder": vid_folder,
                "keys": keys,
                "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_keys_db(keys_db)
            capture_count += 1
            captured = len(keys_db)

            print(f"     💾 Saved! ({captured}/{total} total)")
            print(f"     ⏩ Play next video now!")

            # Clear for next
            requests.get(f"{BRIDGE}/clear-license", timeout=5)
            requests.get(f"{BRIDGE}/clear", timeout=5)

        except KeyboardInterrupt:
            print(f"\n\n  Done! {capture_count} keys captured this session.")
            print(f"  Total in database: {len(keys_db)}/{total}")
            print(f"  Run dashboard.py to download videos!")
            break
        except Exception as e:
            print(f"  Error: {e}")
            import traceback; traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
