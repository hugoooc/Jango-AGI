"""Direct software-API adapters used by chief-engineer workers.

There is intentionally no desktop, screenshot, Holo, or computer-use import in
this module.  ``SimulationApi`` is the seam for OpenVSP, CFD, CAD, FEA, or any
other engineering tool that exposes a programmable interface.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .models import Domain, MetricSpec, ParameterSpec


ProgressSink = Callable[[dict[str, Any]], None]


class SimulationApi(Protocol):
    """Minimal contract a worker needs from a specialized engineering API."""

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        ...

    def close(self) -> None:
        ...

    def artifacts(self) -> list[dict[str, Any]]:
        ...


class SyntheticApi:
    """Small deterministic backend for local development and orchestration tests.

    It is not a physics substitute.  It gives the chief-engineer loop a
    repeatable objective landscape before the real OpenVSP Python package is
    installed on a worker image.
    """

    def __init__(self, defaults: Mapping[str, float] | None = None, latency_s: float = 0.0):
        self.defaults = {
            "wing_span": 10.0,
            "wing_sweep": 25.0,
            "wing_taper": 0.55,
            "wing_twist": -1.0,
            "htail_span": 7.0,
            "htail_area": 8.0,
            "htail_arm": 5.0,
            "cg_shift": 0.0,
            "skin_thickness": 0.08,
        }
        if defaults:
            self.defaults.update({key: float(value) for key, value in defaults.items()})
        self.latency_s = max(0.0, float(latency_s))

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        if self.latency_s:
            time.sleep(self.latency_s)
        p = {**self.defaults, **{key: float(value) for key, value in design.items()}}
        aspect_ratio = p["wing_span"] ** 2 / 48.0
        induced_drag = 0.045 / max(aspect_ratio, 0.1)
        profile_drag = 0.017 + 0.00018 * (p["wing_sweep"] - 20.0) ** 2
        twist_penalty = 0.0005 * (p["wing_twist"] + 1.0) ** 2
        cd = profile_drag + induced_drag + twist_penalty
        cl = 0.52 + 0.002 * (p["wing_span"] - 10.0) - 0.001 * abs(p["wing_sweep"] - 25.0)
        lift_to_drag = cl / cd

        # Positive static margin means a restoring pitching moment.  Tail area
        # and moment arm help; aft CG shift hurts it.
        tail_effective_area = p["htail_area"] * (p["htail_span"] / self.defaults["htail_span"])
        static_margin = (
            0.015 * tail_effective_area * p["htail_arm"]
            - 0.025 * p["cg_shift"]
            - 0.06
        )
        mass = 400.0 + 18.0 * p["wing_span"] + 260.0 * tail_effective_area / 100.0
        mass += 120.0 * p["skin_thickness"]
        return {
            "L_D": round(lift_to_drag, 6),
            "CL": round(cl, 6),
            "CD": round(cd, 6),
            "static_margin": round(static_margin, 6),
            "mass": round(mass, 6),
            "wing_span": p["wing_span"],
        }

    def close(self) -> None:
        return None

    def artifacts(self) -> list[dict[str, Any]]:
        return []


class HttpSimulationApi:
    """Client used by a controller when a worker lives in a VM/container."""

    def __init__(
        self,
        endpoint: str,
        candidate_id: str = "candidate",
        timeout_s: float = 900.0,
        artifact_url_prefix: str | None = None,
        poll_interval_s: float = 0.12,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.candidate_id = candidate_id
        self.timeout_s = timeout_s
        self.artifact_url_prefix = artifact_url_prefix.rstrip("/") if artifact_url_prefix else None
        self.poll_interval_s = poll_interval_s
        self._progress_sink: ProgressSink | None = None
        self._artifacts: list[dict[str, Any]] = []

    def set_progress_sink(self, sink: ProgressSink | None) -> None:
        self._progress_sink = sink

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        payload = json.dumps({
            "candidate_id": self.candidate_id,
            "design": dict(design),
            "analyses": list(analyses),
        }).encode()
        request = urllib.request.Request(
            self.endpoint + "/evaluations",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=min(30.0, self.timeout_s)) as response:
            accepted = json.loads(response.read())
        if accepted.get("error"):
            raise RuntimeError(accepted["error"])
        job_id = accepted.get("job_id")
        if not job_id:
            raise RuntimeError("API worker returned no evaluation id")

        cursor = 0
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            with urllib.request.urlopen(
                f"{self.endpoint}/evaluations/{job_id}?after={cursor}",
                timeout=min(30.0, self.timeout_s),
            ) as response:
                body = json.loads(response.read())
            for item in body.get("progress", []):
                payload = self._public_artifact(dict(item))
                if self._progress_sink:
                    self._progress_sink(payload)
            cursor = int(body.get("progress_cursor", cursor))
            if body.get("state") == "complete":
                self._artifacts = [
                    self._public_artifact({"artifact": item})["artifact"]
                    for item in body.get("artifacts", [])
                ]
                return body.get("metrics", {})
            if body.get("state") == "failed":
                raise RuntimeError(body.get("error", "container worker failed"))
            time.sleep(self.poll_interval_s)
        raise TimeoutError(f"container worker exceeded {self.timeout_s:.0f}s")

    def _public_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            artifact = dict(artifact)
            if self.artifact_url_prefix and artifact.get("name"):
                artifact["url"] = f"{self.artifact_url_prefix}/{artifact['name']}"
            artifact.pop("path", None)
            payload["artifact"] = artifact
        return payload

    def close(self) -> None:
        return None

    def artifacts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._artifacts]


class SubprocessOpenVSPApi:
    """Crash-contained OpenVSP worker using one operating-system process per design.

    OpenVSP exposes global model state and malformed solver geometry can abort the
    interpreter.  A subprocess is therefore the real local equivalent of an
    isolated VM worker: parallel candidates cannot corrupt each other or the
    chief-engineer service.
    """

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        vspaero_path: str | os.PathLike[str] | None,
        workdir: str | os.PathLike[str],
        *,
        python_executable: str | os.PathLike[str] | None = None,
        artifact_url_prefix: str | None = None,
        timeout_s: float = 900.0,
    ):
        self.model_path = str(Path(model_path).resolve())
        self.vspaero_path = str(Path(vspaero_path).resolve()) if vspaero_path else None
        self.workdir = Path(workdir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.artifact_url_prefix = artifact_url_prefix.rstrip("/") if artifact_url_prefix else None
        self.timeout_s = timeout_s
        self._artifacts: list[dict[str, Any]] = []
        self._progress_sink: ProgressSink | None = None

    def set_progress_sink(self, sink: ProgressSink | None) -> None:
        self._progress_sink = sink

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        request_path = self.workdir / "request.json"
        response_path = self.workdir / "response.json"
        progress_path = self.workdir / "progress.jsonl"
        log_path = self.workdir / "solver.log"
        for stale in (response_path, progress_path):
            stale.unlink(missing_ok=True)
        request_path.write_text(json.dumps({
            "model_path": self.model_path,
            "vspaero_path": self.vspaero_path,
            "workdir": str(self.workdir),
            "design": dict(design),
            "analyses": list(analyses),
            "progress_path": str(progress_path),
        }))
        command = [
            self.python_executable,
            "-m",
            "chief_engineer.worker_process",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
        progress_offset = 0
        deadline = time.monotonic() + self.timeout_s
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                progress_offset = self._drain_progress(progress_path, progress_offset)
                if time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise TimeoutError(f"OpenVSP worker exceeded {self.timeout_s:.0f}s")
                time.sleep(0.08)
            progress_offset = self._drain_progress(progress_path, progress_offset)
        if process.returncode != 0:
            if response_path.exists():
                response = json.loads(response_path.read_text())
                if response.get("error"):
                    raise RuntimeError(response["error"])
            tail = _tail(log_path)
            raise RuntimeError(
                f"OpenVSP worker exited with code {process.returncode}"
                + (f": {tail}" if tail else "")
            )
        if not response_path.exists():
            raise RuntimeError("OpenVSP worker returned no response")
        response = json.loads(response_path.read_text())
        if response.get("error"):
            raise RuntimeError(response["error"])
        self._artifacts = []
        for artifact in response.get("artifacts", []):
            item = dict(artifact)
            if self.artifact_url_prefix and item.get("name"):
                item["url"] = f"{self.artifact_url_prefix}/{item['name']}"
            item.pop("path", None)
            self._artifacts.append(item)
        return response.get("metrics", {})

    def _drain_progress(self, path: Path, offset: int) -> int:
        if not path.exists():
            return offset
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read()
            new_offset = stream.tell()
        for line in data.splitlines():
            try:
                payload = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            artifact = payload.get("artifact")
            if isinstance(artifact, dict):
                artifact = dict(artifact)
                if self.artifact_url_prefix and artifact.get("name"):
                    artifact["url"] = f"{self.artifact_url_prefix}/{artifact['name']}"
                artifact.pop("path", None)
                payload["artifact"] = artifact
            if self._progress_sink:
                self._progress_sink(payload)
        return new_offset

    def artifacts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._artifacts]

    def close(self) -> None:
        return None


class OpenVSPDirectApi:
    """OpenVSP Python API adapter for isolated worker processes.

    Each instance owns one OpenVSP API context.  The fleet creates one instance
    per VM/worker, so model state cannot leak between parallel candidates.
    ``openvsp`` is imported lazily because the rest of the orchestration stack
    remains installable and testable without OpenVSP on the controller machine.
    """

    DEFAULT_PARAMETER_MAP = {
        "wing_span": ("Wing", "Span", "XSec_1"),
        "wing_sweep": ("Wing", "Sweep", "XSec_1"),
        "wing_taper": ("Wing", "Taper", "XSec_1"),
        "wing_twist": ("Wing", "Twist", "XSec_1"),
        "wing_root_chord": ("Wing", "Root_Chord", "XSec_1"),
        "htail_span": ("horizontal stabilizer", "Span", "XSec_1"),
        "htail_arm": ("horizontal stabilizer", "X_Rel_Location", "XForm"),
        "wing_density": ("Wing", "Density", "Mass_Props"),
        "fuselage_density": ("fuselage", "Density", "Mass_Props"),
        "htail_density": ("horizontal stabilizer", "Density", "Mass_Props"),
        "vtail_density": ("vertical stabilizer", "Density", "Mass_Props"),
    }

    PARAMETER_SPECS = (
        ParameterSpec("wing_span", 2.5, 15.0, 0.08, "m", "Primary wing semi-span", (Domain.GEOMETRY, Domain.AERODYNAMICS, Domain.STRUCTURES)),
        ParameterSpec("wing_sweep", 0.0, 60.0, 0.12, "deg", "Primary wing sweep", (Domain.GEOMETRY, Domain.AERODYNAMICS)),
        ParameterSpec("wing_taper", 0.20, 1.0, 0.12, "ratio", "Primary wing taper ratio", (Domain.GEOMETRY, Domain.AERODYNAMICS, Domain.STRUCTURES)),
        ParameterSpec("wing_twist", -8.0, 8.0, 0.15, "deg", "Primary wing twist", (Domain.GEOMETRY, Domain.AERODYNAMICS, Domain.STABILITY)),
        ParameterSpec("wing_root_chord", 2.0, 14.0, 0.08, "m", "Primary wing root chord", (Domain.GEOMETRY, Domain.AERODYNAMICS, Domain.STRUCTURES)),
        ParameterSpec("htail_span", 2.0, 14.0, 0.10, "m", "Horizontal-tail semi-span", (Domain.GEOMETRY, Domain.STABILITY, Domain.STRUCTURES)),
        ParameterSpec("htail_arm", 15.0, 45.0, 0.06, "m", "Horizontal-tail longitudinal position", (Domain.GEOMETRY, Domain.STABILITY)),
        ParameterSpec("wing_density", 0.10, 5.0, 0.10, "mass/volume", "Wing material density", (Domain.STRUCTURES,)),
        ParameterSpec("fuselage_density", 0.10, 5.0, 0.10, "mass/volume", "Fuselage material density", (Domain.STRUCTURES,)),
        ParameterSpec("htail_density", 0.10, 5.0, 0.10, "mass/volume", "Horizontal-tail material density", (Domain.STRUCTURES,)),
        ParameterSpec("vtail_density", 0.10, 5.0, 0.10, "mass/volume", "Vertical-tail material density", (Domain.STRUCTURES,)),
    )

    METRIC_SPECS = (
        MetricSpec("L_D", "aerodynamics", "max", ("l/d", "lift to drag", "aerodynamic efficiency"), "ratio", (Domain.AERODYNAMICS, Domain.GEOMETRY)),
        MetricSpec("CD", "aerodynamics", "min", ("cd", "drag coefficient", "drag"), "coefficient", (Domain.AERODYNAMICS, Domain.GEOMETRY)),
        MetricSpec("CL", "aerodynamics", "max", ("cl", "lift coefficient", "lift"), "coefficient", (Domain.AERODYNAMICS, Domain.GEOMETRY)),
        MetricSpec("static_margin", "stability", "max", ("static margin", "stability margin", "stable", "stability"), "slope/deg", (Domain.STABILITY, Domain.GEOMETRY)),
        MetricSpec("mass", "structures", "min", ("mass", "weight", "total mass"), "model mass", (Domain.STRUCTURES, Domain.GEOMETRY)),
        MetricSpec("cg_x", "structures", "min", ("x cg", "cg x", "longitudinal cg", "center of gravity"), "m", (Domain.STRUCTURES,)),
        MetricSpec("cg_y", "structures", "min", ("y cg", "cg y", "lateral cg"), "m", (Domain.STRUCTURES,)),
        MetricSpec("cg_z", "structures", "min", ("z cg", "cg z", "vertical cg"), "m", (Domain.STRUCTURES,)),
    )

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        vspaero_path: str | os.PathLike[str] | None = None,
        workdir: str | os.PathLike[str] | None = None,
        parameter_map: Mapping[str, tuple[str, str, str]] | None = None,
        progress_sink: ProgressSink | None = None,
    ):
        self.model_path = str(model_path)
        self.vspaero_path = str(vspaero_path) if vspaero_path else None
        self.workdir = Path(workdir or Path.cwd())
        self.parameter_map = dict(self.DEFAULT_PARAMETER_MAP)
        if parameter_map:
            self.parameter_map.update(parameter_map)
        self._vsp = None
        self._artifacts: list[dict[str, Any]] = []
        self._progress_sink = progress_sink

    @property
    def vsp(self):
        if self._vsp is None:
            try:
                import openvsp as vsp  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "OpenVSPDirectApi requires the OpenVSP Python package on the worker"
                ) from exc
            self._vsp = vsp
        return self._vsp

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        self._progress("loading-model", 0.04, "Loading source OpenVSP model")
        vsp = self.vsp
        vsp.ClearVSPModel()
        vsp.ReadVSPFile(self.model_path)
        self._progress("model-loaded", 0.12, "Source model loaded and indexed")
        for key, value in design.items():
            try:
                geom_name, parm, group = self.parameter_map[key]
            except KeyError as exc:
                raise ValueError(f"no OpenVSP parameter mapping for {key!r}") from exc
            geom_id = self._find_geom(geom_name)
            vsp.SetParmVal(geom_id, parm, group, float(value))
        vsp.Update()
        self._progress("parameters-applied", 0.23, f"Applied {len(design)} design parameters")

        self.workdir.mkdir(parents=True, exist_ok=True)
        base = self.workdir / "candidate"
        design_path = self.workdir / "design.vsp3"
        vsp.WriteVSPFile(str(design_path), vsp.SET_ALL)
        obj_path = self.workdir / "geometry.obj"
        mesh_id = vsp.ExportFile(str(obj_path), vsp.SET_ALL, vsp.EXPORT_OBJ)
        if mesh_id:
            vsp.DeleteGeom(mesh_id)
        self._artifacts = [
            _artifact(str(design_path), "OpenVSP design", "model/openvsp-vsp3"),
            _artifact(str(obj_path), "Interactive geometry", "model/obj"),
        ]
        self._progress(
            "geometry-exported",
            0.36,
            "Geometry exported; aerodynamic preparation can begin",
            artifact=self._artifacts[1],
        )

        requested = set(analyses)
        results: dict[str, float] = {}
        # Mass properties must be evaluated on the complete design.  The
        # aerodynamic adapter intentionally removes nested engine bodies from
        # its in-memory VLM model because they crash VSPAERO's mesher, so any
        # whole-aircraft analyses have to run before that destructive solver
        # preparation step.
        if "structures" in requested:
            self._progress("mass-properties", 0.42, "Evaluating whole-aircraft mass properties")
            results.update(self._run_mass_properties(vsp))
        if requested.intersection({"aerodynamics", "stability"}):
            results.update(self._run_vspaero(vsp, requested))
        if "geometry" in requested:
            results["geometry_valid"] = 1.0
        # Return the canonical design state, including values inherited from
        # the source model. This lets the next specialist modify the actual
        # geometry instead of relying on synthetic defaults.
        for key, (geom_name, parm, group) in self.parameter_map.items():
            try:
                results[key] = float(vsp.GetParmVal(self._find_geom(geom_name), parm, group))
            except (ValueError, RuntimeError):
                continue
        self._progress("evidence-ready", 1.0, "Structured metrics and artifacts ready")
        return results

    def _find_geom(self, name: str) -> str:
        vsp = self.vsp
        for geom_id in vsp.FindGeoms():
            if vsp.GetGeomName(geom_id).lower() == name.lower():
                return geom_id
        raise ValueError(f"OpenVSP geometry {name!r} was not found in {self.model_path}")

    def _run_vspaero(self, vsp, requested: set[str]) -> dict[str, float]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        base = self.workdir / "candidate"
        # The full transport model includes nested engine bodies that crash the
        # VSPAERO mesher. Analyze its aerodynamic lifting surfaces as a VLM set,
        # while preserving the complete model and OBJ artifacts written above.
        for geom_id in list(vsp.FindGeoms()):
            if vsp.GetGeomName(geom_id).lower() == "engine":
                vsp.DeleteGeom(geom_id)
        vsp.Update()
        thin_set = 3
        lifting_surfaces = {"wing", "horizontal stabilizer", "vertical stabilizer"}
        for geom_id in vsp.FindGeoms():
            vsp.SetSetFlag(
                geom_id,
                thin_set,
                vsp.GetGeomName(geom_id).lower() in lifting_surfaces,
            )
        # VSPAERO names its result files from the currently written model.
        vsp.WriteVSPFile(str(base) + ".vsp3", vsp.SET_ALL)
        self._progress("aero-prepared", 0.46, "Lifting surfaces isolated for VLM analysis")
        if self.vspaero_path:
            vsp.SetVSPAEROPath(self.vspaero_path)
        vsp.SetAnalysisInputDefaults("VSPAEROComputeGeometry")
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "GeomSet", [-1])
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "ThinGeomSet", [thin_set])
        self._progress("meshing", 0.54, "Generating VSPAERO computational geometry")
        vsp.ExecAnalysis("VSPAEROComputeGeometry")
        self._progress("mesh-ready", 0.64, "Aerodynamic mesh complete")
        vsp.SetAnalysisInputDefaults("VSPAEROSweep")
        vsp.SetIntAnalysisInput("VSPAEROSweep", "GeomSet", [-1])
        vsp.SetIntAnalysisInput("VSPAEROSweep", "ThinGeomSet", [thin_set])
        vsp.SetDoubleAnalysisInput("VSPAEROSweep", "AlphaStart", [0.0])
        vsp.SetIntAnalysisInput("VSPAEROSweep", "AlphaNpts", [9])
        vsp.SetDoubleAnalysisInput("VSPAEROSweep", "AlphaEnd", [8.0])
        vsp.SetDoubleAnalysisInput("VSPAEROSweep", "MachStart", [0.1])
        vsp.SetIntAnalysisInput("VSPAEROSweep", "MachNpts", [1])
        self._progress("solving", 0.69, "Sweeping 9 angles of attack through VSPAERO")
        vsp.ExecAnalysis("VSPAEROSweep")
        self._progress("post-processing", 0.94, "Parsing polar and stability derivatives")
        rows = _read_polar(str(base) + ".polar")
        if not rows:
            raise RuntimeError(f"VSPAERO produced no polar rows in {base}.polar")
        best = max(rows, key=lambda row: row.get("L_D", float("-inf")))
        output = {key: value for key, value in best.items() if isinstance(value, float)}
        if "stability" in requested and len(rows) >= 2:
            first, last = rows[0], rows[-1]
            delta_alpha = last.get("alpha", 0.0) - first.get("alpha", 0.0)
            if delta_alpha:
                slope = (last.get("CMy", 0.0) - first.get("CMy", 0.0)) / delta_alpha
                output["static_margin"] = round(-slope, 8)
        polar_path = str(base) + ".polar"
        if Path(polar_path).exists():
            self._artifacts.append(_artifact(polar_path, "VSPAERO polar", "text/vspaero-polar"))
        log_path = self.workdir / "solver.log"
        if log_path.exists():
            self._artifacts.append(_artifact(log_path, "Solver log", "text/plain"))
        return output

    def _run_mass_properties(self, vsp) -> dict[str, float]:
        vsp.SetAnalysisInputDefaults("MassProp")
        result_id = vsp.ExecAnalysis("MassProp")
        output: dict[str, float] = {}
        for key in ("Total_Mass", "X_Cg", "Y_Cg", "Z_Cg"):
            try:
                values = vsp.GetDoubleResults(result_id, key)
                if values:
                    output[key] = float(values[0])
            except (AttributeError, RuntimeError):
                # OpenVSP versions differ in result field names.  Keep the
                # adapter usable for aero-only runs and expose what is present.
                continue
        canonical = {
            "Total_Mass": "mass",
            "X_Cg": "cg_x",
            "Y_Cg": "cg_y",
            "Z_Cg": "cg_z",
        }
        for source, target in canonical.items():
            if source in output:
                output[target] = output[source]
        return output

    def close(self) -> None:
        if self._vsp is not None:
            self._vsp.ClearVSPModel()

    def artifacts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._artifacts if Path(str(item.get("path", ""))).exists()]

    def _progress(
        self,
        phase: str,
        progress: float,
        detail: str,
        *,
        artifact: dict[str, Any] | None = None,
    ) -> None:
        if not self._progress_sink:
            return
        payload: dict[str, Any] = {
            "phase": phase,
            "progress": max(0.0, min(1.0, float(progress))),
            "detail": detail,
        }
        if artifact:
            payload["artifact"] = dict(artifact)
        self._progress_sink(payload)


def _artifact(path: str, label: str, kind: str) -> dict[str, Any]:
    file_path = Path(path)
    return {
        "name": file_path.name,
        "label": label,
        "kind": kind,
        "path": str(file_path.resolve()),
        "bytes": file_path.stat().st_size if file_path.exists() else 0,
    }


def _tail(path: Path, limit: int = 1200) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode(errors="replace").strip().replace("\n", " ")


def _read_polar(path: str) -> list[dict[str, float]]:
    """Read the whitespace-delimited polar emitted by VSPAERO."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    with file_path.open(newline="") as stream:
        rows = [line.split() for line in stream if line.strip()]
    header_index = next(
        (index for index, row in enumerate(rows) if "L/D" in row and "CLtot" in row),
        None,
    )
    if header_index is None:
        return []
    header = rows[header_index]
    columns = {name: index for index, name in enumerate(header)}
    names = {"AoA": "alpha", "CLtot": "CL", "CDtot": "CD", "L/D": "L_D", "CMytot": "CMy"}
    parsed = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max(columns.values()):
            continue
        try:
            parsed.append({target: float(row[columns[source]]) for source, target in names.items() if source in columns})
        except ValueError:
            continue
    return parsed
