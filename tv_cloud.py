# ================================================================
#  HUYA → TV STREAMER (CLOUD) — tv_cloud.py  v1.6
#  Headless Linux version for ClawCloud / Docker
#  Gate page: channel select + code + Watch/Stop
# ================================================================

import os
import subprocess
import threading
import time
import random
import signal
import sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

# ================================================================
#  CONFIG — set via environment variables in ClawCloud
# ================================================================

TG_TOKEN   = os.environ.get("TG_TOKEN",  "8230303716:AAH3bppU55xK4mTEmLh2gCFTc91SjqkvGUk")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "5406920859")
PORT       = int(os.environ.get("PORT", 8080))
HLS_DIR    = "/tmp/hls"
STREAM_NAME = "best"

CHANNELS = [
    ("虎牙斯诺克",      "https://m.huya.com/880214"),   # default
    ("斯诺克副台",      "https://m.huya.com/880625"),
    ("光头阿强",        "https://m.huya.com/28962587"),
    ("台球1号桌",       "https://m.huya.com/20072620"),
    ("台球2号桌",       "https://m.huya.com/20072621"),
    ("台球3号桌",       "https://m.huya.com/18501408"),
    ("台球4号桌",       "https://m.huya.com/18501324"),
    ("台球5号桌",       "https://m.huya.com/17455465"),
    ("台球6号桌",       "https://m.huya.com/18501329"),
    ("台球7号桌",       "https://m.huya.com/18501166"),
    ("台球8号桌",       "https://m.huya.com/17611732"),
    ("银诺台球",        "https://m.huya.com/yinuotaiqiu"),
    ("星辰直播",        "https://m.huya.com/398147"),
    ("李锦教练",        "https://m.huya.com/631274"),
    ("洋洋台球",        "https://m.huya.com/22939862"),
    ("梁台球",          "https://m.huya.com/26410375"),
    ("肖国栋",          "https://m.huya.com/210147"),
    ("Long3mu",         "https://m.huya.com/26245146"),
]

# ================================================================
#  STATE
# ================================================================

access_code  = str(random.randint(100000, 999999))
unlocked_ips = set()
current_url  = CHANNELS[0][1]
sl_proc      = None
ff_proc      = None
running      = True
proc_lock    = threading.Lock()

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
            if f.startswith("live") or f == "input.ts":
                try:
                    os.remove(os.path.join(HLS_DIR, f))
                except:
                    pass
    except:
        pass

# ================================================================
#  STREAM
# ================================================================

def start_stream(url=None):
    global sl_proc, ff_proc, current_url
    if url:
        current_url = url
    cleanup_hls()

    TEMP_TS = "/tmp/hls/input.ts"

    sl_cmd = ["streamlink", "--output", TEMP_TS, "--force", "--hls-live-restart", current_url, STREAM_NAME]
    ff_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-re", "-i", TEMP_TS,
        "-map", "0:v:0", "-map", "0:a:0",
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+omit_endlist",
        os.path.join(HLS_DIR, "live.m3u8")
    ]

    sl = subprocess.Popen(sl_cmd, stderr=subprocess.DEVNULL)
    # Wait for streamlink to create and start writing the file
    print("[Stream] Waiting for streamlink to start...")
    deadline = time.time() + 30
    while time.time() < deadline:
        if os.path.exists(TEMP_TS) and os.path.getsize(TEMP_TS) > 10000:
            break
        time.sleep(0.5)
    print("[Stream] Starting ffmpeg...")
    ff = subprocess.Popen(ff_cmd, stderr=None)

    with proc_lock:
        sl_proc = sl
        ff_proc = ff

    print(f"[Stream] Started: {current_url}")

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
#  GATE PAGE HTML
# ================================================================

def build_gate_html(error=""):
    rows = ""
    for i, (name, url) in enumerate(CHANNELS, 1):
        default = " ← default" if i == 1 else ""
        rows += f'<tr><td><input type="radio" name="ch" value="{url}" {"checked" if i==1 else ""}></td><td>{i}.</td><td>{name}</td><td style="color:#aaa;font-size:0.85em">{url}{default}</td></tr>\n'
    rows += f'<tr><td><input type="radio" name="ch" value="custom"></td><td>0.</td><td colspan="2"><input type="text" name="custom_url" placeholder="Enter Huya URL manually" style="width:320px;font-size:0.95em;padding:4px;border-radius:4px;border:none"></td></tr>\n'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>📺 TV Stream</title>
