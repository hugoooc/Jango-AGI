"""LegacyPilot dashboard server — localhost, zero external deps (stdlib only).

A single-page UI to ask design questions in plain language. Submitting a question
runs the engine on THIS machine (you watch OpenVSP being driven live on the
desktop) and returns a structured report the page renders as a sentence, a table,
and a chart.

  python -m ui.server            # serves http://localhost:8765

Endpoints:
  GET  /              the dashboard page
  POST /ask           {question, slow?} -> {job_id}   (starts a background job)
  POST /voice/start   {} -> {voice_id}                (starts microphone streaming)
  POST /voice/stop?id=..                                (flushes and ends the turn)
  GET  /voice/status?id=.. -> {state, transcript, error}
  GET  /status?id=..  -> {state, report|error, seconds}
  GET  /examples      -> a few ready-made questions

Jobs run one at a time (one OpenVSP on this Mac). A second ask while busy is
rejected with a clear message.
"""
import asyncio
import hmac
import json
import os
import secrets
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
VOICE_MAX_SECONDS = float(os.environ.get("LP_VOICE_MAX_SECONDS", "300"))
VOICE_SESSION_TTL = float(os.environ.get("LP_VOICE_SESSION_TTL", "300"))
CSRF_TOKEN = secrets.token_urlsafe(32)

_jobs = {}                      # job_id -> {state, report, error, seconds, question}
_voice_sessions = {}            # voice_id -> internal capture state
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


def _capture_voice(stop_signal, on_text):
    """Stream until Stop is requested; kept separate for deterministic tests."""
    return asyncio.run(transcribe_microphone(
        gradium_api_key(), max_seconds=VOICE_MAX_SECONDS,
        stop_signal=stop_signal, on_text=on_text,
    ))


def _run_voice(voice_id):
    with _lock:
        stop_signal = _voice_sessions[voice_id]["stop_signal"]

    def publish(transcript):
        with _lock:
            _voice_sessions[voice_id]["transcript"] = transcript

    try:
        transcript = _capture_voice(stop_signal, publish)
        with _lock:
            _voice_sessions[voice_id].update(state="done", transcript=transcript)
    except ValueError:
        with _lock:
            _voice_sessions[voice_id].update(
                state="error", error="No speech was transcribed",
            )
    except Exception as exc:
        with _lock:
            _voice_sessions[voice_id].update(
                state="error", error=f"{type(exc).__name__}: {exc}",
            )
    finally:
        with _lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session["finished_at"] = time.monotonic()
        _voice_busy.clear()


def _public_voice(session):
    return {key: session.get(key) for key in ("state", "transcript", "error")}


def _prune_voice_sessions(now=None):
    """Drop completed captures after a short result-retrieval window.

    Caller must hold ``_lock``.
    """
    now = time.monotonic() if now is None else now
    expired = [
        voice_id for voice_id, session in _voice_sessions.items()
        if session.get("finished_at") is not None
        and now - session["finished_at"] >= VOICE_SESSION_TTL
    ]
    for voice_id in expired:
        del _voice_sessions[voice_id]


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

    def _csrf_is_valid(self):
        supplied = self.headers.get("X-LegacyPilot-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, CSRF_TOKEN)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                page = f.read().replace(b"__CSRF_TOKEN__", CSRF_TOKEN.encode())
                self._send(200, page, "text/html; charset=utf-8")
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
        elif path == "/voice/status":
            qs = parse_qs(urlparse(self.path).query)
            voice_id = (qs.get("id") or [""])[0]
            with _lock:
                _prune_voice_sessions()
                session = _voice_sessions.get(voice_id)
                public = _public_voice(session) if session else None
            if not public:
                self._send(404, json.dumps({"error": "unknown voice session"}))
            else:
                self._send(200, json.dumps(public))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._csrf_is_valid():
            self._send(403, json.dumps({"error": "invalid request token"}))
            return
        path = urlparse(self.path).path
        if path == "/voice/start":
            self._handle_voice_start()
            return
        if path == "/voice/stop":
            self._handle_voice_stop()
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

    def _handle_voice_start(self):
        with _lock:
            _prune_voice_sessions()
            unavailable = _busy.is_set() or _voice_busy.is_set()
            if not unavailable:
                _voice_busy.set()
        if unavailable:
            self._send(409, json.dumps({"error": "busy — LegacyPilot is already running"}))
            return
        voice_id = str(int(time.time() * 1000))
        with _lock:
            _voice_sessions[voice_id] = {
                "state": "running", "transcript": "", "error": None,
                "stop_signal": threading.Event(),
            }
        threading.Thread(target=_run_voice, args=(voice_id,), daemon=True).start()
        self._send(200, json.dumps({"voice_id": voice_id}))

    def _handle_voice_stop(self):
        qs = parse_qs(urlparse(self.path).query)
        voice_id = (qs.get("id") or [""])[0]
        with _lock:
            session = _voice_sessions.get(voice_id)
            if session and session["state"] == "running":
                session["state"] = "stopping"
                session["stop_signal"].set()
            public = _public_voice(session) if session else None
        if not public:
            self._send(404, json.dumps({"error": "unknown voice session"}))
        else:
            self._send(200, json.dumps(public))


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
