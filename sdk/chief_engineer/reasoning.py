"""Reasoning providers for autonomous mission planning and chief review.

The chief can run entirely with the inspectable rule-based provider, or use any
OpenAI-compatible text reasoning API. This layer never accepts screenshots and
never emits GUI actions; its output is a typed engineering workflow.
"""

from __future__ import annotations

import json
import os
import math
import re
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .models import DomainName, GoalSpec, domain_name


@dataclass(frozen=True)
class StageSpec:
    id: str
    name: str
    domain: DomainName
    analyses: tuple[str, ...]
    fanout_per_input: int
    keep: int
    depends_on: tuple[str, ...] = ()
    instructions: str = ""


@dataclass(frozen=True)
class MissionPlan:
    stages: tuple[StageSpec, ...]
    max_cycles: int
    worker_budget: int
    rationale: str


@dataclass(frozen=True)
class ChiefDecision:
    action: str
    rationale: str
    feedback: str


class ReasoningProvider(Protocol):
    def plan(self, goal: GoalSpec, worker_budget: int, max_cycles: int) -> MissionPlan:
        ...

    def review(
        self,
        goal: GoalSpec,
        cycle: int,
        incumbent_metrics: Mapping[str, float],
        challenger_metrics: Mapping[str, float],
        improved: bool,
        feasible: bool,
    ) -> ChiefDecision:
        ...


class RuleBasedReasoningProvider:
    """Deterministic chief logic used when no external reasoning API is set."""

    def __init__(
        self,
        domain_dependencies: Mapping[str, Sequence[str]] | None = None,
        domain_parameter_counts: Mapping[str, int] | None = None,
    ):
        self.domain_dependencies = {
            str(domain): tuple(map(str, dependencies))
            for domain, dependencies in (domain_dependencies or {}).items()
        }
        self.domain_parameter_counts = {
            str(domain): max(0, int(count))
            for domain, count in (domain_parameter_counts or {}).items()
        }

    def plan(self, goal: GoalSpec, worker_budget: int = 12, max_cycles: int = 3) -> MissionPlan:
        stages: list[StageSpec] = []
        previous: tuple[str, ...] = ()
        required_analyses = goal.analyses or tuple(domain_name(domain) for domain in goal.domains)
        domains = _ordered_domains(goal.domains, self.domain_dependencies) or list(required_analyses)

        for index, domain in enumerate(domains):
            fanout, keep = _stage_shape(
                worker_budget,
                bool(previous),
                self.domain_parameter_counts.get(domain, 1),
            )
            stage = StageSpec(
                id=_stage_id(domain, index),
                name=_stage_name(domain),
                domain=domain,
                analyses=tuple(required_analyses),
                fanout_per_input=fanout,
                keep=keep,
                depends_on=previous,
                instructions=(
                    f"Explore only adapter-declared {domain} variables, evaluate the required API analyses, "
                    "and preserve parameter and artifact provenance for the next team."
                ),
            )
            stages.append(stage)
            previous = (stage.id,)

        return MissionPlan(
            stages=tuple(stages),
            max_cycles=max(1, max_cycles),
            worker_budget=max(1, worker_budget),
            rationale=(
                f"Explore the software-declared design space for {goal.objective} through "
                f"{' → '.join(domain_name(stage.domain) for stage in stages)} specialist stages, "
                "with evidence-based handoffs and chief review."
            ),
        )

    def review(self, goal, cycle, incumbent_metrics, challenger_metrics, improved, feasible):
        if not improved:
            return ChiefDecision(
                "stop",
                "The full team loop did not beat the incumbent.",
                "Freeze the incumbent and expose the evidence for human review.",
            )
        if feasible and cycle >= goal.max_iterations:
            return ChiefDecision(
                "accept",
                "The challenger improves the objective and satisfies every active constraint.",
                "Publish the winning design and its solver evidence.",
            )
        if feasible:
            return ChiefDecision(
                "iterate",
                "The design is feasible, but another focused cycle may improve the objective.",
                "Use the winner as the new baseline; narrow geometry steps and re-run aero and stability.",
            )
        return ChiefDecision(
            "iterate",
            "The objective improved but downstream constraints are still open.",
            "Send constraint violations back to geometry and stability specialists before the next campaign.",
        )


