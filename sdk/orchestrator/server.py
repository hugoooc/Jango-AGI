"""Local dashboard that creates and controls Dockerized OpenVSP GUI workers."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sdk_product.voice import gradium_api_key, transcribe_microphone

PORT = int(os.environ.get("LP_ORCHESTRATOR_PORT", "8765"))
VOICE_MAX_SECONDS = float(os.environ.get("LP_VOICE_MAX_SECONDS", "300"))
VOICE_SESSION_TTL = float(os.environ.get("LP_VOICE_SESSION_TTL", "300"))
CSRF_TOKEN = secrets.token_urlsafe(32)
HERE = Path(__file__).parent

_voice_sessions: dict[str, dict] = {}
_voice_lock = threading.Lock()
_voice_busy = threading.Event()
_ask_busy = threading.Event()


def _load_env() -> None:
    path = Path(__file__).parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

from . import docker_fleet as fleet  # noqa: E402
from . import agent  # noqa: E402


def _capture_voice(stop_signal: threading.Event, on_text) -> str:
    """Capture host microphone audio and stream live Gradium transcripts."""
    return asyncio.run(transcribe_microphone(
        gradium_api_key(),
        max_seconds=VOICE_MAX_SECONDS,
        stop_signal=stop_signal,
        on_text=on_text,
    ))


def _public_voice(session: dict) -> dict:
    return {key: session.get(key) for key in ("state", "transcript", "error")}


def _prune_voice_sessions(now: float | None = None) -> None:
    """Remove completed sessions after the result-retrieval window.

    Caller must hold ``_voice_lock``.
    """
    now = time.monotonic() if now is None else now
    expired = [
        voice_id for voice_id, session in _voice_sessions.items()
        if session.get("finished_at") is not None
        and now - session["finished_at"] >= VOICE_SESSION_TTL
    ]
    for voice_id in expired:
        del _voice_sessions[voice_id]


def _run_voice(voice_id: str) -> None:
    with _voice_lock:
        stop_signal = _voice_sessions[voice_id]["stop_signal"]

    def publish(transcript: str) -> None:
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session["transcript"] = transcript

    try:
        transcript = _capture_voice(stop_signal, publish)
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session.update(state="done", transcript=transcript)
    except ValueError:
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session.update(state="error", error="No speech was transcribed")
    except Exception as exc:
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session.update(state="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session:
                session["finished_at"] = time.monotonic()
        _voice_busy.clear()


def _dispatch(items: list[fleet.Worker], values: list[float] | None, mode: str) -> list[dict]:
    results: list[dict | None] = [None] * len(items)

    def send(position: int, item: fleet.Worker) -> None:
        try:
            actions = (
                fleet.wing_span_actions(values[position])
                if mode == "wing_span"
                else fleet.smoke_actions(item.index)
            )
            results[position] = {
                "worker": item.json(),
                **fleet.request(item, "POST", "/actions", {"actions": actions}),
                "value": values[position] if values else None,
            }
        except Exception as exc:
            results[position] = {"worker": item.json(), "error": f"{type(exc).__name__}: {exc}"}

    threads = [threading.Thread(target=send, args=(position, item)) for position, item in enumerate(items)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [result for result in results if result is not None]


def _parallel_mass_sweep(values: list[float]) -> dict:
    """MASTER AGENT: fan a wingspan sweep across all workers, run set+measure in
    parallel, wait for each, and collect the mass curve. Wall-clock ~= ONE run,
    not N runs — that's the point of the fleet."""
    started = time.time()
    workers = fleet.ensure_workers(len(values))
    points: list[dict | None] = [None] * len(values)

    # BAKE ONCE: ground the control coordinates a single time (on worker 1),
    # cached across runs. Every worker then replays baked clicks -> ~1 vision
    # call per run instead of 7. This is the big speedup.
    try:
        coords = fleet.discover_coords(workers[0])
    except Exception:
        coords = {}   # fall back to fully vision-grounded if discovery fails

    def run_point(pos: int, item: fleet.Worker) -> None:
        try:
            actions = fleet.wing_span_mass_actions_baked(values[pos], coords)
            ack = fleet.request(item, "POST", "/actions", {"actions": actions})
            job = fleet.wait_job(item, ack["job_id"])
            vals = fleet.mass_from_job(job) or {}
            points[pos] = {"x": values[pos], "y": vals.get("Total_Mass"),
                           "cg_x": vals.get("X_Cg"), "worker": item.index,
                           "state": job.get("state")}
        except Exception as exc:
            points[pos] = {"x": values[pos], "y": None, "worker": item.index,
                           "error": f"{type(exc).__name__}: {exc}"}

    threads = [threading.Thread(target=run_point, args=(i, w)) for i, w in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    pts = [p for p in points if p is not None]
    return {"study": "parallel_mass_sweep", "input": "wing_span", "output": "mass",
            "points": pts, "workers": len(workers), "baked": bool(coords),
            "seconds": round(time.time() - started, 1)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, value) -> None:
        self._send(code, json.dumps(value).encode(), "application/json")

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _csrf_is_valid(self) -> bool:
        supplied = self.headers.get("X-LegacyPilot-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, CSRF_TOKEN)

    def _handle_voice_start(self) -> None:
        with _voice_lock:
            _prune_voice_sessions()
            unavailable = _voice_busy.is_set() or _ask_busy.is_set()
            if not unavailable:
                _voice_busy.set()
        if unavailable:
            self._json(409, {"error": "the fleet or voice input is already running"})
            return

        voice_id = secrets.token_urlsafe(18)
        with _voice_lock:
            _voice_sessions[voice_id] = {
                "state": "running",
                "transcript": "",
                "error": None,
                "stop_signal": threading.Event(),
            }
        threading.Thread(target=_run_voice, args=(voice_id,), daemon=True).start()
        self._json(200, {"voice_id": voice_id})

    def _handle_voice_stop(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        voice_id = (query.get("id") or [""])[0]
        with _voice_lock:
            session = _voice_sessions.get(voice_id)
            if session and session["state"] == "running":
                session["state"] = "stopping"
                session["stop_signal"].set()
            public = _public_voice(session) if session else None
        if public is None:
            self._json(404, {"error": "unknown voice session"})
        else:
            self._json(200, public)

    def _handle_ask(self, payload: dict) -> None:
        with _voice_lock:
            unavailable = _voice_busy.is_set() or _ask_busy.is_set()
            if not unavailable:
                _ask_busy.set()
        if unavailable:
            self._json(409, {"error": "the fleet or voice input is already running"})
            return

        try:
            # ASK: the agent plans from plain language, then dispatches the
            # fleet (spins up exactly the workers it needs) and waits.
            question = str(payload.get("question", ""))
            decision = agent.plan(question, max_workers=fleet.MAX_WORKERS)
            if not decision.get("ok"):
                self._json(400, {"error": decision.get("error", "could not plan")})
                return
            result = _parallel_mass_sweep(decision["values"])
            result["question"] = question
            result["plan"] = decision["note"]
            self._json(200, result)
        finally:
            _ask_busy.clear()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                page = (HERE / "index.html").read_bytes().replace(
                    b"__CSRF_TOKEN__", CSRF_TOKEN.encode(),
                )
                self._send(200, page, "text/html; charset=utf-8")
                return
            if path == "/api/voice/status":
                query = parse_qs(urlparse(self.path).query)
                voice_id = (query.get("id") or [""])[0]
                with _voice_lock:
                    _prune_voice_sessions()
                    session = _voice_sessions.get(voice_id)
                    public = _public_voice(session) if session else None
                if public is None:
                    self._json(404, {"error": "unknown voice session"})
                else:
                    self._json(200, public)
                return
            if path == "/api/workers":
                self._json(200, [item.json() for item in fleet.list_workers()])
                return
            if path.startswith("/api/workers/"):
                parts = path.strip("/").split("/")
                item = fleet.worker(int(parts[2]))
                if len(parts) == 4 and parts[3] == "screenshot":
                    self._send(200, fleet.request(item, "GET", "/screenshot"), "image/png")
                    return
                if len(parts) == 5 and parts[3] == "jobs":
                    self._json(200, fleet.request(item, "GET", f"/jobs/{parts[4]}"))
                    return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/api/voice/start", "/api/voice/stop"):
                if not self._csrf_is_valid():
                    self._json(403, {"error": "invalid request token"})
                    return
                if path == "/api/voice/start":
                    self._handle_voice_start()
                else:
                    self._handle_voice_stop()
                return
            payload = self._payload()
            if path == "/api/workers/start":
                items = fleet.ensure_workers(int(payload.get("count", 1)))
                self._json(200, [item.json() for item in items])
                return
            if path == "/api/workers/stop":
                fleet.stop_all()
                self._json(200, {"stopped": True})
                return
            if path == "/api/runs":
                mode = str(payload.get("mode", "smoke"))
                values = [float(value) for value in payload.get("values", [])]
                count = len(values) if mode == "wing_span" else int(payload.get("count", 2))
                if mode == "wing_span" and not values:
                    raise ValueError("wing_span mode requires values")
                items = fleet.ensure_workers(count)
                self._json(202, {"mode": mode, "runs": _dispatch(items, values or None, mode)})
                return
            if path == "/api/sweep":
                # MASTER AGENT: parallel mass sweep across the fleet -> a curve.
                values = [float(v) for v in payload.get("values", [])]
                if not values:
                    raise ValueError("sweep requires a non-empty 'values' list")
                self._json(200, _parallel_mass_sweep(values))
                return
            if path == "/api/ask":
                self._handle_ask(payload)
                return
            self._json(404, {"error": "not found"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"LegacyPilot container fleet → http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
