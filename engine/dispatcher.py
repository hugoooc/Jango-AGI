"""Dispatcher — turn a structured design request into a GUI-driven answer.

An agent (or the NL parser) produces a Request: a set of input changes and the
outputs to measure. The dispatcher:
  1. measures the requested outputs on the CURRENT (baseline) geometry
  2. applies every input change through the GUI
  3. measures the outputs again
  4. reports before/after/delta per output
  5. restores the changed inputs (so the next question starts clean)

It only runs the analyses actually needed (an output names its analysis; the
dispatcher runs each required analysis once, not once per output). It refuses —
loudly, up front — outputs tagged "slow" (VSPAERO) when the caller asked to stay
fast, so the jury is never surprised by a minutes-long solve.
"""
import time

from . import registry as R
from . import executor as E


class Change:
    """One input change. mode: 'set' (absolute) | 'scale' (x*(1+amt)) | 'delta' (+amt)."""
    def __init__(self, input_key, mode, amount):
        self.input_key = input_key
        self.mode = mode
        self.amount = amount

    def target(self, current):
        if self.mode == "set":
            return float(self.amount)
        if self.mode == "scale":
            return round(current * (1 + self.amount), 4)
        if self.mode == "delta":
            return round(current + self.amount, 4)
        raise ValueError(f"bad mode {self.mode}")


class Request:
    def __init__(self, changes, outputs, fast_only=True):
        self.changes = changes          # list[Change]
        self.outputs = outputs          # list[output_key]
        self.fast_only = fast_only


def _needed_analyses(output_keys):
    """The distinct analyses required to produce the requested outputs."""
    return list(dict.fromkeys(R.OUTPUTS[o]["analysis"] for o in output_keys))


def _measure(analyses):
    """Run each needed analysis once; merge their result dicts."""
    merged = {}
    for a in analyses:
        fn = E.ANALYSES.get(a)
        if fn is None:
            merged[f"_{a}_error"] = "analysis not wired for GUI (likely slow/solver)"
            continue
        merged.update(fn() or {})
    return merged


def _pick(results, output_key):
    """Extract the fields for one output from a merged results dict."""
    return {f: results.get(f) for f in R.OUTPUTS[output_key]["fields"]}


def run(req: Request) -> dict:
    """Execute a request and return a structured before/after/delta report."""
    t0 = time.time()

    slow = [o for o in req.outputs if R.OUTPUTS[o]["speed"] == "slow"]
    if slow and req.fast_only:
        return {"error": f"outputs {slow} need an external solver (minutes, not <15s). "
                         f"Re-ask with fast_only=False to run them.",
                "outputs": req.outputs}

    analyses = _needed_analyses(req.outputs)

    # 1. baseline measurement
    base = _measure(analyses)

    # 2. apply changes (record before/after of each input for restore)
    applied = []
    for ch in req.changes:
        before = E.read_input(ch.input_key) if ch.mode != "set" else None
        # for 'set' we still want the original to restore -> read it
        if before is None:
            before = E.read_input(ch.input_key)
        target = ch.target(before)
        E.set_input(ch.input_key, target)
        applied.append((ch, before, target))

    # 3. after measurement
    after = _measure(analyses)

    # 4. build the report
    report = {"changes": [], "outputs": {}, "seconds": None}
    for ch, before, target in applied:
        report["changes"].append({
            "input": ch.input_key, "mode": ch.mode, "amount": ch.amount,
            "before": before, "after": target,
        })
    for o in req.outputs:
        b, a = _pick(base, o), _pick(after, o)
        deltas = {k: (None if b.get(k) is None or a.get(k) is None
                      else round(a[k] - b[k], 4)) for k in R.OUTPUTS[o]["fields"]}
        report["outputs"][o] = {"before": b, "after": a, "delta": deltas}

    # 5. restore inputs to baseline
    for ch, before, _ in applied:
        try:
            E.set_input(ch.input_key, before)
        except Exception:
            pass

    report["seconds"] = round(time.time() - t0, 1)
    return report
