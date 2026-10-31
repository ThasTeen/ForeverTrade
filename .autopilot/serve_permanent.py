import os
import sys
import argparse
import http.server
import socketserver
import time
import signal

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 10459
PID_FILE = os.path.join(DIRECTORY, "serve.pid")
LOG_FILE = os.path.join(DIRECTORY, "daemon.log")

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

class RobustHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Disable caching for live autopilot state and html
        if self.path.endswith("state.js") or self.path.endswith("dashboard.html"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format, *args):
        log("%s - - %s" % (self.address_string(), format % args))

class ReusableThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def write_pid(port):
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(f"{port} {os.getpid()}\n")
    except Exception as e:
        log(f"Failed to write PID file: {e}")

def cleanup_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Permanent Autopilot Dashboard Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    args = parser.parse_args()
    port = args.port

    def on_signal(signum, frame):
        log(f"Received signal {signum}, stopping gracefully...")
        cleanup_pid()
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
    except Exception:
        pass

    log(f"Starting permanent dashboard server on port {port}...")

    # Retry loop in case port is in TIME_WAIT
    while True:
        try:
            httpd = ReusableThreadingServer(("127.0.0.1", port), RobustHandler)
            write_pid(port)
            log(f"Server successfully bound and listening at http://127.0.0.1:{port}/dashboard.html (PID: {os.getpid()})")
            try:
                httpd.serve_forever()
            finally:
                httpd.server_close()
        except Exception as srv_err:
            log(f"Server error: {srv_err}. Retrying in 1s...")
            time.sleep(1)

if __name__ == "__main__":
    main()