<style>
  body {{ background:#111; color:#fff; font-family:monospace;
         padding:30px 20px; margin:0; }}
  h2   {{ margin-bottom:20px; font-size:1.3em; }}
  table {{ border-collapse:collapse; margin-bottom:24px; }}
  td   {{ padding:5px 12px; }}
  td:first-child {{ padding-right:6px; }}
  .sep {{ border-top:1px solid #333; margin:20px 0; }}
  .code-row {{ display:flex; align-items:center; gap:12px; margin-bottom:18px; }}
  input[type=text] {{ background:#222; color:#fff; }}
  input.code {{ font-size:1.8em; width:160px; text-align:center;
                letter-spacing:6px; padding:8px; border-radius:6px;
                border:none; background:#222; color:#fff; }}
  .btn {{ font-size:1.1em; padding:10px 32px; border-radius:6px;
          border:none; cursor:pointer; margin-right:12px; }}
  .watch {{ background:#1a73e8; color:#fff; }}
  .stop  {{ background:#c0392b; color:#fff; }}
  .err   {{ color:#f66; margin-top:10px; }}
</style>
</head>
<body>
<h2>📺 虎牙直播 — 选择频道</h2>
<form method="GET" action="/auth">
<table>
{rows}
</table>
<div class="sep"></div>
<div class="code-row">
  <span>Access Code:</span>
  <input class="code" type="text" name="code" maxlength="6" autofocus placeholder="______">
</div>
<button class="btn watch" type="submit" name="action" value="watch">▶ Watch</button>
<button class="btn stop"  type="submit" name="action" value="stop">■ Stop Server</button>
{('<p class="err">' + error + '</p>') if error else ''}
</form>
</body>
</html>"""

# ================================================================
#  HTTP HANDLER
# ================================================================

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *a):
        pass

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
        global running
        ip = self.client_ip()

        # Gate page
        if self.path == "/" or self.path == "":
            html = build_gate_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        # Auth endpoint
        if self.path.startswith("/auth"):
            qs   = parse_qs(urlparse(self.path).query)
            code = qs.get("code", [""])[0].strip()
            action = qs.get("action", ["watch"])[0]

            if code != access_code:
                html = build_gate_html(error="Wrong code. Try again.")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return

            # Stop server
            if action == "stop":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h2 style='font-family:sans-serif;color:#fff;background:#111;padding:40px'>Server stopped. Goodbye!</h2>")
                print("[Stop] Shutdown requested via browser")
                threading.Thread(target=lambda: (time.sleep(1), os.kill(os.getpid(), signal.SIGTERM)), daemon=True).start()
                return

            # Watch — get selected channel
            ch_val     = qs.get("ch", [CHANNELS[0][1]])[0]
            custom_url = qs.get("custom_url", [""])[0].strip()
            if ch_val == "custom" and "huya.com" in custom_url:
                url = custom_url
            elif ch_val == "custom":
                html = build_gate_html(error="Invalid Huya URL.")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return
            else:
                url = ch_val

            unlocked_ips.add(ip)
            print(f"[Auth] {ip} unlocked → {url}")

            # Only restart if different channel or stream is dead
            with proc_lock:
                ff_poll = ff_proc.poll() if ff_proc else "no_proc"
                stream_dead = (ff_proc is None or ff_poll is not None)

            if url != current_url or stream_dead:
                stop_stream()
                start_stream(url)

            self.send_response(302)
            self.send_header("Location", "/live.m3u8")
            self.end_headers()
            return

        # HLS — only unlocked IPs
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
    print("  HUYA → TV STREAMER (CLOUD)  v1.6")
    print("=" * 58)
    print(f"  Port : {PORT}")
    print(f"  Code : {access_code}")
    print("=" * 58)

    tg_send(f"📺 TV Streamer started\nAccess code: {access_code}")

    threading.Thread(target=heartbeat, daemon=True).start()

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
