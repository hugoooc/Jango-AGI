"""LegacyPilot dashboard server — localhost, zero external deps (stdlib only).

A single-page UI to ask design questions in plain language. Submitting a question
runs the engine on THIS machine (you watch OpenVSP being driven live on the
desktop) and returns a structured report the page renders as a sentence, a table,
and a chart.

  python -m ui.server            # serves http://localhost:8765

Endpoints:
  GET  /              the dashboard page
  POST /ask           {question, slow?} -> {job_id}   (starts a background job)
  POST /voice         {} -> {transcript}              (captures one microphone turn)
  GET  /status?id=..  -> {state, report|error, seconds}
  GET  /examples      -> a few ready-made questions

Jobs run one at a time (one OpenVSP on this Mac). A second ask while busy is
rejected with a clear message.
"""
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import engine  # noqa: E402
from sdk_product.voice import gradium_api_key, transcribe_microphone  # noqa: E402

HERE = os.path.dirname(__file__)
PORT = int(os.environ.get("LP_PORT", "8765"))

_jobs = {}                      # job_id -> {state, report, error, seconds, question}
_lock = threading.Lock()
_busy = threading.Event()       # only one OpenVSP job at a time
_voice_busy = threading.Event() # only one microphone capture at a time


EXAMPLES = [
    "Impact on mass if the wingspan goes to 12m?",
    "How do mass and CG change if I increase wingspan by 10%?",
    "Set wing chord to 4 and report wetted area and volume",
    "Reduce tail span by 20% — effect on mass and inertia?",
]


def _run_job(job_id, question, slow):
    try:
        report = engine.ask(question, fast_only=not slow)
        with _lock:
            _jobs[job_id].update(state="done", report=report,
                                 seconds=report.get("seconds"))
    except Exception as e:
        with _lock:
            _jobs[job_id].update(state="error", error=f"{type(e).__name__}: {e}")
    finally:
        _busy.clear()


def _capture_voice():
    """Capture one turn on the server Mac; kept separate for deterministic tests."""
    return asyncio.run(transcribe_microphone(gradium_api_key()))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/examples":
            self._send(200, json.dumps(EXAMPLES))
        elif path == "/status":
            qs = parse_qs(urlparse(self.path).query)
            job_id = (qs.get("id") or [""])[0]
            with _lock:
                job = _jobs.get(job_id)
            if not job:
                self._send(404, json.dumps({"error": "unknown job"}))
            else:
                self._send(200, json.dumps(job))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/voice":
            self._handle_voice()
            return
        if path != "/ask":
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}
        question = (payload.get("question") or "").strip()
        if not question:
            self._send(400, json.dumps({"error": "empty question"}))
            return
        with _lock:
            unavailable = _busy.is_set() or _voice_busy.is_set()
            if not unavailable:
                _busy.set()
        if unavailable:
            self._send(409, json.dumps({"error": "busy — LegacyPilot is already running"}))
            return
        job_id = str(int(time.time() * 1000))
        with _lock:
            _jobs[job_id] = {"state": "running", "question": question,
                             "report": None, "error": None, "seconds": None}
        threading.Thread(target=_run_job, args=(job_id, question, bool(payload.get("slow"))),
                         daemon=True).start()
        self._send(200, json.dumps({"job_id": job_id}))

    def _handle_voice(self):
        with _lock:
            unavailable = _busy.is_set() or _voice_busy.is_set()
            if not unavailable:
                _voice_busy.set()
        if unavailable:
            self._send(409, json.dumps({"error": "busy — LegacyPilot is already running"}))
            return
        try:
            transcript = _capture_voice()
            self._send(200, json.dumps({"transcript": transcript}))
        except Exception as exc:
            self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            _voice_busy.clear()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"LegacyPilot dashboard → http://localhost:{PORT}")
    print("(OpenVSP will be driven live on this desktop as questions run)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
