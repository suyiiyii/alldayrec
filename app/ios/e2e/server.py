"""The live-record server's client endpoints as run.sh needs them: keeps every upload, decodes it with ffmpeg (as
server/audio.py does: 400 when it cannot), logs status reports, event logs and end signals to OUT/server.jsonl; Bearer
token required. Any other page (the app's WebViews) says a test is running. `check` reads that log back: every piece decoded, and the recorder's audio one gap-free run in time.
    python3 server.py serve HOST PORT TOKEN OUT
    python3 server.py check OUT DEVICE"""
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def seconds(body: bytes) -> float | None:
    """As server/audio.py decode(): what ffmpeg exits 0 on is audio, warnings or not (an Android or iPhone batch of
    chained Ogg streams makes it warn about timestamps, 10-05)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                       input=body, capture_output=True)
    return None if p.returncode or len(p.stdout) < 2 else len(p.stdout) / 32000


def serve(host: str, port: int, token: str, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "server.jsonl", "a", buffering=1)

    class H(BaseHTTPRequestHandler):
        def reply(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def authorized(self):
            if self.headers.get("Authorization") == f"Bearer {token}":
                return True
            self.reply({"error": "token"}, 401)
            return False

        def do_GET(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/"):  # the app's web pages (?token=): what is going on, not a token error
                b = ("<meta name=viewport content='width=device-width'><p style='font:17px sans-serif;padding:24px'>"
                     "alldayrec 正在做自动测试，几分钟后自动恢复正常设置。</p>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if not self.authorized():
                return
            if path == "/api/client-config":
                return self.reply({"slice": {"foreground": 1, "background": 20, "screenOff": 60}})
            if path == "/api/status":
                return self.reply({"devices": []})
            self.reply({"error": "none"}, 404)

        def do_POST(self):
            if not self.authorized():
                return
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            rec = {"at": int(time.time() * 1000), "path": u.path, **q}
            if u.path == "/api/chunks":
                rec.update(bytes=len(body), seconds=seconds(body), type=self.headers.get("Content-Type"))
                d = out / q["device"]
                d.mkdir(exist_ok=True)
                (d / f"{q['start']}.ogg").write_bytes(body)
            elif u.path in ("/api/device-status", "/api/device-log"):
                rec["body"] = json.loads(body or b"null")
            log.write(json.dumps(rec) + "\n")
            if u.path == "/api/chunks" and rec["seconds"] is None:
                return self.reply({"error": "could not decode"}, 400)
            self.reply({"ok": True})

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()


def check(out: Path, device: str) -> int:
    recs = [json.loads(l) for l in open(out / "server.jsonl")]
    chunks = sorted((r for r in recs if r["path"] == "/api/chunks" and r["device"] == device), key=lambda r: int(r["start"]))
    bad = [r for r in recs if r["path"] == "/api/chunks" and r["seconds"] is None]
    problems = [f"{len(bad)} uploads did not decode"] if bad else []
    end = None
    for r in chunks:  # each request's audio [start, start + seconds): back to back, as the recorder's frames are
        if r["seconds"] is None:
            continue  # counted above; the app dropped it (400), so its time shows as a gap
        start = int(r["start"])
        if end is not None and abs(start - end) > 100:  # two TIME writes differ by their BLE latency
            problems.append(f"{'gap' if start > end else 'overlap'} of {abs(start - end)} ms at {start}")
        end = start + round(r["seconds"] * 1000)
    total = sum(r["seconds"] or 0 for r in chunks)
    status = [r["body"] for r in recs if r["path"] == "/api/device-status" and r["device"] == device]
    events = sum(len(r["body"] or []) for r in recs if r["path"] == "/api/device-log")
    print(f"{device}: {len(chunks)} uploads, {total:.1f} s of audio, from {chunks[0]['start'] if chunks else '-'} to {end}; "
          f"{len(status)} status reports (last {status[-1] if status else '-'}); {events} log events; "
          f"{sum(r['path'] == '/api/chunks/end' for r in recs)} end signals")
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems or not chunks else 0


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve(sys.argv[2], int(sys.argv[3]), sys.argv[4], Path(sys.argv[5]))
    else:
        sys.exit(check(Path(sys.argv[2]), sys.argv[3]))
