"""Live API-only chief-engineer service and mission control room."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .adapters import AdapterManifest, SoftwareAdapterRegistry, synthetic_registry
from .api import HttpSimulationApi, OpenVSPDirectApi, SubprocessOpenVSPApi, SyntheticApi
from .events import EventBus
from .fleet import DockerVmProvider, LocalVmProvider
from .mission import AutonomousChief, MissionOutcome


HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("CHIEF_ENGINEER_PORT", "8765"))
MAX_MISSIONS = 24


@dataclass
class MissionRecord:
    id: str
    request: str
    bus: EventBus
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    outcome: MissionOutcome | None = None
    result_snapshot: dict | None = None
    error: str | None = None

    def public(self) -> dict:
        return {
            "mission_id": self.id,
            "request": self.request,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.outcome.as_dict() if self.outcome is not None else self.result_snapshot,
        }


_missions: dict[str, MissionRecord] = {}
_missions_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(200, (HERE / "control_room.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._json(200, {
                "service": "hacknation-chief-engineer",
                "backend": _backend_name(),
                "worker_provider": _worker_provider_name(),
                "reasoning": _reasoning_name(),
                "persistence": "durable-jsonl",
                "missions": len(_missions),
            })
            return
        if path == "/api/capabilities":
            self._json(200, [manifest.as_dict() for manifest in _registry().manifests()])
            return
        artifact = _artifact_path(path)
        if artifact is not None:
            if not artifact.exists() or not artifact.is_file():
                self._json(404, {"error": "unknown artifact"})
                return
            content_types = {
                ".obj": "text/plain; charset=utf-8",
                ".vsp3": "application/octet-stream",
                ".polar": "text/plain; charset=utf-8",
                ".log": "text/plain; charset=utf-8",
            }
            self._send(200, artifact.read_bytes(), content_types.get(artifact.suffix, "application/octet-stream"))
            return
        if path == "/api/missions":
            with _missions_lock:
                records = sorted(_missions.values(), key=lambda item: item.created_at, reverse=True)
                self._json(200, [record.public() for record in records])
            return
        mission_id, suffix = _mission_path(path)
        if mission_id:
            record = _record(mission_id)
            if record is None:
                self._json(404, {"error": "unknown mission"})
                return
            if suffix == "events.json":
                after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
                events = [event.as_dict() for event in record.bus.snapshot(after=after)]
                self._json(200, {
                    "mission_id": mission_id,
                    "after": after,
                    "next_after": events[-1]["sequence"] if events else after,
                    "closed": record.bus.closed,
                    "events": events,
                })
                return
            if suffix == "events":
                after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
                self._events(record, after)
                return
            if not suffix:
                self._json(200, record.public())
                return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/missions", "/api/optimize"}:
            self._json(404, {"error": "not found"})
            return
        try:
            payload = self._payload()
            request = str(payload.get("goal") or payload.get("request") or "").strip()
            if not request:
                raise ValueError("goal is required")
            if path == "/api/optimize":
                mission_id = "sync-" + secrets.token_hex(5)
                bus = EventBus(mission_id)
                outcome = _chief(mission_id, bus).run(
                    request,
                    initial_design=payload.get("initial_design"),
                    worker_budget=_bounded_int(payload.get("workers"), 12, 1, 32),
                    max_cycles=_bounded_int(payload.get("cycles") or payload.get("iterations"), 3, 1, 8),
                )
                self._json(200, outcome.as_dict())
                return
            record = _start_mission(request, payload)
            self._json(202, {"mission_id": record.id, "state": record.state})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

    def _events(self, record: MissionRecord, after: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for event in record.bus.stream(after=after, heartbeat_s=8.0):
                if event is None:
                    chunk = b": heartbeat\n\n"
                else:
                    chunk = (
                        f"id: {event.sequence}\n"
                        f"event: mission\n"
                        f"data: {json.dumps(event.as_dict())}\n\n"
                    ).encode()
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def _start_mission(request: str, payload: dict) -> MissionRecord:
    mission_id = "m-" + secrets.token_hex(6)
    record = MissionRecord(mission_id, request, EventBus(mission_id, _events_path(mission_id)))
    with _missions_lock:
        if len(_missions) >= MAX_MISSIONS:
            oldest = min(_missions.values(), key=lambda item: item.created_at)
            if oldest.state in {"complete", "failed", "incomplete"}:
                _missions.pop(oldest.id, None)
        _missions[mission_id] = record
    _save_record(record)
    thread = threading.Thread(
        target=_run_mission,
        args=(record, payload),
        daemon=True,
        name=f"chief-{mission_id}",
    )
    thread.start()
    return record


def _run_mission(record: MissionRecord, payload: dict) -> None:
    record.state = "running"
    record.started_at = time.time()
    _save_record(record)
    try:
        record.outcome = _chief(record.id, record.bus).run(
            record.request,
            initial_design=payload.get("initial_design"),
            worker_budget=_bounded_int(payload.get("workers"), 12, 1, 32),
            max_cycles=_bounded_int(payload.get("cycles"), 3, 1, 8),
        )
        record.state = record.outcome.status
        record.result_snapshot = record.outcome.as_dict()
    except Exception as exc:
        record.state = "failed"
        record.error = f"{type(exc).__name__}: {exc}"
        record.bus.publish("mission.failed", {"reason": record.error})
    finally:
        record.finished_at = time.time()
        _save_record(record)
        record.bus.close()


def _chief(mission_id: str, bus: EventBus) -> AutonomousChief:
    registry = _registry(mission_id)
    return AutonomousChief(
        mission_id,
        registry.composite_factory(),
        event_sink=bus.publish,
        max_workers=32,
        parameter_specs=registry.parameter_specs(),
        metric_specs=registry.metric_specs(),
        domain_dependencies=registry.domain_dependencies(),
        domain_parameter_counts=registry.domain_parameter_counts(),
        vm_provider=_vm_provider(mission_id),
    )


def _registry(mission_id: str = "capabilities") -> SoftwareAdapterRegistry:
    model_path = os.environ.get("MODEL_PATH")
    if not model_path:
        latency = float(os.environ.get("CHIEF_SYNTHETIC_LATENCY_S", "0.16"))
        return synthetic_registry(lambda _handle: SyntheticApi(latency_s=latency))
    registry = SoftwareAdapterRegistry()
    vspaero_path = os.environ.get("VSPAERO_PATH")
    workdir = Path(os.environ.get("CHIEF_ENGINEER_WORKDIR", "./chief-engineer-runs"))
    concurrency = "isolated-container" if _worker_provider_name() == "docker" else "isolated-process"

    def openvsp_factory(handle):
        candidate_id = handle.metadata.get("candidate_id", handle.id)
        artifact_prefix = f"/api/artifacts/{mission_id}/{candidate_id}"
        endpoint = handle.metadata.get("endpoint")
        if endpoint:
            return HttpSimulationApi(
                endpoint,
                candidate_id=candidate_id,
                artifact_url_prefix=artifact_prefix,
            )
        return SubprocessOpenVSPApi(
            model_path=model_path,
            vspaero_path=vspaero_path,
            workdir=workdir / mission_id / candidate_id,
            artifact_url_prefix=artifact_prefix,
        )

    registry.register(
        AdapterManifest(
            name="openvsp-python-api",
            version="3.x",
            analyses=("geometry", "aerodynamics", "stability", "structures"),
            input_parameters=tuple(OpenVSPDirectApi.DEFAULT_PARAMETER_MAP),
            output_metrics=("geometry_valid", "L_D", "CL", "CD", "CMy", "static_margin", "mass", "cg_x", "cg_y", "cg_z"),
            artifact_types=("model/openvsp-vsp3", "model/obj", "polar/vspaero", "log/vspaero"),
            concurrency=concurrency,
            parameter_specs=OpenVSPDirectApi.PARAMETER_SPECS,
            metric_specs=OpenVSPDirectApi.METRIC_SPECS,
            domain_dependencies={
                "aerodynamics": ("geometry",),
                "stability": ("geometry", "aerodynamics"),
                "structures": ("geometry",),
            },
        ),
        openvsp_factory,
    )
    return registry


def _vm_provider(mission_id: str):
    workdir = Path(os.environ.get("CHIEF_ENGINEER_WORKDIR", "./chief-engineer-runs")).resolve()
    if _worker_provider_name() == "docker":
        return DockerVmProvider(
            image=os.environ.get("CHIEF_WORKER_IMAGE", "hacknation-openvsp-api-worker:3.51"),
            workspace_root=workdir / mission_id,
            startup_timeout_s=float(os.environ.get("CHIEF_WORKER_STARTUP_TIMEOUT_S", "45")),
            platform=os.environ.get("CHIEF_WORKER_PLATFORM", "linux/amd64"),
        )
    return LocalVmProvider(root=str(workdir / mission_id / "worker-slots"))


def _record(mission_id: str) -> MissionRecord | None:
    with _missions_lock:
        return _missions.get(mission_id)


def _mission_path(path: str) -> tuple[str | None, str | None]:
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["api", "missions"]:
        return parts[2], parts[3] if len(parts) > 3 else ""
    return None, None


def _artifact_path(path: str) -> Path | None:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:2] != ["api", "artifacts"]:
        return None
    mission_id, candidate_id, filename = parts[2:]
    if not all(part and part.replace("-", "").replace("_", "").isalnum() for part in (mission_id, candidate_id)):
        return None
    if filename not in {"geometry.obj", "design.vsp3", "candidate.polar", "solver.log"}:
        return None
    root = Path(os.environ.get("CHIEF_ENGINEER_WORKDIR", "./chief-engineer-runs")).resolve()
    candidate_root = (root / mission_id / candidate_id).resolve()
    artifact = (candidate_root / filename).resolve()
    if candidate_root not in artifact.parents:
        return None
    return artifact


def _state_root() -> Path:
    workdir = Path(os.environ.get("CHIEF_ENGINEER_WORKDIR", "./chief-engineer-runs")).resolve()
    root = Path(os.environ.get("CHIEF_ENGINEER_STATE_DIR", str(workdir / "mission-state"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _events_path(mission_id: str) -> Path:
    return _state_root() / f"{mission_id}.events.jsonl"


def _save_record(record: MissionRecord) -> None:
    path = _state_root() / f"{record.id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record.public(), separators=(",", ":")))
    temporary.replace(path)


def _restore_missions() -> None:
    with _missions_lock:
        if _missions:
            return
        for path in sorted(_state_root().glob("m-*.json")):
            try:
                item = json.loads(path.read_text())
                mission_id = str(item["mission_id"])
                bus = EventBus(mission_id, _events_path(mission_id))
                record = MissionRecord(
                    id=mission_id,
                    request=str(item.get("request", "")),
                    bus=bus,
                    state=str(item.get("state", "failed")),
                    created_at=float(item.get("created_at", path.stat().st_mtime)),
                    started_at=item.get("started_at"),
                    finished_at=item.get("finished_at"),
                    result_snapshot=item.get("result"),
                    error=item.get("error"),
                )
                if record.state in {"queued", "running"}:
                    record.state = "failed"
                    record.error = "Mission interrupted by a service restart. Existing evidence remains replayable."
                    record.finished_at = time.time()
                    bus.publish("mission.failed", {"reason": record.error})
                    _save_record(record)
                bus.close()
                _missions[mission_id] = record
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                continue


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    parsed = default if value in (None, "") else int(value)
    return max(minimum, min(maximum, parsed))


def _backend_name() -> str:
    if not os.environ.get("MODEL_PATH"):
        return "synthetic"
    return "openvsp-container-workers" if _worker_provider_name() == "docker" else "openvsp-api-workers"


def _worker_provider_name() -> str:
    return os.environ.get("CHIEF_WORKER_PROVIDER", "local").strip().lower()


def _reasoning_name() -> str:
    configured = all(os.environ.get(key) for key in (
        "CHIEF_REASONING_BASE_URL",
        "CHIEF_REASONING_API_KEY",
        "CHIEF_REASONING_MODEL",
    ))
    return "openai-compatible" if configured else "deterministic-chief"


def main() -> None:
    _restore_missions()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Hacknation mission control listening on :{PORT} ({_backend_name()})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
