"""Master agent — turn a natural-language question into a fleet plan.

The dashboard should let you ASK, not click. This parses a question into a plan:
which wingspan values to evaluate and therefore how many workers to spin up. The
orchestrator then dispatches one value per worker in parallel and waits.

Handles phrasings like:
  "sweep the wingspan from 8 to 20 in 6 points and plot the mass"
  "how does mass change as wingspan goes from 10 to 40?"
  "compare the mass at wingspans 10, 14, 18, 22"
  "what's the mass at a 12m wingspan?"           (single point)

Regex-first (deterministic, no API); if nothing matches, returns a helpful error
the UI can show. Kept dependency-free so the orchestrator stays stdlib-only.
"""
from __future__ import annotations

import re

_NUM = r"-?\d+(?:\.\d+)?"
_RANGE = re.compile(rf"(?:from\s+)?({_NUM})\s*(?:m|meters|metres)?\s*(?:to|-|–|through|→)\s*({_NUM})", re.I)
_LIST = re.compile(rf"(?:wingspans?|spans?|values?|at)\s*[:=]?\s*((?:{_NUM}\s*,\s*){{1,}}{_NUM})", re.I)
_POINTS = re.compile(r"(\d+)\s*points?", re.I)
_SINGLE = re.compile(rf"(?:to|=|of|at)\s*({_NUM})\s*(?:m|meters|metres)?\b", re.I)
_WING = re.compile(r"(wing\s*spans?|\bspans?\b)", re.I)


def plan(question: str, max_workers: int = 12) -> dict:
    """Return a plan {ok, values, workers, note} or {ok:False, error}."""
    q = question.strip()
    if not q:
        return {"ok": False, "error": "Ask a question, e.g. 'sweep wingspan 8 to 20 in 6 points'."}
    if not _WING.search(q):
        return {"ok": False, "error": "I can currently vary the wing span. "
                "Try: 'sweep wingspan from 8 to 20 in 6 points'."}

    # explicit list: "spans 10, 14, 18, 22"
    m = _LIST.search(q)
    if m:
        values = [float(x) for x in re.findall(_NUM, m.group(1))]
        return _finalize(values, max_workers, "explicit list")

    # range sweep: "from 8 to 20 [in N points]"
    m = _RANGE.search(q)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        n = int(_POINTS.search(q).group(1)) if _POINTS.search(q) else _default_points(lo, hi)
        values = _linspace(lo, hi, n)
        return _finalize(values, max_workers, f"sweep {lo}-{hi} in {n} points")

    # single point: "at 12m" / "to 12"
    m = _SINGLE.search(q)
    if m:
        return _finalize([float(m.group(1))], max_workers, "single point")

    return {"ok": False, "error": "Couldn't find span values. Try "
            "'sweep wingspan from 8 to 20 in 6 points' or 'spans 10, 14, 18'."}


def _finalize(values: list[float], max_workers: int, note: str) -> dict:
    values = [v for v in values if v > 0][:max_workers]
    if not values:
        return {"ok": False, "error": "No positive span values found."}
    return {"ok": True, "values": values, "workers": len(values),
            "note": f"{note}: {len(values)} run(s) across {len(values)} worker(s)"}


def _default_points(lo: float, hi: float) -> int:
    return 5


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n <= 1:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [round(lo + step * i, 3) for i in range(n)]
