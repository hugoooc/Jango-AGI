"""Goal decomposition for the chief engineer."""

from __future__ import annotations

import re
from typing import Iterable

from .models import Constraint, Domain, DomainName, ExplorationPlan, ExplorationStage, GoalSpec, MetricSpec, domain_name


DEFAULT_METRICS = (
    MetricSpec("L_D", "aerodynamics", "max", ("l/d", "lift to drag", "aerodynamic efficiency"), domains=(Domain.AERODYNAMICS, Domain.GEOMETRY)),
    MetricSpec("CD", "aerodynamics", "min", ("drag coefficient", "drag", "cd"), domains=(Domain.AERODYNAMICS, Domain.GEOMETRY)),
    MetricSpec("CL", "aerodynamics", "max", ("lift coefficient", "lift", "cl"), domains=(Domain.AERODYNAMICS, Domain.GEOMETRY)),
    MetricSpec("static_margin", "stability", "max", ("static margin", "stability margin", "stability", "stable"), domains=(Domain.STABILITY, Domain.GEOMETRY)),
    MetricSpec("mass", "structures", "min", ("total mass", "mass", "weight"), domains=(Domain.STRUCTURES, Domain.GEOMETRY)),
)


class EngineeringPlanner:
    """Turn a difficult engineering request into domain work packages.

    This is intentionally a small, inspectable baseline planner.  It is also a
    stable contract for replacing the heuristic with a reasoning model later:
    the rest of the system only consumes ``GoalSpec`` and ``ExplorationPlan``.
    """

    def __init__(self, metric_specs: Iterable[MetricSpec] | None = None):
        self.metric_specs = tuple(metric_specs or DEFAULT_METRICS)
        self.metrics_by_name = {item.name: item for item in self.metric_specs}

    def parse_goal(self, request: str, *, max_iterations: int = 4) -> GoalSpec:
        text = request.strip()
        if not text:
            raise ValueError("engineering goal cannot be empty")
        lower = text.lower()
        mentions = self._mentions(lower)
        if not mentions:
            available = ", ".join(item.name for item in self.metric_specs)
            raise ValueError(f"could not identify an objective metric; available metrics: {available}")
        objective_spec, direction = self._objective(lower, mentions)
        objective = objective_spec.name
        constraints = self._constraints(lower, objective)

        involved_specs = [objective_spec]
        involved_specs.extend(
            self.metrics_by_name[item.metric]
            for item in constraints
            if item.metric in self.metrics_by_name
        )
        domains = list(dict.fromkeys(
            domain_name(domain)
            for spec in involved_specs
            for domain in (spec.domains or (spec.analysis,))
        )) or [domain_name(domain) for domain in self._domains(lower, objective, constraints)]
        analyses = list(dict.fromkeys(spec.analysis for spec in involved_specs))
        return GoalSpec(
            raw_request=text,
            objective=objective,
            direction=direction,
            constraints=tuple(constraints),
            domains=tuple(domains),
            max_iterations=max(1, max_iterations),
            analyses=tuple(analyses),
        )

    def _mentions(self, text: str) -> list[tuple[int, int, MetricSpec, str]]:
        mentions = []
        for spec in self.metric_specs:
            aliases = (spec.name.lower(), *spec.aliases)
            for alias in aliases:
                for match in re.finditer(rf"(?<![a-z0-9_]){re.escape(alias.lower())}(?![a-z0-9_])", text):
                    mentions.append((match.start(), -len(alias), spec, alias))
        mentions.sort(key=lambda item: (item[0], item[1]))
        # At the same character position, keep the longest semantic alias so
        # "lift to drag" is not misread as the separate "lift" metric.
        by_position = {}
        for item in mentions:
            by_position.setdefault(item[0], item)
        return list(by_position.values())

    @staticmethod
    def _objective(text: str, mentions) -> tuple[MetricSpec, str]:
        verbs = list(re.finditer(r"\b(minimi[sz]e|maximi[sz]e|reduce|decrease|lower|increase|raise|improve|optimi[sz]e)\b", text))
        chosen = mentions[0]
        verb = None
        if verbs:
            verb = verbs[0]
            after = [item for item in mentions if item[0] >= verb.end()]
            if after:
                chosen = min(after, key=lambda item: item[0] - verb.end())
        spec = chosen[2]
        word = verb.group(1) if verb else ""
        if word.startswith(("minimi", "reduce", "decrease", "lower")):
            direction = "min"
        elif word.startswith(("maximi", "increase", "raise", "improve")):
            direction = "max"
        else:
            direction = spec.default_direction
        return spec, direction

    def _constraints(self, text: str, objective: str) -> list[Constraint]:
        constraints = []
        seen = set()
        for spec in self.metric_specs:
            aliases = sorted({spec.name.lower(), *(alias.lower() for alias in spec.aliases)}, key=len, reverse=True)
            for alias in aliases:
                escaped = re.escape(alias)
                patterns = (
                    (rf"{escaped}\s*(?:(?:must|should)\s+be\s+|(?:stays?|remains?)\s+|at\s+)?(?:>=|at\s+least|above|over|greater\s+than|minimum(?:\s+of)?)\s*({_NUMBER})", ">="),
                    (rf"{escaped}\s*(?:(?:must|should)\s+be\s+|(?:stays?|remains?)\s+|at\s+)?(?:<=|at\s+most|below|under|less\s+than|maximum(?:\s+of)?|max)\s*({_NUMBER})", "<="),
                    (rf"(?:keep|stay|remain)\s+{escaped}\s*(?:<=|below|under)\s*({_NUMBER})", "<="),
                    (rf"(?:keep|stay|remain)\s+{escaped}\s*(?:>=|above|over)\s*({_NUMBER})", ">="),
                )
                match = None
                operator = None
                for pattern, candidate_operator in patterns:
                    match = re.search(pattern, text)
                    if match:
                        operator = candidate_operator
                        break
                if match and (spec.name, operator, match.group(1)) not in seen:
                    constraints.append(Constraint(spec.name, operator, float(match.group(1)), spec.name))
                    seen.add((spec.name, operator, match.group(1)))
                    break
        if objective != "static_margin" and any(token in text for token in ("stable", "stability")):
            if not any(item.metric == "static_margin" for item in constraints):
                constraints.append(Constraint("static_margin", ">=", 0.05, "stable"))
        return constraints

    def decompose(self, goal: GoalSpec, worker_count: int | None = None) -> ExplorationPlan:
        domains = goal.domains or (Domain.GEOMETRY,)
        analyses = _required_analyses(goal)
        requested = worker_count or max(2, len(domains) * 2)
        requested = max(1, min(requested, 64))
        ordered_domains = _ordered_domains(domains)
        stages = []
        previous: str | None = None
        for index, domain in enumerate(ordered_domains, start=1):
            stage_name = f"stage-{index}-{domain_name(domain)}"
            stages.append(ExplorationStage(
                name=stage_name,
                domain=domain,
                analyses=tuple(analyses),
                fanout=requested,
                depends_on=(previous,) if previous else (),
                rationale=f"Fan out {domain_name(domain)} specialists from the previous stage's best design.",
            ))
            previous = stage_name
        rationale = (
            f"Run sequential stages ({' → '.join(domain_name(domain) for domain in ordered_domains)}); "
            f"fan out up to {requested} workers per stage and evaluate "
            f"{', '.join(analyses)} directly through each software API."
        )
        return ExplorationPlan(goal, tuple(ordered_domains), tuple(analyses), requested, rationale, tuple(stages))

    @staticmethod
    def _domains(lower: str, objective: str, constraints: Iterable[Constraint]) -> list[Domain]:
        domains: list[Domain] = []
        if objective in {"L_D", "CD"} or any(token in lower for token in ("lift", "aero", "drag")):
            domains.append(Domain.AERODYNAMICS)
        if any(token in lower for token in ("stable", "stability", "static margin", "pitch")):
            domains.append(Domain.STABILITY)
        if any(token in lower for token in ("mass", "weight", "struct", "stress", "load")):
            domains.append(Domain.STRUCTURES)
        if objective in {"L_D", "CD"} or any(token in lower for token in ("shape", "geometry", "wing", "tail", "design")) or not domains:
            domains.append(Domain.GEOMETRY)
        return list(dict.fromkeys(domains))