class OpenAICompatibleReasoningProvider:
    """Text-only reasoning adapter for an OpenAI-compatible model endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, fallback=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback = fallback or RuleBasedReasoningProvider()

    @classmethod
    def from_environment(cls, fallback=None):
        base = os.environ.get("CHIEF_REASONING_BASE_URL")
        key = os.environ.get("CHIEF_REASONING_API_KEY")
        model = os.environ.get("CHIEF_REASONING_MODEL")
        if not (base and key and model):
            return None
        return cls(base, key, model, fallback=fallback)

    def plan(self, goal: GoalSpec, worker_budget: int, max_cycles: int) -> MissionPlan:
        dependencies = getattr(self.fallback, "domain_dependencies", {})
        prompt = (
            "You are a chief engineer. Build a sequential/parallel workflow using only the adapter-declared "
            f"domains {list(map(domain_name, goal.domains))} and analyses {list(goal.analyses)}. "
            f"Adapter-declared domain dependencies are {dependencies}. "
            "Return JSON with rationale and stages. "
            "Each stage needs id, name, domain, analyses, fanout_per_input, keep, depends_on, instructions. "
            f"Worker budget: {worker_budget}. Max cycles: {max_cycles}. Goal: {goal.raw_request}."
        )
        try:
            data = self._json_call(prompt)
            stages = tuple(StageSpec(
                id=str(item["id"]),
                name=str(item["name"]),
                domain=str(item["domain"]),
                analyses=tuple(str(value) for value in item.get("analyses", [])),
                fanout_per_input=max(1, int(item.get("fanout_per_input", 1))),
                keep=max(1, int(item.get("keep", 1))),
                depends_on=tuple(str(value) for value in item.get("depends_on", [])),
                instructions=str(item.get("instructions", "")),
            ) for item in data["stages"])
            _validate_model_plan(stages, goal, worker_budget)
            return MissionPlan(stages, max_cycles, worker_budget, str(data.get("rationale", "Model-authored plan")))
        except Exception:
            return self.fallback.plan(goal, worker_budget, max_cycles)

    def review(self, goal, cycle, incumbent_metrics, challenger_metrics, improved, feasible):
        prompt = (
            "Act as chief engineer reviewing one multidisciplinary cycle. Return only JSON with "
            "action (accept|iterate|stop), rationale, feedback. "
            f"Goal={goal.raw_request}; cycle={cycle}; incumbent={dict(incumbent_metrics)}; "
            f"challenger={dict(challenger_metrics)}; improved={improved}; feasible={feasible}."
        )
        try:
            data = self._json_call(prompt)
            action = str(data.get("action", "iterate"))
            if action not in {"accept", "iterate", "stop"}:
                action = "iterate"
            if action == "accept" and not feasible:
                action = "iterate"
            if action == "accept" and not improved:
                action = "stop"
            return ChiefDecision(action, str(data.get("rationale", "")), str(data.get("feedback", "")))
        except Exception:
            return self.fallback.review(goal, cycle, incumbent_metrics, challenger_metrics, improved, feasible)

    def _json_call(self, prompt: str) -> dict:
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read())
        content = payload["choices"][0]["message"].get("content", "")
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("reasoning model returned no JSON object")
        return json.loads(match.group(0))


def _ordered_domains(
    domains,
    dependencies: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Stable topological order driven entirely by adapter-declared edges."""
    selected = list(dict.fromkeys(domain_name(domain) for domain in domains))
    selected_set = set(selected)
    edges = {
        domain: tuple(item for item in (dependencies or {}).get(domain, ()) if item in selected_set)
        for domain in selected
    }
    ordered: list[str] = []
    remaining = list(selected)
    while remaining:
        ready = [domain for domain in remaining if all(dep in ordered for dep in edges[domain])]
        if not ready:
            cycle = " -> ".join(remaining)
            raise ValueError(f"adapter domain dependencies contain a cycle: {cycle}")
        for domain in ready:
            ordered.append(domain)
            remaining.remove(domain)
    return ordered


def _stage_shape(worker_budget: int, has_parent: bool, parameter_count: int) -> tuple[int, int]:
    """Scale exploration from declared design-space width, independent of domain vocabulary."""
    width = max(1, int(parameter_count))
    if has_parent:
        fanout = min(4, max(2, math.ceil(math.sqrt(width))))
    else:
        fanout = min(8, max(4, 2 * math.ceil(math.sqrt(width))))
    # worker_budget limits concurrent infrastructure, not the number of
    # hypotheses the fleet may evaluate in batches.
    fanout = min(fanout, max(2, int(worker_budget) * 4))
    return fanout, min(2, fanout)


def _stage_id(domain: str, index: int) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    return cleaned or f"domain-{index + 1}"


def _stage_name(domain: str) -> str:
    return f"{domain.replace('_', ' ').title()} campaign"


def _validate_model_plan(stages: tuple[StageSpec, ...], goal: GoalSpec, worker_budget: int) -> None:
    """Fail closed when a reasoning model invents capabilities or an invalid graph."""
    if not stages:
        raise ValueError("model-authored plan has no stages")
    allowed_domains = {domain_name(domain) for domain in goal.domains}
    allowed_analyses = set(goal.analyses)
    ids = [stage.id for stage in stages]
    if len(ids) != len(set(ids)):
        raise ValueError("model-authored plan has duplicate stage ids")
    seen: set[str] = set()
    used_domains: set[str] = set()
    for stage in stages:
        domain = domain_name(stage.domain)
        if domain not in allowed_domains:
            raise ValueError(f"model-authored plan invented domain {domain!r}")
        used_domains.add(domain)
        if not stage.analyses or not set(stage.analyses).issubset(allowed_analyses):
            raise ValueError(f"stage {stage.id!r} uses undeclared or empty analyses")
        if any(dependency not in seen for dependency in stage.depends_on):
            raise ValueError(f"stage {stage.id!r} depends on a missing or later stage")
        if stage.fanout_per_input > max(2, int(worker_budget) * 4):
            raise ValueError(f"stage {stage.id!r} exceeds the hypothesis budget")
        if stage.keep > stage.fanout_per_input:
            raise ValueError(f"stage {stage.id!r} keeps more candidates than it creates")
        seen.add(stage.id)
    if not allowed_domains.issubset(used_domains):
        missing = sorted(allowed_domains - used_domains)
        raise ValueError(f"model-authored plan omitted required domains: {missing}")
    if not allowed_analyses.issubset(set(stages[-1].analyses)):
        raise ValueError("final stage cannot measure every objective and constraint")
