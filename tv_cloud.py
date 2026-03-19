# ================================================================
#  HUYA → TV STREAMER (CLOUD) — tv_cloud.py  v1.0
#  Headless Linux version for ClawCloud / Docker
#  Gate page with 6-digit code sent to Telegram on startup
# ================================================================

import os
import subprocess
import threading
import time
import random
import signal
import sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import requests

# ================================================================
#  CONFIG — set these as environment variables in ClawCloud
# ================================================================

HUYA_URL        = os.environ.get("HUYA_URL", "https://m.huya.com/880214")
TG_TOKEN        = os.environ.get("TG_TOKEN",  "8230303716:AAH3bppU55xK4mTEmLh2gCFTc91SjqkvGUk")
TG_CHAT_ID      = os.environ.get("TG_CHAT_ID", "5406920859")
PORT            = int(os.environ.get("PORT", 8080))
HLS_DIR         = "/tmp/hls"
STREAM_NAME     = "best"

# ================================================================
#  STATE
# ================================================================

access_code     = str(random.randint(100000, 999999))
unlocked_ips    = set()
sl_proc         = None
ff_proc         = None
running         = True
proc_lock       = threading.Lock()

# ================================================================
#  TELEGRAM
# ================================================================

def tg_send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"[TG] Failed: {e}")

# ================================================================
#  HELPERS
# ================================================================

def fmt_hms(seconds):
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}h {m:02d}m {s:02d}s"

def cleanup_hls():
    try:
        for f in os.listdir(HLS_DIR):
            if f.startswith("live"):
                try:
                    os.remove(os.path.join(HLS_DIR, f))
                except:
                    pass
    except:
        pass

# ================================================================
#  STREAM
# ================================================================

def start_stream():
    global sl_proc, ff_proc
    cleanup_hls()

    sl_cmd = ["streamlink", "--stdout", HUYA_URL, STREAM_NAME]
    ff_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", "pipe:0",
        "-map", "0:v:0", "-map", "0:a:0",
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+omit_endlist",
        os.path.join(HLS_DIR, "live.m3u8")
    ]

    with proc_lock:
        sl_proc = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ff_proc = subprocess.Popen(ff_cmd, stdin=sl_proc.stdout, stderr=subprocess.DEVNULL)
        sl_proc.stdout.close()

    print(f"[Stream] Started: {HUYA_URL}")

def stop_stream():
    global sl_proc, ff_proc
    with proc_lock:
        for p in (ff_proc, sl_proc):
            if p and p.poll() is None:
                try:
                    p.kill()
                except:
                    pass
        sl_proc = None
        ff_proc = None
    time.sleep(1)

# ================================================================
#  HEARTBEAT
# ================================================================

def heartbeat():
    global running
    stream_start = time.time()
    while running:
        time.sleep(5)
        if not running:
            break
        try:
            with proc_lock:
                ff_poll = ff_proc.poll() if ff_proc else "no_proc"
                dead    = (ff_proc is None or ff_poll is not None)
            if dead and running:
                print("[Heartbeat] Stream dropped — restarting...")
                stream_start = time.time()
                start_stream()
            else:
                print(f"\r[Streaming]  {fmt_hms(time.time() - stream_start)}", end="", flush=True)
        except Exception as e:
            print(f"\n[Heartbeat] ERROR: {e}")

# ================================================================
#  HTTP HANDLER — gate page + HLS serving
# ================================================================

GATE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TV Stream</title>
<style>
  body {{ background:#111; color:#fff; font-family:sans-serif;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; height:100vh; margin:0; }}
  h2   {{ margin-bottom:30px; font-size:1.5em; }}
  input {{ font-size:2em; width:220px; text-align:center; padding:10px;
           border-radius:8px; border:none; letter-spacing:8px; }}
  button {{ margin-top:20px; font-size:1.2em; padding:12px 40px;
            border-radius:8px; border:none; background:#1a73e8;
            color:#fff; cursor:pointer; }}
  .err {{ color:#f66; margin-top:15px; font-size:1.1em; }}
</style>
</head>
<body>
  <h2>📺 Enter Access Code</h2>
  <form method="GET" action="/auth">
    <input type="text" name="code" maxlength="6" autofocus placeholder="______" />
    <br>
    <button type="submit">Watch</button>
  </form>
  {error}
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *a):
        pass  # suppress request logs

    def client_ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    def serve_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        ip = self.client_ip()

        # Gate page
        if self.path == "/" or self.path == "":
            html = GATE_HTML.format(error="")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
            return

        # Auth endpoint
        if self.path.startswith("/auth"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            code = qs.get("code", [""])[0].strip()
            if code == access_code:
                unlocked_ips.add(ip)
                print(f"[Auth] {ip} unlocked")
                self.send_response(302)
                self.send_header("Location", "/live.m3u8")
                self.end_headers()
            else:
                html = GATE_HTML.format(error='<p class="err">Wrong code. Try again.</p>')
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
            return

        # HLS files — only for unlocked IPs
        if ip not in unlocked_ips:
            self.send_error(404)
            return

        if self.path == "/live.m3u8":
            self.serve_file(os.path.join(HLS_DIR, "live.m3u8"), "application/vnd.apple.mpegurl")
        elif self.path.endswith(".ts"):
            fname = os.path.basename(self.path)
            self.serve_file(os.path.join(HLS_DIR, fname), "video/mp2t")
        else:
            self.send_error(404)

# ================================================================
#  MAIN
# ================================================================

def main():
    global running

    os.makedirs(HLS_DIR, exist_ok=True)

    print("=" * 58)
    print("  HUYA → TV STREAMER (CLOUD)  v1.0")
    print("=" * 58)
    print(f"  Channel : {HUYA_URL}")
    print(f"  Port    : {PORT}")
    print(f"  Code    : {access_code}")
    print("=" * 58)

    # Send code to Telegram
    tg_send(f"📺 TV Streamer started\nChannel: {HUYA_URL}\nAccess code: {access_code}")

    # Start stream
    start_stream()

    # Start heartbeat
    threading.Thread(target=heartbeat, daemon=True).start()

    # Start HTTP server
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[HTTP] Server started on port {PORT}")

    def shutdown(sig, frame):
        global running
        print("\n[Shutdown] Stopping...")
        running = False
        stop_stream()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.serve_forever()

if __name__ == "__main__":
    main()
