"""API worker fleet and VM provisioning seam."""

from __future__ import annotations

import tempfile
import time
import uuid
import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping, Protocol, Sequence

from .api import SimulationApi
from .models import Candidate, Evaluation, ExplorationPlan


@dataclass(frozen=True)
class VmSpec:
    worker_id: str
    specialist: str
    analyses: tuple[str, ...]
    image: str = "engineering-api-worker:latest"


@dataclass
class VmHandle:
    id: str
    spec: VmSpec
    workspace: Path
    state: str = "provisioned"
    metadata: dict[str, str] = field(default_factory=dict)


class VmProvider(Protocol):
    def provision(self, spec: VmSpec) -> VmHandle:
        ...

    def release(self, handle: VmHandle) -> None:
        ...


class LocalVmProvider:
    """A process-isolated development provider.

    The production implementation can replace this object with a Kubernetes,
    EC2, or Docker provider without changing the chief engineer. Each handle
    still receives its own workspace and identity, which lets the same fleet
    contract be tested locally.
    """

    def __init__(self, root: str | None = None):
        self.root = Path(root) if root else Path(tempfile.mkdtemp(prefix="jango-api-fleet-"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.provisioned: list[VmHandle] = []
        self.released: list[VmHandle] = []
        self._lock = Lock()

    def provision(self, spec: VmSpec) -> VmHandle:
        handle = VmHandle(
            id=f"vm-{spec.worker_id}-{uuid.uuid4().hex[:8]}",
            spec=spec,
            workspace=self.root / spec.worker_id,
        )
        handle.workspace.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.provisioned.append(handle)
        return handle

    def release(self, handle: VmHandle) -> None:
        handle.state = "released"
        with self._lock:
            self.released.append(handle)


class DockerVmProvider:
    """Provision real containerized API workers through the Docker CLI.

    The worker image is expected to run ``python -m chief_engineer.worker_server``
    and expose port 8080. This provider is intentionally small: a Kubernetes or
    cloud provider can implement the same two-method ``VmProvider`` contract.
    """

    def __init__(
        self,
        image: str = "hacknation-openvsp-api-worker:3.51",
        model_path: str | None = None,
        vspaero_path: str | None = None,
        port_base: int = 19080,
        startup_timeout_s: float = 30.0,
        workspace_root: str | Path | None = None,
        platform: str = "linux/amd64",
    ):
        self.image = image
        self.model_path = model_path
        self.vspaero_path = vspaero_path
        self.port_base = port_base
        self.startup_timeout_s = startup_timeout_s
        self._counter = 0
        self.workspace_root = Path(workspace_root or tempfile.mkdtemp(prefix="jango-container-runs-")).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.platform = platform

    def provision(self, spec: VmSpec) -> VmHandle:
        self._counter += 1
        name = f"jango-{spec.worker_id}-{uuid.uuid4().hex[:8]}"
        command = [
            "docker", "run", "-d", "--rm", "--name", name,
            "--platform", self.platform,
            "-p", "127.0.0.1::8080",
            "-v", f"{self.workspace_root}:/worker-runs",
            "--label", "jango.worker=true",
            "-e", f"WORKER_ID={spec.worker_id}",
            "-e", "WORKER_PORT=8080",
        ]
        if self.model_path:
            command.extend(["-e", f"MODEL_PATH={self.model_path}"])
        if self.vspaero_path:
            command.extend(["-e", f"VSPAERO_PATH={self.vspaero_path}"])
        command.append(self.image)
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("DockerVmProvider requires the docker CLI") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"could not start API worker: {exc.stderr.strip()}") from exc

        try:
            port_result = subprocess.run(
                ["docker", "port", name, "8080/tcp"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
            detail = getattr(exc, "stderr", "") or str(exc)
            raise RuntimeError(f"could not discover API worker port: {detail.strip()}") from exc
        binding = port_result.stdout.strip().splitlines()[0]
        port = int(binding.rsplit(":", 1)[-1])
        endpoint = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint + "/health", timeout=1.0) as response:
                    if response.status == 200:
                        return VmHandle(
                            id=f"vm-{spec.worker_id}-{uuid.uuid4().hex[:8]}",
                            spec=spec,
                            workspace=self.workspace_root,
                            metadata={
                                "container": name,
                                "endpoint": endpoint,
                                "runtime": "docker-container",
                                "image": self.image,
                                "platform": self.platform,
                            },
                        )
            except OSError:
                time.sleep(0.25)
        self.release(VmHandle(name, spec, Path("/worker"), metadata={"container": name}))
        raise RuntimeError(f"API worker {name} did not become healthy")

    def release(self, handle: VmHandle) -> None:
        container = handle.metadata.get("container", handle.id)
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, text=True)
        handle.state = "released"


@dataclass(frozen=True)
class WorkerTask:
    candidate: Candidate
    specialist: str
    analyses: tuple[str, ...]


ApiFactory = Callable[[VmHandle], SimulationApi]
FleetEventSink = Callable[[str, dict], None]


class ApiFleet:
    """Provision one API worker per concurrent slot and collect results."""

    def __init__(
        self,
        provider: VmProvider,
        api_factory: ApiFactory,
        max_workers: int = 64,
        event_sink: FleetEventSink | None = None,
    ):
        self.provider = provider
        self.api_factory = api_factory
        self.max_workers = max(1, max_workers)
        self.event_sink = event_sink

    def evaluate(self, tasks: Sequence[WorkerTask], plan: ExplorationPlan) -> list[Evaluation]:
        if not tasks:
            return []
        worker_count = min(len(tasks), plan.worker_count, self.max_workers)
        handles = []
        for index in range(worker_count):
            task = tasks[index]
            handle = self.provider.provision(VmSpec(f"worker-{index:02d}", task.specialist, task.analyses))
            handles.append(handle)
            self._emit("worker.provisioned", {
                "worker_id": handle.id,
                "specialist": task.specialist,
                "workspace": str(handle.workspace),
                "runtime": handle.metadata.get("runtime", "local-process"),
                "container": handle.metadata.get("container"),
                "image": handle.metadata.get("image"),
            })
        buckets = [list(tasks[index::worker_count]) for index in range(worker_count)]
        results: list[Evaluation] = []
        try:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="api-worker") as pool:
                futures = [pool.submit(self._run_slot, handle, bucket) for handle, bucket in zip(handles, buckets)]
                for future in as_completed(futures):
                    results.extend(future.result())
        finally:
            for handle in handles:
                self.provider.release(handle)
                self._emit("worker.released", {"worker_id": handle.id})
        return sorted(results, key=lambda result: result.candidate_id)

    def _run_slot(self, handle: VmHandle, tasks: Sequence[WorkerTask]) -> list[Evaluation]:
        return [self._run_one(task, handle) for task in tasks]

    def _run_one(self, task: WorkerTask, handle: VmHandle) -> Evaluation:
        started = time.monotonic()
        api: SimulationApi | None = None
        try:
            handle.metadata["candidate_id"] = task.candidate.id
            self._emit("agent.started", {
                "agent_id": task.candidate.id,
                "worker_id": handle.id,
                "specialist": task.specialist,
                "analyses": list(task.analyses),
                "rationale": task.candidate.rationale,
            })
            api = self.api_factory(handle)
            progress_setter = getattr(api, "set_progress_sink", None)
            if callable(progress_setter):
                progress_setter(lambda payload: self._emit("agent.progress", {
                    **dict(payload),
                    "agent_id": task.candidate.id,
                    "worker_id": handle.id,
                }))
            metrics = dict(api.evaluate(task.candidate.design, task.analyses))
            artifact_reader = getattr(api, "artifacts", None)
            artifacts = list(artifact_reader()) if callable(artifact_reader) else []
            result = Evaluation(
                candidate_id=task.candidate.id,
                worker_id=handle.id,
                metrics={key: float(value) for key, value in metrics.items()},
                elapsed_s=round(time.monotonic() - started, 4),
                artifacts=artifacts,
            )
            self._emit("agent.completed", {
                "agent_id": task.candidate.id,
                "worker_id": handle.id,
                "metrics": result.metrics,
                "elapsed_s": result.elapsed_s,
                "artifacts": result.artifacts,
            })
            return result
        except Exception as exc:
            result = Evaluation(
                candidate_id=task.candidate.id,
                worker_id=handle.id,
                elapsed_s=round(time.monotonic() - started, 4),
                error=f"{type(exc).__name__}: {exc}",
            )
            self._emit("agent.failed", {
                "agent_id": task.candidate.id,
                "worker_id": handle.id,
                "error": result.error,
            })
            return result
        finally:
            if api is not None:
                api.close()

    def _emit(self, event: str, payload: dict) -> None:
        if self.event_sink:
            self.event_sink(event, payload)
