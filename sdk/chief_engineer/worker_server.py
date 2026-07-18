"""HTTP control plane for one isolated, headless OpenVSP API container."""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .api import OpenVSPDirectApi


PORT = int(os.environ.get("WORKER_PORT", "8080"))
WORKER_ID = os.environ.get("WORKER_ID", "api-worker")
MODEL_PATH = os.environ.get("MODEL_PATH", "/workspace/models/boeing777200.vsp3")
VSPAERO_PATH = os.environ.get("VSPAERO_PATH", "/opt/OpenVSP")
WORK_ROOT = Path(os.environ.get("WORK_ROOT", "/worker-runs"))

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _evaluate(job_id: str, payload: dict) -> None:
    candidate_id = str(payload.get("candidate_id", "candidate"))
    workdir = WORK_ROOT / candidate_id

    def progress(item: dict) -> None:
        event = dict(item)
        artifact = event.get("artifact")
        if isinstance(artifact, dict):
            artifact = dict(artifact)
            artifact.pop("path", None)
            event["artifact"] = artifact
        with _jobs_lock:
            _jobs[job_id]["progress"].append(event)
            _jobs[job_id]["phase"] = event.get("phase")

    api = OpenVSPDirectApi(
        MODEL_PATH,
        vspaero_path=VSPAERO_PATH,
        workdir=workdir,
        progress_sink=progress,
    )
    closed = False
    try:
        metrics = api.evaluate(payload.get("design", {}), payload.get("analyses", []))
        artifacts = []
        for item in api.artifacts():
            artifact = dict(item)
            artifact.pop("path", None)
            artifacts.append(artifact)
        # OpenVSP owns process-global state. Do not advertise this evaluation
        # as complete until cleanup is finished, otherwise the controller may
        # dispatch the next candidate into the same context concurrently.
        api.close()
        closed = True
        with _jobs_lock:
            _jobs[job_id].update(state="complete", metrics=dict(metrics), artifacts=artifacts)
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if not closed:
            api.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {
                "worker_id": WORKER_ID,
                "runtime": "docker-container",
                "api": "openvsp-python",
                "model": MODEL_PATH,
                "pid": os.getpid(),
            })
            return
        if parsed.path.startswith("/evaluations/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job:
                    response = {**job, "progress": job.get("progress", [])[after:]}
                    response["progress_cursor"] = len(job.get("progress", []))
                else:
                    response = None
            self._json(200 if response else 404, response or {"error": "unknown evaluation"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/evaluations":
            self._json(404, {"error": "not found"})
            return
        try:
            payload = self._payload()
            job_id = "e-" + secrets.token_hex(6)
            with _jobs_lock:
                _jobs[job_id] = {
                    "job_id": job_id,
                    "worker_id": WORKER_ID,
                    "candidate_id": str(payload.get("candidate_id", "candidate")),
                    "state": "running",
                    "phase": "queued",
                    "progress": [],
                }
            threading.Thread(target=_evaluate, args=(job_id, payload), daemon=True).start()
            self._json(202, {"job_id": job_id, "worker_id": WORKER_ID, "state": "running"})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    print(f"Jango API worker {WORKER_ID} listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
