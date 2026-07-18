"""Evidence-driven, bounded improvement proposals for the Chief runtime.

This module never edits prompts, adapters, recipes, or production state.  It
turns durable mission evidence into a typed change hypothesis and the gates a
new immutable runtime version must pass before an operator can promote it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ImprovementProposal:
    proposal_id: str
    mission_id: str
    disposition: str
    target: str | None
    priority: str
    confidence: float
    evidence: tuple[dict[str, Any], ...] = ()
    hypothesis: str = ""
    proposed_change: str = ""
    regression_gates: tuple[str, ...] = ()
    rollout: tuple[str, ...] = ()
    auto_promote: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def propose_improvement(
    mission: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> ImprovementProposal:
    """Create one prioritized proposal from domain-neutral mission evidence."""
    mission_id = str(mission.get("mission_id") or "unknown")
    state = str(mission.get("state") or "unknown")
    result = mission.get("result") if isinstance(mission.get("result"), Mapping) else {}
    event_types = [str(item.get("event") or "") for item in events]
    failed = [item for item in events if item.get("event") in {"agent.failed", "mission.failed"}]
    completed_agents = sum(item == "agent.completed" for item in event_types)
    provisioned = sum(item == "worker.provisioned" for item in event_types)
    released = sum(item == "worker.released" for item in event_types)
    transfers = sum(item == "artifact.transferred" for item in event_types)
    baseline_score = _score(result, "baseline")
    winner_score = _score(result, "winner")
    score_delta = None
    if baseline_score is not None and winner_score is not None:
        score_delta = winner_score - baseline_score

    evidence: list[dict[str, Any]] = [{
        "kind": "mission_summary",
        "state": state,
        "agent_completed": completed_agents,
        "worker_provisioned": provisioned,
        "worker_released": released,
        "artifact_transfers": transfers,
        "score_delta": score_delta,
    }]

    if failed:
        evidence.extend(_event_evidence(item) for item in failed[:8])
        failure_rate = len(failed) / max(1, completed_agents + len(failed))
        return _proposal(
            mission_id,
            "proposed",
            "execution_reliability",
            "critical" if state == "failed" else "high",
            min(0.95, 0.55 + failure_rate),
            evidence,
            "One or more executable workers or the mission failed; changing reasoning alone cannot repair missing solver evidence.",
            "Cluster normalized failure signatures, fix the responsible adapter/runtime boundary, and preserve the failing mission as a regression fixture.",
            (
                "Replay every cited failing candidate with the same design and analyses.",
                "Require zero uncategorized worker failures in the fixture set.",
                "Require deterministic metrics and artifact provenance for each recovered evaluation.",
            ),
        )

    if provisioned != released:
        evidence.append({
            "kind": "resource_leak",
            "provisioned": provisioned,
            "released": released,
        })
        return _proposal(
            mission_id,
            "proposed",
            "worker_lifecycle",
            "critical",
            0.98,
            evidence,
            "The worker lifecycle ledger is unbalanced, indicating leaked execution capacity.",
            "Repair release/finally behavior and add lifecycle reconciliation before accepting more missions.",
            (
                "Assert provisioned worker count equals released worker count after success, failure, and cancellation.",
                "Verify no provider-owned worker remains after teardown.",
            ),
        )

    if state in {"failed", "incomplete"} or result.get("status") in {"failed", "incomplete"}:
        evidence.append({"kind": "terminal_state", "reason": result.get("reason") or mission.get("error")})
        return _proposal(
            mission_id,
            "proposed",
            "mission_policy",
            "high",
            0.82,
            evidence,
            "The mission reached a terminal state without a feasible accepted result.",
            "Inspect missing metrics, active violations, and stage selection pressure; change the smallest capability, scorer, or exploration policy that explains the evidence.",
            (
                "Replay the mission contract against the candidate change.",
                "Require every objective and constraint metric to be finite and observed.",
                "Require a feasible result or an explicit unsupported-capability decision.",
            ),
        )

    if score_delta is None:
        evidence.append({"kind": "missing_score", "baseline": baseline_score, "winner": winner_score})
        return _proposal(
            mission_id,
            "proposed",
            "evidence_contract",
            "high",
            0.9,
            evidence,
            "The completed mission cannot be compared because baseline or winner score is absent.",
            "Make objective/constraint evidence mandatory at the scorer boundary and fail closed on missing values.",
            (
                "Reject a candidate with an absent objective metric.",
                "Reject a feasibility claim with an unobserved constraint.",
                "Persist comparable baseline and winner scores.",
            ),
        )

    if score_delta <= 1e-9:
        return _proposal(
            mission_id,
            "proposed",
            "exploration_policy",
            "medium",
            0.72,
            evidence,
            "The authorized campaign produced no measurable improvement over the incumbent.",
            "Use the mission trajectory as an eval fixture and test a bounded change to proposal diversity, stage fan-out, or review feedback.",
            (
                "Use identical capability manifests, baseline, budget, and deterministic scorer.",
                "Require a positive score delta without reducing feasibility.",
                "Compare the candidate recipe with the incumbent in an Introspection experiment before promotion.",
            ),
        )

    return _proposal(
        mission_id,
        "no_change",
        None,
        "none",
        0.96,
        evidence,
        "The mission improved the deterministic score, remained feasible, completed worker cleanup, and recorded no execution failures.",
        "Preserve the current runtime; collect more trajectories before proposing a behavioral change.",
        ("Keep existing regression judges enabled.",),
        notes=("Successful missions are evidence for stability, not permission to mutate production.",),
    )


def _proposal(
    mission_id: str,
    disposition: str,
    target: str | None,
    priority: str,
    confidence: float,
    evidence: Sequence[Mapping[str, Any]],
    hypothesis: str,
    proposed_change: str,
    regression_gates: Sequence[str],
    *,
    notes: Sequence[str] = (),
) -> ImprovementProposal:
    canonical = json.dumps({
        "mission_id": mission_id,
        "target": target,
        "evidence": list(evidence),
        "hypothesis": hypothesis,
    }, sort_keys=True, separators=(",", ":"), default=str)
    proposal_id = "ip-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return ImprovementProposal(
        proposal_id=proposal_id,
        mission_id=mission_id,
        disposition=disposition,
        target=target,
        priority=priority,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        evidence=tuple(dict(item) for item in evidence),
        hypothesis=hypothesis,
        proposed_change=proposed_change,
        regression_gates=tuple(regression_gates),
        rollout=(
            "Create a new immutable recipe commit; never edit the active version in place.",
            "Run unit, adapter, solver replay, and recipe validation gates.",
            "Deploy a sibling staging runtime and compare with the incumbent using the same judge versions.",
            "Promote only a measured winner and retain rollback.",
        ),
        auto_promote=False,
        notes=tuple(notes),
    )


def _score(result: Mapping[str, Any], key: str) -> float | None:
    item = result.get(key)
    if not isinstance(item, Mapping):
        return None
    evaluation = item.get("evaluation")
    if not isinstance(evaluation, Mapping):
        return None
    value = evaluation.get("score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_evidence(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    return {
        "kind": "event",
        "sequence": event.get("sequence"),
        "event": event.get("event"),
        "agent_id": payload.get("agent_id"),
        "worker_id": payload.get("worker_id"),
        "error": payload.get("error") or payload.get("reason"),
    }
