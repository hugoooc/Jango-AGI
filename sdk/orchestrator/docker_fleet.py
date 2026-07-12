"""Manage isolated OpenVSP GUI workers through the local Docker CLI."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass


IMAGE = os.environ.get("LP_WORKER_IMAGE", "legacypilot-openvsp-worker:dev")
NAME_PREFIX = os.environ.get("LP_WORKER_PREFIX", "legacypilot-worker-")
API_PORT_BASE = int(os.environ.get("LP_WORKER_API_PORT_BASE", "18080"))
VNC_PORT_BASE = int(os.environ.get("LP_WORKER_VNC_PORT_BASE", "16080"))
MAX_WORKERS = int(os.environ.get("LP_MAX_WORKERS", "12"))


@dataclass(frozen=True)
class Worker:
    index: int
    name: str
    api_port: int
    vnc_port: int
    running: bool
    healthy: bool

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def viewer_url(self) -> str:
        return f"http://localhost:{self.vnc_port}/vnc.html?autoconnect=true&resize=scale"

    def json(self) -> dict:
        return {**asdict(self), "api_url": self.api_url, "viewer_url": self.viewer_url}


def _resolve_api_key() -> str | None:
    """Find the Holo key from the environment or a nearby .env, so workers get
    it even when the orchestrator was launched without exporting it."""
    for var in ("HCOMPANY_API_KEY", "HAI_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        env_path = os.path.join(here, ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith(("HCOMPANY_API_KEY=", "HAI_API_KEY=")):
                    return line.split("=", 1)[1].strip()
        here = os.path.dirname(here)
    return None


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, env=os.environ.copy()
    )


def _container_running(name: str) -> bool:
    result = _docker("inspect", "-f", "{{.State.Running}}", name, check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _healthy(api_port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/health", timeout=1.0) as response:
            payload = json.loads(response.read())
        return bool(payload.get("openvsp"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def worker(index: int) -> Worker:
    if not 1 <= index <= MAX_WORKERS:
        raise ValueError(f"worker index must be between 1 and {MAX_WORKERS}")
    name = f"{NAME_PREFIX}{index}"
    running = _container_running(name)
    api_port = API_PORT_BASE + index - 1
    return Worker(
        index=index,
        name=name,
        api_port=api_port,
        vnc_port=VNC_PORT_BASE + index - 1,
        running=running,
        healthy=running and _healthy(api_port),
    )


def list_workers() -> list[Worker]:
    return [item for index in range(1, MAX_WORKERS + 1) if (item := worker(index)).running]


def start_worker(index: int) -> Worker:
    current = worker(index)
    if current.running:
        return current
    _docker("rm", "-f", current.name, check=False)
    command = [
        "run", "-d", "--platform", "linux/amd64",
        "--name", current.name,
        "--label", "com.legacypilot.openvsp-worker=true",
        "-p", f"127.0.0.1:{current.api_port}:8080",
        "-p", f"127.0.0.1:{current.vnc_port}:6080",
        "-e", f"WORKER_ID=worker-{index}",
    ]
    api_key = _resolve_api_key()
    if api_key:
        command.extend(["-e", f"HCOMPANY_API_KEY={api_key}"])
    command.append(IMAGE)
    _docker(*command)
    return worker(index)


def ensure_workers(count: int, timeout: float = 120.0) -> list[Worker]:
    if not 1 <= count <= MAX_WORKERS:
        raise ValueError(f"count must be between 1 and {MAX_WORKERS}")
    for index in range(1, count + 1):
        start_worker(index)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = [worker(index) for index in range(1, count + 1)]
        if all(item.healthy for item in result):
            return result
        time.sleep(1)
    states = [worker(index).json() for index in range(1, count + 1)]
    raise RuntimeError(f"workers did not become healthy: {states}")


def stop_all() -> None:
    result = _docker(
        "ps", "-aq", "--filter", "label=com.legacypilot.openvsp-worker=true", check=False
    )
    ids = result.stdout.split()
    if ids:
        _docker("rm", "-f", *ids)


def request(item: Worker, method: str, path: str, payload: dict | None = None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        item.api_url + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        if response.headers.get_content_type() == "application/json":
            return json.loads(body)
        return body


def wing_span_actions(value: float) -> list[dict]:
    """A vision-grounded GUI-only trajectory; every target comes from pixels."""
    return [
        {"type": "vision_click", "target": "the Wing row in the Geom Browser tree", "double": True},
        {"type": "sleep", "seconds": 1.0},
        {"type": "vision_click", "target": "the Plan tab in the Wing geometry editor"},
        {"type": "sleep", "seconds": 0.6},
        {"type": "vision_click", "target": "the Span numeric input in the Total Planform section"},
        {"type": "key", "key": "ctrl+a"},
        {"type": "type", "text": str(value)},
        {"type": "key", "key": "Return"},
        {"type": "sleep", "seconds": 1.0},
        {"type": "vision_assert", "assertion": (
            f"the Wing geometry editor is open on the Plan tab and the Span input visibly shows {value}"
        )},
    ]


def smoke_actions(index: int) -> list[dict]:
    """Deterministic no-key test: open a different top-level menu per worker."""
    menu_x = [31, 75, 141, 199, 258, 327, 372][(index - 1) % 7]
    return [{"type": "click", "x": menu_x, "y": 43}, {"type": "sleep", "seconds": 1}]


def wing_span_mass_actions(value: float) -> list[dict]:
    """Set the wing span, run Mass Properties, and READ the resulting mass/CG —
    all vision-grounded, GUI-only. Returns a `vision_read` value in its result."""
    return [
        {"type": "vision_click", "target": "the Wing row in the Geom Browser tree", "double": True},
        {"type": "sleep", "seconds": 1.0},
        {"type": "vision_click", "target": "the Plan tab in the Wing geometry editor"},
        {"type": "sleep", "seconds": 0.6},
        {"type": "vision_click", "target": "the Span numeric input in the Total Planform section"},
        {"type": "key", "key": "ctrl+a"},
        {"type": "type", "text": str(value)},
        {"type": "key", "key": "Return"},
        {"type": "sleep", "seconds": 1.0},
        # run Mass Properties: Analysis menu -> Mass Prop... -> Compute
        {"type": "vision_click", "target": "the Analysis menu in the top menu bar"},
        {"type": "sleep", "seconds": 0.6},
        {"type": "vision_click", "target": "the Mass Prop... item in the open Analysis menu"},
        {"type": "sleep", "seconds": 0.8},
        {"type": "vision_click", "target": "the Compute button in the Mass Properties dialog"},
        {"type": "sleep", "seconds": 1.2},
        {"type": "vision_read",
         "prompt": "Read the Mass Properties Results panel shown on screen.",
         "keys": ["Total_Mass", "X_Cg"]},
    ]


def wait_job(item: Worker, job_id: str, timeout: float = 180.0) -> dict:
    """Poll a worker's job until it settles; return the final job dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = request(item, "GET", f"/jobs/{job_id}", timeout=15)
        if isinstance(job, dict) and job.get("state") in ("done", "error"):
            return job
        time.sleep(1.5)
    return {"state": "timeout", "job_id": job_id}


def mass_from_job(job: dict):
    """Extract the mass/CG the worker read via its vision_read action."""
    for act in job.get("actions", []) or []:
        if act.get("type") == "vision_read":
            return act.get("values", {})
    return {}
