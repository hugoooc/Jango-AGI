"""Local dashboard that creates and controls Dockerized OpenVSP GUI workers."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("LP_ORCHESTRATOR_PORT", "8765"))
HERE = Path(__file__).parent


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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
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
