"""Chief-engineer optimization loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from .agents import SpecialistAgent, default_specialists, materialize_candidates
from .fleet import ApiFactory, ApiFleet, LocalVmProvider, WorkerTask
from .models import Candidate, Evaluation, GoalSpec, IterationReport, OptimizationReport, domain_name
from .planner import EngineeringPlanner


EventSink = Callable[[str, dict], None]


@dataclass
class ChiefEngineer:
    """Coordinate decomposition, parallel API runs, scoring, and iteration."""

    api_factory: ApiFactory
    planner: EngineeringPlanner = field(default_factory=EngineeringPlanner)
    vm_provider: object | None = None
    specialists: Mapping | None = None
    max_workers: int = 64
    event_sink: EventSink | None = None

    def __post_init__(self) -> None:
        if self.vm_provider is None:
            self.vm_provider = LocalVmProvider()
        if self.specialists is None:
            self.specialists = default_specialists()
        self.fleet = ApiFleet(self.vm_provider, self.api_factory, self.max_workers)

    def run(
        self,
        request: str,
        initial_design: Mapping[str, float] | None = None,
        *,
        max_iterations: int | None = None,
        worker_count: int | None = None,
    ) -> OptimizationReport:
        goal = self.planner.parse_goal(request, max_iterations=max_iterations or 4)
        plan = self.planner.decompose(goal, worker_count=worker_count)
        self._emit("plan.created", {
            "domains": [domain_name(domain) for domain in plan.domains],
            "workers": plan.worker_count,
            "analyses": list(plan.analyses),
        })

        baseline_candidate = Candidate("baseline", None, dict(initial_design or {}), plan.domains[0], "initial design", 0)
        baseline_eval = self._evaluate([baseline_candidate], plan)[0]
        self._score(baseline_eval, goal)
        incumbent = baseline_candidate
        incumbent_eval = baseline_eval
        report = OptimizationReport(goal, plan, baseline_eval, incumbent, incumbent_eval)

        if baseline_eval.error:
            report.status = "failed"
            report.reason = f"baseline evaluation failed: {baseline_eval.error}"
            return report

        best_feasible_seen = baseline_eval.feasible
        for iteration in range(1, goal.max_iterations + 1):
            candidates = materialize_candidates(
                self.specialists, plan.domains, incumbent.design, incumbent.id, goal, iteration,
            )
            candidates = candidates[:plan.worker_count]
            evaluations = self._evaluate(candidates, plan)
            for evaluation in evaluations:
                self._score(evaluation, goal)
            selected_candidate, selected_eval = self._select(
                candidates, evaluations, incumbent, incumbent_eval, goal,
            )
            improved = selected_candidate.id != incumbent.id
            iteration_report = IterationReport(
                iteration=iteration,
                incumbent_id=incumbent.id,
                candidates=candidates,
                evaluations=evaluations,
                selected_id=selected_candidate.id,
                improved=improved,
            )
            report.iterations.append(iteration_report)
            self._emit("iteration.completed", {
                "iteration": iteration,
                "candidate_count": len(candidates),
                "selected": selected_candidate.id,
                "feasible": selected_eval.feasible,
                "score": selected_eval.score,
            })

            if improved:
                incumbent, incumbent_eval = selected_candidate, selected_eval
            best_feasible_seen = best_feasible_seen or selected_eval.feasible
            report.winner, report.winner_evaluation = incumbent, incumbent_eval
            if not improved:
                report.status = "complete" if best_feasible_seen else "incomplete"
                report.reason = "no candidate improved the incumbent"
                return report

        report.status = "complete" if incumbent_eval.feasible else "incomplete"
        report.reason = "iteration budget exhausted"
        return report

    def _evaluate(self, candidates: list[Candidate], plan) -> list[Evaluation]:
        tasks = [WorkerTask(candidate, domain_name(candidate.proposed_by), plan.analyses) for candidate in candidates]
        return self.fleet.evaluate(tasks, plan)

    @staticmethod
    def _score(evaluation: Evaluation, goal: GoalSpec) -> None:
        if evaluation.error:
            evaluation.feasible = False
            evaluation.score = float("-inf")
            return
        evaluation.violations = {
            constraint.label or constraint.metric: constraint.violation(evaluation.metrics)
            for constraint in goal.constraints
        }
        evaluation.feasible = all(value <= goal.tolerance for value in evaluation.violations.values())
        objective = evaluation.metrics.get(goal.objective)
        if objective is None:
            evaluation.score = float("-inf")
            evaluation.feasible = False
            return
        penalty = sum(value * 1_000.0 for value in evaluation.violations.values())
        evaluation.score = goal.objective_sign * objective - penalty

    @staticmethod
    def _select(candidates, evaluations, incumbent, incumbent_eval, goal):
        by_id = {candidate.id: candidate for candidate in candidates}
        valid = [evaluation for evaluation in evaluations if evaluation.error is None]
        if not valid:
            return incumbent, incumbent_eval
        chosen = max(valid, key=lambda evaluation: evaluation.score)
        if chosen.score <= incumbent_eval.score + goal.tolerance:
            return incumbent, incumbent_eval
        return by_id[chosen.candidate_id], chosen

    def _emit(self, event: str, payload: dict) -> None:
        if self.event_sink:
            self.event_sink(event, payload)


def synthetic_engineer(**kwargs) -> ChiefEngineer:
    """Convenience constructor used by the demo and smoke tests."""
    from .api import SyntheticApi

    return ChiefEngineer(api_factory=lambda _handle: SyntheticApi(), **kwargs)
