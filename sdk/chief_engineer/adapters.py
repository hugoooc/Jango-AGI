"""Capability registry for direct engineering-software APIs.

The canonical handoff between disciplines is a JSON-compatible design state
plus structured metrics. Adapters can wrap Python APIs, HTTP services, or
solver-specific RPC workers without leaking those details into the chief.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .api import SimulationApi
from .fleet import VmHandle
from .models import MetricSpec, ParameterSpec, domain_name


@dataclass(frozen=True)
class AdapterManifest:
    name: str
    version: str
    analyses: tuple[str, ...]
    input_parameters: tuple[str, ...]
    output_metrics: tuple[str, ...]
    artifact_types: tuple[str, ...] = ("design-state/json",)
    concurrency: str = "isolated-process"
    parameter_specs: tuple[ParameterSpec, ...] = ()
    metric_specs: tuple[MetricSpec, ...] = ()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "analyses": list(self.analyses),
            "input_parameters": list(self.input_parameters),
            "output_metrics": list(self.output_metrics),
            "artifact_types": list(self.artifact_types),
            "concurrency": self.concurrency,
            "parameters": [
                {
                    "name": item.name,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "relative_step": item.relative_step,
                    "unit": item.unit,
                    "description": item.description,
                    "domains": [domain_name(domain) for domain in item.domains],
                }
                for item in self.parameter_specs
            ],
            "metrics": [
                {
                    "name": item.name,
                    "analysis": item.analysis,
                    "default_direction": item.default_direction,
                    "aliases": list(item.aliases),
                    "unit": item.unit,
                    "domains": [domain_name(domain) for domain in item.domains],
                }
                for item in self.metric_specs
            ],
        }


AdapterFactory = Callable[[VmHandle], SimulationApi]


class SoftwareAdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, tuple[AdapterManifest, AdapterFactory]] = {}

    def register(self, manifest: AdapterManifest, factory: AdapterFactory) -> None:
        if manifest.name in self._adapters:
            raise ValueError(f"adapter {manifest.name!r} is already registered")
        self._adapters[manifest.name] = (manifest, factory)

    def manifests(self) -> list[AdapterManifest]:
        return [item[0] for item in self._adapters.values()]

    def parameter_specs(self) -> tuple[ParameterSpec, ...]:
        by_name = {}
        for manifest, _factory in self._adapters.values():
            for item in manifest.parameter_specs:
                by_name[item.name] = item
        return tuple(by_name.values())

    def metric_specs(self) -> tuple[MetricSpec, ...]:
        by_name = {}
        for manifest, _factory in self._adapters.values():
            for item in manifest.metric_specs:
                by_name[item.name] = item
        return tuple(by_name.values())

    def factory_for(self, analysis: str) -> tuple[AdapterManifest, AdapterFactory]:
        for manifest, factory in self._adapters.values():
            if analysis in manifest.analyses:
                return manifest, factory
        raise LookupError(f"no direct API adapter provides analysis {analysis!r}")

    def composite_factory(self) -> AdapterFactory:
        return lambda handle: CompositeSimulationApi(self, handle)


class CompositeSimulationApi:
    """Route one multidisciplinary evaluation across registered API adapters."""

    def __init__(self, registry: SoftwareAdapterRegistry, handle: VmHandle):
        self.registry = registry
        self.handle = handle
        self._instances: list[SimulationApi] = []
        self._progress_sink = None

    def set_progress_sink(self, sink) -> None:
        self._progress_sink = sink

    def evaluate(self, design: Mapping[str, float], analyses: Sequence[str]) -> Mapping[str, float]:
        grouped: dict[str, tuple[AdapterManifest, AdapterFactory, list[str]]] = {}
        for analysis in analyses:
            manifest, factory = self.registry.factory_for(analysis)
            if manifest.name not in grouped:
                grouped[manifest.name] = (manifest, factory, [])
            grouped[manifest.name][2].append(analysis)

        metrics: dict[str, float] = {}
        for _manifest, factory, adapter_analyses in grouped.values():
            adapter = factory(self.handle)
            self._instances.append(adapter)
            progress_setter = getattr(adapter, "set_progress_sink", None)
            if callable(progress_setter):
                progress_setter(self._progress_sink)
            values = adapter.evaluate(design, adapter_analyses)
            overlap = set(metrics).intersection(values)
            if overlap:
                raise RuntimeError(f"multiple API adapters emitted the same metrics: {sorted(overlap)}")
            metrics.update({key: float(value) for key, value in values.items()})
        return metrics

    def close(self) -> None:
        for instance in reversed(self._instances):
            instance.close()
        self._instances.clear()

    def artifacts(self) -> list[dict]:
        artifacts = []
        for instance in self._instances:
            reader = getattr(instance, "artifacts", None)
            if callable(reader):
                artifacts.extend(reader())
        return artifacts


def synthetic_registry(factory: AdapterFactory) -> SoftwareAdapterRegistry:
    registry = SoftwareAdapterRegistry()
    registry.register(
        AdapterManifest(
            name="synthetic-flight-physics",
            version="1.0",
            analyses=("geometry", "aerodynamics", "stability", "structures"),
            input_parameters=(
                "wing_span", "wing_sweep", "wing_taper", "wing_twist",
                "htail_span", "htail_area", "htail_arm", "cg_shift", "skin_thickness",
            ),
            output_metrics=("L_D", "CL", "CD", "static_margin", "mass", "wing_span"),
            concurrency="thread-safe",
        ),
        factory,
    )
    return registry
