#!/usr/bin/env python3
"""
Dashboard Backend — Web UI for managing video downloads.

Serves dashboard.html and provides API endpoints for:
- Listing captured videos with download status
- Queuing downloads
- Tracking progress
- Pause/resume/cancel

Usage:
  python dashboard.py
  Then open http://localhost:8080 in your browser
"""
import os, sys, re, json, time, base64, subprocess, threading, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests as req_lib

BRIDGE = "http://127.0.0.1:8899"
API = "https://api.classplusapp.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WVD_PATH = os.path.join(SCRIPT_DIR, "device.wvd")
DOWNLOADS = os.path.join(SCRIPT_DIR, "downloads")
TEMP = os.path.join(SCRIPT_DIR, "temp")
KEYS_DB = os.path.join(SCRIPT_DIR, "keys_db.json")
INDEX_FILE = os.path.join(SCRIPT_DIR, "video_index.json")
TOOLS_DIR = os.path.join(SCRIPT_DIR, "CONNECT", "tools")
DASHBOARD_HTML = os.path.join(SCRIPT_DIR, "dashboard.html")
DEFAULT_COURSE = 45312
PORT = 8080

# Add local tools to PATH
if os.path.isdir(TOOLS_DIR):
    os.environ["PATH"] = TOOLS_DIR + os.pathsep + os.environ.get("PATH", "")

os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(TEMP, exist_ok=True)