def _analysis_for_domain(domain: DomainName) -> str:
    name = domain_name(domain)
    return "aerodynamics" if name == "geometry" else name


def _required_analyses(goal: GoalSpec) -> list[str]:
    """Include analyses needed to score both the objective and constraints."""
    if goal.analyses:
        return list(goal.analyses)
    metrics = [goal.objective, *(constraint.metric for constraint in goal.constraints)]
    analyses = []
    for metric in metrics:
        if metric in {"L_D", "CL", "CD"}:
            analyses.append("aerodynamics")
        elif metric == "static_margin":
            analyses.append("stability")
        elif metric in {"mass", "stress", "deflection"}:
            analyses.append("structures")
    return list(dict.fromkeys(analyses or [_analysis_for_domain(domain) for domain in goal.domains]))


def _ordered_domains(domains: Iterable[DomainName]) -> list[str]:
    selected = list(dict.fromkeys(domain_name(domain) for domain in domains))
    preferred = ["geometry", "aerodynamics", "stability", "structures"]
    return [name for name in preferred if name in selected] + [
        name for name in selected if name not in preferred
    ]


def _number_before_unit(text: str, phrase: str) -> float | None:
    phrase_pattern = re.escape(phrase)
    match = re.search(rf"({_NUMBER})\s*(?:%|percent)?\s*{phrase_pattern}", text)
    if match:
        return float(match.group(1))
    match = re.search(rf"{phrase_pattern}\s*(?:of|at|>=|>)?\s*({_NUMBER})\s*(?:%|percent)?", text)
    return float(match.group(1)) if match else None


def _number_after_any(text: str, words: Iterable[str]) -> float | None:
    joined = "|".join(re.escape(word) for word in words)
    match = re.search(rf"(?:{joined})\s*({_NUMBER})", text)
    return float(match.group(1)) if match else None


_NUMBER = r"-?\d+(?:\.\d+)?"
