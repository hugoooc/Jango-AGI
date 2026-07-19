"""Golden-fixture runner. Deterministic gate: no LLM judging here.

Usage: python -m evals.run --fixture evals/fixtures/mass_sensitivity.yaml
Exit 0 on pass; 1 on fail with an expected-vs-observed diff.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml  # pyyaml


# ---------------------------------------------------------------------------
# Integration hooks — the ONLY two places that touch the mission kernel.
# ---------------------------------------------------------------------------

def start_mission(contract: dict, backend: str) -> str:
    """TODO: bind to the mission kernel (sdk/chief_engineer) start API.

    Must return the mission id. Raise on refusal — a fixture that cannot start
    is a failure, not a skip.
    """
    raise NotImplementedError("bind to chief_engineer mission start")


def read_terminal_evidence(mission_id: str) -> dict:
    """TODO: bind to the kernel's event/read API once the mission is terminal.

    Return: {
      "terminal_status": str,
      "metrics": {variant_name: {metric: value}},
      "constraints": [{"metric": ..., "observed": ..., "violation": ...}],
      "events": {"worker.provisioned": int, "agent.completed": int,
                 "agent.failed": int, "artifact.transferred": int},
    }
    """
    raise NotImplementedError("bind to chief_engineer event read")


# ---------------------------------------------------------------------------

def _close(a: float, b: float, rel_tol: float) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.0)


def check(fixture: dict, evidence: dict) -> list[str]:
    errs: list[str] = []
    exp = fixture["expected"]

    if evidence["terminal_status"] != exp["terminal_status"]:
        errs.append(f"terminal_status: expected {exp['terminal_status']}, "
                    f"got {evidence['terminal_status']}")

    base = evidence["metrics"].get("baseline", {})
    for d in exp.get("deltas", []) or []:
        metric, variant = d["metric"], d["variant"]
        try:
            observed = evidence["metrics"][variant][metric] - base[metric]
        except KeyError as e:
            errs.append(f"missing metric for delta check: {e}")
            continue
        if not _close(observed, d["value"], d["rel_tol"]):
            errs.append(f"delta {variant}.{metric}: expected {d['value']} "
                        f"(rel_tol {d['rel_tol']}), got {observed:+.4g}")

    for rule in exp.get("ordering", []) or []:
        left, _, right = rule.partition(">")
        left, right = left.strip(), right.strip()
        try:
            metric = exp["deltas"][0]["metric"]
            lv = evidence["metrics"][left][metric] - base[metric]
            rv = evidence["metrics"][right][metric] - base[metric]
            if not lv > rv:
                errs.append(f"ordering violated: {left} ({lv:+.4g}) !> {right} ({rv:+.4g})")
        except KeyError as e:
            errs.append(f"missing metric for ordering check: {e}")

    if exp.get("constraints_satisfied"):
        for c in evidence.get("constraints", []):
            if not math.isfinite(c["observed"]) or c["violation"] != 0:
                errs.append(f"constraint {c['metric']}: observed {c['observed']}, "
                            f"violation {c['violation']}")

    inv, ev = fixture["invariants"], evidence["events"]
    if ev.get("worker.provisioned", 0) < inv["worker_provisioned_min"]:
        errs.append("invariant: worker.provisioned below minimum")
    if ev.get("agent.completed", 0) < inv["agent_completed_min"]:
        errs.append("invariant: agent.completed below minimum")
    if ev.get("agent.failed", 0) > inv["agent_failed_allowed"]:
        errs.append("invariant: unexplained agent.failed present")
    if inv.get("artifact_transferred_required") and ev.get("artifact.transferred", 0) < 1:
        errs.append("invariant: artifact.transferred required but absent")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()

    fixture = yaml.safe_load(Path(args.fixture).read_text())
    backend = args.backend or fixture["backend"]

    mission_id = start_mission(fixture["contract"], backend)
    evidence = read_terminal_evidence(mission_id)
    errs = check(fixture, evidence)

    if errs:
        print(f"FAIL {fixture['name']} (mission {mission_id})")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"PASS {fixture['name']} (mission {mission_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
