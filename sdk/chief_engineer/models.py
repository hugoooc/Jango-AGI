"""Data contracts for the API-first chief-engineer loop.

The orchestration layer deliberately speaks in domain objects rather than GUI
actions.  A worker receives a candidate design, calls a software API, and
returns structured metrics that can be compared and fed into the next loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, TypeAlias


class Domain(str, Enum):
    AERODYNAMICS = "aerodynamics"
    STABILITY = "stability"
    STRUCTURES = "structures"
    GEOMETRY = "geometry"


DomainName: TypeAlias = str | Domain


def domain_name(domain: DomainName) -> str:
    """Return a stable domain identifier without restricting adapter vocabulary."""
    return domain.value if isinstance(domain, Domain) else str(domain)


@dataclass(frozen=True)
class ParameterSpec:
    """One software-declared design variable the chief may explore."""

    name: str
    minimum: float
    maximum: float
    relative_step: float = 0.10
    unit: str = ""
    description: str = ""
    domains: tuple[DomainName, ...] = (Domain.GEOMETRY,)


@dataclass(frozen=True)
class MetricSpec:
    """Canonical metric exposed by an adapter, independent of solver naming."""

    name: str
    analysis: str
    default_direction: str = "min"
    aliases: tuple[str, ...] = ()
    unit: str = ""
    domains: tuple[DomainName, ...] = ()


@dataclass(frozen=True)
class Constraint:
    metric: str
    operator: str
    value: float
    label: str | None = None

    def violation(self, metrics: Mapping[str, float]) -> float:
        """Return a normalized positive violation, or zero when satisfied."""
        observed = metrics.get(self.metric)
        if observed is None:
            return float("inf")
        if self.operator == ">=":
            return max(0.0, self.value - observed)
        if self.operator == "<=":
            return max(0.0, observed - self.value)
        if self.operator == "==":
            return abs(observed - self.value)
        raise ValueError(f"unsupported constraint operator: {self.operator}")


@dataclass(frozen=True)
class GoalSpec:
    raw_request: str
    objective: str
    direction: str = "max"
    constraints: tuple[Constraint, ...] = ()
    domains: tuple[DomainName, ...] = ()
    max_iterations: int = 4
    tolerance: float = 1e-3
    analyses: tuple[str, ...] = ()

    @property
    def objective_sign(self) -> float:
        return 1.0 if self.direction == "max" else -1.0


@dataclass(frozen=True)
class Candidate:
    id: str
    parent_id: str | None
    design: Mapping[str, float]
    proposed_by: DomainName
    rationale: str
    iteration: int


@dataclass
class Evaluation:
    candidate_id: str
    worker_id: str
    metrics: dict[str, float] = field(default_factory=dict)
    feasible: bool = False
    violations: dict[str, float] = field(default_factory=dict)
    score: float = float("-inf")
    elapsed_s: float = 0.0
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ExplorationStage:
    name: str
    domain: DomainName
    analyses: tuple[str, ...]
    fanout: int
    depends_on: tuple[str, ...] = ()
    rationale: str = ""


@dataclass(frozen=True)
class ExplorationPlan:
    goal: GoalSpec
    domains: tuple[DomainName, ...]
    analyses: tuple[str, ...]
    worker_count: int
    rationale: str
    stages: tuple[ExplorationStage, ...] = ()


@dataclass
class IterationReport:
    iteration: int
    incumbent_id: str
    candidates: list[Candidate] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    selected_id: str | None = None
    improved: bool = False


@dataclass
class OptimizationReport:
    goal: GoalSpec
    plan: ExplorationPlan
    baseline: Evaluation | None
    winner: Candidate | None
    winner_evaluation: Evaluation | None
    iterations: list[IterationReport] = field(default_factory=list)
    status: str = "incomplete"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialize the report for a dashboard, API endpoint, or audit log."""
        return {
            "status": self.status,
            "reason": self.reason,
            "goal": {
                "request": self.goal.raw_request,
                "objective": self.goal.objective,
                "direction": self.goal.direction,
                "domains": [domain_name(d) for d in self.goal.domains],
                "constraints": [
                    {"metric": c.metric, "operator": c.operator, "value": c.value}
                    for c in self.goal.constraints
                ],
            },
            "plan": {
                "domains": [domain_name(d) for d in self.plan.domains],
                "analyses": list(self.plan.analyses),
                "worker_count": self.plan.worker_count,
                "rationale": self.plan.rationale,
                "stages": [
                    {
                        "name": stage.name,
                        "domain": domain_name(stage.domain),
                        "analyses": list(stage.analyses),
                        "fanout": stage.fanout,
                        "depends_on": list(stage.depends_on),
                    }
                    for stage in self.plan.stages
                ],
            },
            "winner": None if self.winner is None else {
                "id": self.winner.id,
                "design": dict(self.winner.design),
                "proposed_by": domain_name(self.winner.proposed_by),
                "rationale": self.winner.rationale,
            },
            "winner_evaluation": _evaluation_dict(self.winner_evaluation),
            "iterations": [
                {
                    "iteration": item.iteration,
                    "incumbent_id": item.incumbent_id,
                    "selected_id": item.selected_id,
                    "improved": item.improved,
                    "evaluations": [_evaluation_dict(e) for e in item.evaluations],
                }
                for item in self.iterations
            ],
        }


def _evaluation_dict(evaluation: Evaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "candidate_id": evaluation.candidate_id,
        "worker_id": evaluation.worker_id,
        "metrics": dict(evaluation.metrics),
        "feasible": evaluation.feasible,
        "violations": dict(evaluation.violations),
        "score": evaluation.score,
        "elapsed_s": evaluation.elapsed_s,
        "error": evaluation.error,
        "artifacts": [dict(item) for item in evaluation.artifacts],
    }