# ==========================================
# Download Manager
# ==========================================
class DownloadManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = []          # list of vid_keys to download
        self.active = {}         # vid_key → {status, progress, speed, downloaded_mb, total_mb, thread}
        self.paused = set()      # vid_keys that are paused
        self.cancel_flags = {}   # vid_key → threading.Event
        self.max_concurrent = 2
        self.worker_thread = None
        self.token = None

    def get_token(self):
        """Get fresh auth token from bridge."""
        try:
            r = req_lib.get(f"{BRIDGE}/token", timeout=5).json()
            if r.get("success"):
                self.token = r["token"]
                return self.token
        except:
            pass
        return self.token

    def api_headers(self):
        return {
            "x-access-token": self.token or self.get_token(),
            "User-Agent": "Mobile-Android",
            "App-Version": "1.12.1.2", "Api-Version": "56",
            "Device-Id": "4b7ed5ff3b8cadf8", "Device-Details": "samsung_SM-M346B_SDK-36",
            "region": "IN", "Content-Type": "application/json",
            "Build-Number": "56", "is-apk": "0",
        }

    def get_video_urls(self, vid_key):
        """Get fresh MPD URL for a video using its encrypted content ID."""
        # Look up content ID from video index
        index = load_index()
        info = index.get(vid_key, {})

        # Try to find the encrypted content ID by looking through course content
        token = self.get_token()
        if not token:
            return None, None, None

        # Search course content for this vidKey to get encrypted contentId
        folders = [(None, "")]
        while folders:
            fid, path = folders.pop(0)
            url = f"{API}/v2/course/content/get?courseId={DEFAULT_COURSE}&storeContentEvent=false"
            if fid:
                url += f"&folderId={fid}"
            try:
                r = req_lib.get(url, headers=self.api_headers(), timeout=10)
                items = r.json().get("data", {}).get("courseContent", [])
                for item in items:
                    if item.get("contentType") == 1:
                        folders.append((item.get("id"), ""))
                    elif item.get("vidKey") == vid_key:
                        enc_id = item.get("encryptedContentId", item.get("contentId"))
                        if enc_id:
                            return self._get_drm_urls(enc_id)
            except:
                pass
        return None, None, None

    def _get_drm_urls(self, enc_id):
        """Get MPD URL from encrypted content ID."""
        enc = urllib.parse.quote(str(enc_id))
        url = f"{API}/cams/uploader/video/jw-signed-url?contentId={enc}&offlineDownload=false"
        try:
            r = req_lib.get(url, headers=self.api_headers(), timeout=15).json()
            mpd_url = r.get("drmUrls", {}).get("manifestUrl")
            if mpd_url:
                return mpd_url, None, None
        except:
            pass
        return None, None, None

    def get_tracks_from_mpd(self, mpd_url, quality="720"):
        """Get video and audio track URLs from MPD."""
        try:
            mpd_text = req_lib.get(mpd_url, timeout=15).text
            base_urls = re.findall(r"<BaseURL>(.*?)</BaseURL>", mpd_text)
            base = mpd_url.rsplit("/", 1)[0] + "/"
            qs = "?" + mpd_url.split("?", 1)[1] if "?" in mpd_url else ""

            video_url = audio_url = None
            for u in base_urls:
                if "audio" in u.lower(): audio_url = base + u + qs
                if quality in u: video_url = base + u + qs
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
            return video_url, audio_url
        except:
            return None, None

    def enqueue(self, vid_keys):
        """Add videos to download queue."""
        keys_db = load_keys_db()
        added = 0
        with self.lock:
            for vk in vid_keys:
                if vk in keys_db and vk not in self.queue and vk not in self.active:
                    # Check if already downloaded
                    if not self._is_downloaded(vk, keys_db[vk]):
                        self.queue.append(vk)
                        added += 1
        self._ensure_worker()
        return added

    def pause(self, vid_key):
        with self.lock:
            if vid_key in self.active:
                self.paused.add(vid_key)
                if vid_key in self.cancel_flags:
                    self.cancel_flags[vid_key].set()

    def resume(self, vid_key):
        with self.lock:
            self.paused.discard(vid_key)
            if vid_key not in self.queue and vid_key not in self.active:
                self.queue.append(vid_key)
        self._ensure_worker()

    def cancel(self, vid_key):
        with self.lock:
            if vid_key in self.queue:
                self.queue.remove(vid_key)
            if vid_key in self.cancel_flags:
                self.cancel_flags[vid_key].set()
            self.paused.discard(vid_key)

    def _is_downloaded(self, vid_key, info):
        """Check if video file already exists."""
        name = re.sub(r'[<>:"/\\|?*]', '', info.get("name", "")).strip().rstrip(". ")[:120]
        if not name: name = f"video_{vid_key[:12]}"
        folder = info.get("folder", "")
        out_dir = os.path.join(DOWNLOADS, folder) if folder else DOWNLOADS
        out_path = os.path.join(out_dir, f"{name}.mp4")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 10000

    def get_status(self):
        """Get all video statuses for the API."""
        keys_db = load_keys_db()
        result = []
        for vid_key, info in keys_db.items():
            entry = {
                "vid_key": vid_key,
                "name": info.get("name", "Unknown"),
                "duration": info.get("duration", ""),
                "folder": info.get("folder", ""),
                "captured_at": info.get("captured_at", ""),
                "keys_count": len(info.get("keys", {})),
            }

            with self.lock:
                if vid_key in self.active:
                    a = self.active[vid_key]
                    if vid_key in self.paused:
                        entry["status"] = "paused"
                    else:
                        entry["status"] = "downloading"
                    entry["progress"] = a.get("progress", 0)
                    entry["speed"] = a.get("speed", "")
                    entry["downloaded_mb"] = a.get("downloaded_mb", 0)
                    entry["total_mb"] = a.get("total_mb", 0)
                elif vid_key in self.queue:
                    entry["status"] = "queued"
                    entry["progress"] = 0
                elif self._is_downloaded(vid_key, info):
                    entry["status"] = "downloaded"
                    entry["progress"] = 100
                else:
                    entry["status"] = "captured"
                    entry["progress"] = 0

            result.append(entry)
        return result

    def _ensure_worker(self):
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker_thread.start()

    def _worker_loop(self):
        while True:
            vid_key = None
            with self.lock:
                # Find next non-paused item in queue
                for vk in list(self.queue):
                    if vk not in self.paused:
                        vid_key = vk
                        self.queue.remove(vk)
                        break
            if not vid_key:
                time.sleep(1)
                with self.lock:
                    if not self.queue:
                        return
                continue

            self._download_video(vid_key)

    def _download_video(self, vid_key):
        """Download a single video."""
        keys_db = load_keys_db()
        info = keys_db.get(vid_key, {})
        keys = info.get("keys", {})
        name = re.sub(r'[<>:"/\\|?*]', '', info.get("name", "")).strip().rstrip(". ")[:120]
        if not name: name = f"video_{vid_key[:12]}"
        folder = info.get("folder", "")

        cancel_event = threading.Event()
        with self.lock:
            self.active[vid_key] = {
                "status": "downloading", "progress": 0,
                "speed": "", "downloaded_mb": 0, "total_mb": 0,
                "phase": "getting URLs..."
            }
            self.cancel_flags[vid_key] = cancel_event

        try:
            # Get fresh MPD URL
            with self.lock:
                self.active[vid_key]["phase"] = "fetching video URL..."

            mpd_url, _, _ = self.get_video_urls(vid_key)
            if not mpd_url:
                with self.lock:
                    self.active[vid_key]["phase"] = "❌ Could not get video URL"
                time.sleep(3)
                return

            if cancel_event.is_set(): return

            video_url, audio_url = self.get_tracks_from_mpd(mpd_url)
            if not video_url or not audio_url:
                with self.lock:
                    self.active[vid_key]["phase"] = "❌ Missing tracks"
                time.sleep(3)
                return

            if cancel_event.is_set(): return

            # Download
            v_enc = os.path.join(TEMP, f"v_{vid_key[:12]}.mp4")
            a_enc = os.path.join(TEMP, f"a_{vid_key[:12]}.mp4")

            with self.lock:
                self.active[vid_key]["phase"] = "downloading video..."

            v_size = self._download_track(vid_key, video_url, v_enc, cancel_event, "video")
            if cancel_event.is_set(): return

            with self.lock:
                self.active[vid_key]["phase"] = "downloading audio..."
                self.active[vid_key]["progress"] = 70

            a_size = self._download_track(vid_key, audio_url, a_enc, cancel_event, "audio")
            if cancel_event.is_set(): return

            # Decrypt
            with self.lock:
                self.active[vid_key]["phase"] = "decrypting..."
                self.active[vid_key]["progress"] = 85
                self.active[vid_key]["speed"] = ""

            v_dec = os.path.join(TEMP, f"vd_{vid_key[:12]}.mp4")
            a_dec = os.path.join(TEMP, f"ad_{vid_key[:12]}.mp4")
            ka = []
            for kid, key in keys.items():
                ka.extend(["--key", f"{kid}:{key}"])
            subprocess.run(["mp4decrypt"] + ka + [v_enc, v_dec], capture_output=True)
            subprocess.run(["mp4decrypt"] + ka + [a_enc, a_dec], capture_output=True)

            if cancel_event.is_set(): return

            # Mux
            with self.lock:
                self.active[vid_key]["phase"] = "muxing..."
                self.active[vid_key]["progress"] = 92

            out_dir = os.path.join(DOWNLOADS, folder) if folder else DOWNLOADS
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{name}.mp4")

            subprocess.run(["ffmpeg", "-y", "-i", v_dec, "-i", a_dec,
                             "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart",
                             out_path], capture_output=True)

            # Cleanup temp
            for f in [v_enc, a_enc, v_dec, a_dec]:
                try: os.remove(f)
                except: pass

            if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
                sz = os.path.getsize(out_path) / (1024 * 1024)
                with self.lock:
                    self.active[vid_key]["phase"] = f"✅ Done! {sz:.0f} MB"
                    self.active[vid_key]["progress"] = 100
                print(f"  ✅ Downloaded: {name} ({sz:.0f} MB)")
            else:
                with self.lock:
                    self.active[vid_key]["phase"] = "❌ Mux failed"
                print(f"  ❌ Failed: {name}")

        except Exception as e:
            with self.lock:
                self.active[vid_key]["phase"] = f"❌ Error: {str(e)[:50]}"
            print(f"  ❌ Error downloading {name}: {e}")
        finally:
            time.sleep(2)
            with self.lock:
                self.active.pop(vid_key, None)
                self.cancel_flags.pop(vid_key, None)
                self.paused.discard(vid_key)

    def _download_track(self, vid_key, url, path, cancel_event, track_type):
        """Download a single track with progress updates."""
        r = req_lib.get(url, stream=True, timeout=120)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        t0 = time.time()
        total_mb = total / (1024 * 1024) if total > 0 else 0

        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                if cancel_event.is_set():
                    return downloaded
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - t0
                speed = downloaded / elapsed if elapsed > 0 else 0
                speed_mb = speed / (1024 * 1024)
                done_mb = downloaded / (1024 * 1024)

                if total > 0:
                    if track_type == "video":
                        pct = (downloaded / total) * 65  # video = 0-65%
                    else:
                        pct = 65 + (downloaded / total) * 20  # audio = 65-85%
                else:
                    pct = 50

                with self.lock:
                    if vid_key in self.active:
                        self.active[vid_key]["progress"] = round(pct, 1)
                        self.active[vid_key]["speed"] = f"{speed_mb:.1f} MB/s"
                        self.active[vid_key]["downloaded_mb"] = round(done_mb, 1)
                        self.active[vid_key]["total_mb"] = round(total_mb, 1)
        return downloaded


