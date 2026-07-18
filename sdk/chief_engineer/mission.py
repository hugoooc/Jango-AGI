"""Autonomous sequential/parallel multidisciplinary chief-engineer missions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .agents import CapabilityDrivenAgent, default_specialists
from .fleet import ApiFactory, ApiFleet, LocalVmProvider, WorkerTask
from .models import Candidate, Evaluation, ExplorationPlan, MetricSpec, OptimizationReport, ParameterSpec, domain_name
from .orchestrator import ChiefEngineer
from .planner import EngineeringPlanner
from .reasoning import (
    ChiefDecision,
    MissionPlan,
    OpenAICompatibleReasoningProvider,
    ReasoningProvider,
    RuleBasedReasoningProvider,
    StageSpec,
)


@dataclass
class StageOutcome:
    stage: StageSpec
    inputs: list[Candidate]
    candidates: list[Candidate]
    evaluations: list[Evaluation]
    selected: list[Candidate]


@dataclass
class CycleOutcome:
    cycle: int
    stages: list[StageOutcome] = field(default_factory=list)
    challenger: Candidate | None = None
    challenger_evaluation: Evaluation | None = None
    decision: ChiefDecision | None = None


@dataclass
class MissionOutcome:
    mission_id: str
    request: str
    plan: MissionPlan
    baseline: Candidate
    baseline_evaluation: Evaluation
    winner: Candidate
    winner_evaluation: Evaluation
    cycles: list[CycleOutcome]
    status: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "request": self.request,
            "status": self.status,
            "reason": self.reason,
            "plan": {
                "rationale": self.plan.rationale,
                "worker_budget": self.plan.worker_budget,
                "max_cycles": self.plan.max_cycles,
                "stages": [_stage_dict(stage) for stage in self.plan.stages],
            },
            "baseline": _candidate_result(self.baseline, self.baseline_evaluation),
            "winner": _candidate_result(self.winner, self.winner_evaluation),
            "cycles": [
                {
                    "cycle": cycle.cycle,
                    "decision": None if cycle.decision is None else {
                        "action": cycle.decision.action,
                        "rationale": cycle.decision.rationale,
                        "feedback": cycle.decision.feedback,
                    },
                    "stages": [
                        {
                            "stage": _stage_dict(stage.stage),
                            "inputs": [candidate.id for candidate in stage.inputs],
                            "candidate_count": len(stage.candidates),
                            "selected": [candidate.id for candidate in stage.selected],
                            "evaluations": [_evaluation_dict(item) for item in stage.evaluations],
                        }
                        for stage in cycle.stages
                    ],
                }
                for cycle in self.cycles
            ],
        }


class AutonomousChief:
    """Run a multidisciplinary design organization as an executable mission graph."""

    def __init__(
        self,
        mission_id: str,
        api_factory: ApiFactory,
        *,
        reasoning: ReasoningProvider | None = None,
        vm_provider=None,
        event_sink=None,
        max_workers: int = 32,
        parameter_specs: tuple[ParameterSpec, ...] = (),
        metric_specs: tuple[MetricSpec, ...] = (),
    ):
        self.mission_id = mission_id
        self.planner = EngineeringPlanner(metric_specs or None)
        self.reasoning = reasoning or OpenAICompatibleReasoningProvider.from_environment() or RuleBasedReasoningProvider()
        self.parameter_names = {item.name for item in parameter_specs}
        self.specialists = default_specialists()
        if parameter_specs:
            domains = list(dict.fromkeys(
                domain_name(domain)
                for spec in parameter_specs
                for domain in spec.domains
            ))
            self.specialists = {
                domain: CapabilityDrivenAgent(domain, parameter_specs)
                for domain in domains
            }
        self.event_sink = event_sink
        self.fleet = ApiFleet(
            vm_provider or LocalVmProvider(),
            api_factory,
            max_workers=max_workers,
            event_sink=self._emit,
        )

    def run(
        self,
        request: str,
        initial_design: Mapping[str, float] | None = None,
        *,
        worker_budget: int = 12,
        max_cycles: int = 3,
    ) -> MissionOutcome:
        goal = self.planner.parse_goal(request, max_iterations=max_cycles)
        plan = self.reasoning.plan(goal, worker_budget, max_cycles)
        self._emit("mission.planned", {
            "request": request,
            "objective": goal.objective,
            "direction": goal.direction,
            "constraints": [
                {"metric": item.metric, "operator": item.operator, "value": item.value}
                for item in goal.constraints
            ],
            "analyses": list(goal.analyses),
            "rationale": plan.rationale,
            "worker_budget": plan.worker_budget,
            "max_cycles": plan.max_cycles,
            "stages": [_stage_dict(stage) for stage in plan.stages],
        })

        baseline = Candidate("baseline", None, dict(initial_design or {}), plan.stages[0].domain, "Current released design", 0)
        baseline_evaluation = self._evaluate([baseline], plan.stages[-1], goal, worker_budget)[0]
        if not initial_design and not baseline_evaluation.error:
            design_keys = self.parameter_names or {
                "wing_span", "wing_sweep", "wing_taper", "wing_twist", "wing_root_chord",
                "htail_span", "htail_area", "htail_arm", "cg_shift", "skin_thickness",
            }
            resolved_design = {
                key: value for key, value in baseline_evaluation.metrics.items() if key in design_keys
            }
            baseline = Candidate(
                baseline.id,
                baseline.parent_id,
                resolved_design,
                baseline.proposed_by,
                baseline.rationale,
                baseline.iteration,
            )
        ChiefEngineer._score(baseline_evaluation, goal)
        incumbent, incumbent_evaluation = baseline, baseline_evaluation
        cycles: list[CycleOutcome] = []
        status, reason = "running", ""

        self._emit("mission.started", {
            "baseline": baseline.id,
            "metrics": baseline_evaluation.metrics,
            "feasible": baseline_evaluation.feasible,
        })
        if baseline_evaluation.error:
            return MissionOutcome(
                self.mission_id, request, plan, baseline, baseline_evaluation,
                incumbent, incumbent_evaluation, cycles, "failed", baseline_evaluation.error,
            )

        for cycle_number in range(1, plan.max_cycles + 1):
            self._emit("cycle.started", {"cycle": cycle_number, "incumbent": incumbent.id})
            cycle = CycleOutcome(cycle_number)
            stage_inputs = [incumbent]

            for stage in plan.stages:
                self._emit("stage.started", {
                    "cycle": cycle_number,
                    "stage": _stage_dict(stage),
                    "inputs": [candidate.id for candidate in stage_inputs],
                })
                candidates = self._propose(stage, stage_inputs, goal, cycle_number)
                self._emit("team.spawned", {
                    "cycle": cycle_number,
                    "stage_id": stage.id,
                    "domain": domain_name(stage.domain),
                    "agent_count": len(candidates),
                    "agents": [
                        {"id": candidate.id, "parent_id": candidate.parent_id, "rationale": candidate.rationale}
                        for candidate in candidates
                    ],
                })
                evaluations = self._evaluate(candidates, stage, goal, worker_budget)
                by_id = {candidate.id: candidate for candidate in candidates}
                successful = [evaluation for evaluation in evaluations if evaluation.error is None]
                selected_evaluations = sorted(successful, key=lambda item: item.score, reverse=True)[:stage.keep]
                selected = [by_id[evaluation.candidate_id] for evaluation in selected_evaluations]
                outcome = StageOutcome(stage, list(stage_inputs), candidates, evaluations, selected)
                cycle.stages.append(outcome)

                self._emit("stage.completed", {
                    "cycle": cycle_number,
                    "stage_id": stage.id,
                    "selected": [
                        {
                            "candidate_id": evaluation.candidate_id,
                            "score": evaluation.score,
                            "feasible": evaluation.feasible,
                            "metrics": evaluation.metrics,
                        }
                        for evaluation in selected_evaluations
                    ],
                })
                if not selected:
                    status, reason = "failed", f"all {stage.name} agents failed"
                    self._emit("mission.failed", {"reason": reason})
                    return MissionOutcome(
                        self.mission_id, request, plan, baseline, baseline_evaluation,
                        incumbent, incumbent_evaluation, cycles + [cycle], status, reason,
                    )
                next_stage = plan.stages[len(cycle.stages)] if len(cycle.stages) < len(plan.stages) else None
                if next_stage:
                    for candidate in selected:
                        self._emit("artifact.transferred", {
                            "cycle": cycle_number,
                            "artifact_id": candidate.id,
                            "from_stage": stage.id,
                            "to_stage": next_stage.id,
                            "design": dict(candidate.design),
                        })
                stage_inputs = selected

            final_stage = cycle.stages[-1]
            final_evaluations = {
                evaluation.candidate_id: evaluation for evaluation in final_stage.evaluations
            }
            challenger = max(
                final_stage.selected,
                key=lambda candidate: final_evaluations[candidate.id].score,
            )
            challenger_evaluation = final_evaluations[challenger.id]
            improved = challenger_evaluation.score > incumbent_evaluation.score + goal.tolerance
            decision = self.reasoning.review(
                goal,
                cycle_number,
                incumbent_evaluation.metrics,
                challenger_evaluation.metrics,
                improved,
                challenger_evaluation.feasible,
            )
            cycle.challenger = challenger
            cycle.challenger_evaluation = challenger_evaluation
            cycle.decision = decision
            cycles.append(cycle)

            if improved:
                incumbent, incumbent_evaluation = challenger, challenger_evaluation
            self._emit("chief.reviewed", {
                "cycle": cycle_number,
                "action": decision.action,
                "rationale": decision.rationale,
                "feedback": decision.feedback,
                "challenger": challenger.id,
                "improved": improved,
                "feasible": challenger_evaluation.feasible,
                "metrics": challenger_evaluation.metrics,
            })

            if decision.action in {"accept", "stop"}:
                status = "complete" if incumbent_evaluation.feasible else "incomplete"
                reason = decision.rationale
                break
            self._emit("feedback.issued", {
                "cycle": cycle_number,
                "to": [domain_name(stage.domain) for stage in plan.stages],
                "message": decision.feedback,
                "new_baseline": incumbent.id,
            })
        else:
            status = "complete" if incumbent_evaluation.feasible else "incomplete"
            reason = "Chief exhausted the authorized design cycles."

        self._emit("mission.completed", {
            "status": status,
            "reason": reason,
            "winner": incumbent.id,
            "metrics": incumbent_evaluation.metrics,
            "feasible": incumbent_evaluation.feasible,
        })
        return MissionOutcome(
            self.mission_id, request, plan, baseline, baseline_evaluation,
            incumbent, incumbent_evaluation, cycles, status, reason,
        )

    def _propose(self, stage, parents, goal, cycle):
        specialist = self.specialists[domain_name(stage.domain)]
        candidates = []
        ordinal = 0
        for parent in parents:
            proposals = list(specialist.propose(parent.design, goal, cycle, parent.id))
            for design, rationale in proposals[:stage.fanout_per_input]:
                candidates.append(Candidate(
                    id=f"c{cycle}-{stage.id[:4]}-{ordinal:03d}",
                    parent_id=parent.id,
                    design=dict(design),
                    proposed_by=stage.domain,
                    rationale=f"{stage.instructions} {rationale}".strip(),
                    iteration=cycle,
                ))
                ordinal += 1
        return candidates

    def _evaluate(self, candidates, stage, goal, worker_budget):
        exploration = ExplorationPlan(
            goal=goal,
            domains=(stage.domain,),
            analyses=stage.analyses,
            worker_count=min(worker_budget, max(1, len(candidates))),
            rationale=stage.instructions,
        )
        tasks = [
            WorkerTask(candidate, f"{domain_name(stage.domain)}-agent", stage.analyses)
            for candidate in candidates
        ]
        evaluations = self.fleet.evaluate(tasks, exploration)
        for evaluation in evaluations:
            self._score_stage(evaluation, goal)
        return evaluations

    @staticmethod
    def _score_stage(evaluation, goal):
        """Score partial-stage evidence without failing on downstream metrics.

        A geometry or aerodynamic stage may not have produced stability or
        structural metrics yet. Available constraints are enforced immediately;
        missing constraints remain open until a downstream stage evaluates them.
        """
        if evaluation.error:
            evaluation.score = float("-inf")
            evaluation.feasible = False
            return
        objective = evaluation.metrics.get(goal.objective)
        if objective is None:
            evaluation.score = float("-inf")
            evaluation.feasible = False
            return
        violations = {}
        all_constraints_observed = True
        for constraint in goal.constraints:
            if constraint.metric not in evaluation.metrics:
                all_constraints_observed = False
                continue
            violations[constraint.label or constraint.metric] = constraint.violation(evaluation.metrics)
        evaluation.violations = violations
        evaluation.feasible = all_constraints_observed and all(
            value <= goal.tolerance for value in violations.values()
        )
        penalty = sum(value * 1_000.0 for value in violations.values())
        evaluation.score = goal.objective_sign * objective - penalty

    def _emit(self, event, payload):
        if self.event_sink:
            self.event_sink(event, payload)


def _stage_dict(stage: StageSpec) -> dict:
    return {
        "id": stage.id,
        "name": stage.name,
        "domain": domain_name(stage.domain),
        "analyses": list(stage.analyses),
        "fanout_per_input": stage.fanout_per_input,
        "keep": stage.keep,
        "depends_on": list(stage.depends_on),
        "instructions": stage.instructions,
    }


def _evaluation_dict(evaluation: Evaluation) -> dict:
    return {
        "candidate_id": evaluation.candidate_id,
        "worker_id": evaluation.worker_id,
        "metrics": evaluation.metrics,
        "score": evaluation.score,
        "feasible": evaluation.feasible,
        "violations": evaluation.violations,
        "elapsed_s": evaluation.elapsed_s,
        "error": evaluation.error,
        "artifacts": [dict(item) for item in evaluation.artifacts],
    }


def _candidate_result(candidate: Candidate, evaluation: Evaluation) -> dict:
    return {
        "id": candidate.id,
        "parent_id": candidate.parent_id,
        "design": dict(candidate.design),
        "proposed_by": domain_name(candidate.proposed_by),
        "rationale": candidate.rationale,
        "evaluation": _evaluation_dict(evaluation),
    }
