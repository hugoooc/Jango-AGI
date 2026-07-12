"""HTTP control plane for one isolated OpenVSP virtual desktop."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import linux_desktop as desktop


PORT = int(os.environ.get("WORKER_PORT", "8080"))
WORKER_ID = os.environ.get("WORKER_ID", os.environ.get("HOSTNAME", "worker"))
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _openvsp_running() -> bool:
    return subprocess.run(["pgrep", "-x", "vsp"], capture_output=True).returncode == 0


def _run_job(job_id: str, actions: list[dict]) -> None:
    started = time.monotonic()
    completed = []
    try:
        with _lock:
            desktop.focus_openvsp()
            for index, action in enumerate(actions):
                _jobs[job_id]["step"] = index
                completed.append(desktop.execute(action))
        _jobs[job_id].update(
            state="done",
            actions=completed,
            seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:
        _jobs[job_id].update(
            state="error",
            error=f"{type(exc).__name__}: {exc}",
            actions=completed,
            seconds=round(time.monotonic() - started, 3),
        )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def _json(self, code: int, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {
                "worker_id": WORKER_ID,
                "openvsp": _openvsp_running(),
                "display": os.environ.get("DISPLAY", ":99"),
                "screen": desktop.screen_size(),
            })
            return
        if path == "/screenshot":
            png = desktop.screenshot()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if path.startswith("/jobs/"):
            job = _jobs.get(path.rsplit("/", 1)[-1])
            self._json(200 if job else 404, job or {"error": "unknown job"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/actions":
                actions = payload.get("actions")
                if not isinstance(actions, list) or not actions:
                    raise ValueError("actions must be a non-empty list")
                job_id = uuid.uuid4().hex
                _jobs[job_id] = {"state": "running", "worker_id": WORKER_ID, "step": 0}
                threading.Thread(target=_run_job, args=(job_id, actions), daemon=True).start()
                self._json(202, {"job_id": job_id, "worker_id": WORKER_ID})
                return
            if path == "/locate":
                x, y = desktop.locate(str(payload["target"]))
                self._json(200, {"x": x, "y": y, "worker_id": WORKER_ID})
                return
            self._json(404, {"error": "not found"})
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"OpenVSP worker {WORKER_ID} listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