# ==========================================
# Helpers
# ==========================================
def load_keys_db():
    if os.path.exists(KEYS_DB):
        with open(KEYS_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ==========================================
# HTTP Handler
# ==========================================
dm = DownloadManager()


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress access logs

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return json.loads(body) if body else {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/videos":
            self._send_json(dm.get_status())
        elif self.path == "/api/stats":
            videos = dm.get_status()
            self._send_json({
                "total": len(videos),
                "captured": sum(1 for v in videos if v["status"] == "captured"),
                "downloaded": sum(1 for v in videos if v["status"] == "downloaded"),
                "downloading": sum(1 for v in videos if v["status"] in ("downloading", "queued")),
                "paused": sum(1 for v in videos if v["status"] == "paused"),
            })
        else:
            self.send_error(404)

    def do_POST(self):
        data = self._read_json()
        if self.path == "/api/download":
            vid_keys = data.get("vid_keys", [])
            added = dm.enqueue(vid_keys)
            self._send_json({"success": True, "queued": added})
        elif self.path == "/api/download-all":
            keys_db = load_keys_db()
            all_keys = list(keys_db.keys())
            added = dm.enqueue(all_keys)
            self._send_json({"success": True, "queued": added})
        elif self.path == "/api/pause":
            dm.pause(data.get("vid_key", ""))
            self._send_json({"success": True})
        elif self.path == "/api/resume":
            dm.resume(data.get("vid_key", ""))
            self._send_json({"success": True})
        elif self.path == "/api/cancel":
            dm.cancel(data.get("vid_key", ""))
            self._send_json({"success": True})
        else:
            self.send_error(404)

    def _serve_html(self):
        if not os.path.exists(DASHBOARD_HTML):
            self.send_error(404, "dashboard.html not found")
            return
        with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
            html = f.read().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.end_headers()
        self.wfile.write(html)


def main():
    print("=" * 60)
    print("  📊 Classplus Download Dashboard")
    print("=" * 60)

    keys_db = load_keys_db()
    if not keys_db:
        print("  ⚠️  No keys captured yet!")
        print("  Run capture_keys.py first to capture video keys.")
        print("  Starting anyway — dashboard will show empty...")

    print(f"  🔑 {len(keys_db)} videos in keys database")

    # Check bridge for token
    try:
        dm.get_token()
        print("  ✅ Bridge connected")
    except:
        print("  ⚠️  Bridge not running — downloads won't work until bridge is started")

    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"\n  🌐 Dashboard: http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")
    except:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
