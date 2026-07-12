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

Reliability model (verified the hard way): each measurement runs in its OWN
fresh OpenVSP session, with parameter edits applied BEFORE any analysis dialog
is opened. Editing a parameter then measuring in a pristine session updates the
geometry correctly; reusing a session (stale Mass Prop dialog, accumulated FLTK
state) freezes the result. So we relaunch per measurement — slower, but right.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
import lifecycle as L      # noqa: E402
from replay import replay  # noqa: E402

from . import registry as R
from . import executor as E

# Baseline results are keyed by (model, analyses) and persisted. The pristine
# model never changes, so its mass/CG/area are measured ONCE and reused — this
# removes one full OpenVSP launch per question.
_BASELINE_CACHE = os.path.join(os.path.dirname(__file__), "baseline_cache.json")


def _load_baseline(key):
    if not os.path.exists(_BASELINE_CACHE):
        return None
    try:
        return json.load(open(_BASELINE_CACHE)).get(key)
    except Exception:
        return None


def _save_baseline(key, results):
    data = {}
    if os.path.exists(_BASELINE_CACHE):
        try:
            data = json.load(open(_BASELINE_CACHE))
        except Exception:
            data = {}
    data[key] = results
    json.dump(data, open(_BASELINE_CACHE, "w"), indent=2)


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
        if a == "massprop":
            # the proven trajectory: menu open -> Compute -> read (works in a
            # fresh session where edits were applied first)
            log = replay("massprop", reset=True, slices="20")
            hits = [e for e in log if e.get("op") == "read_results"]
            merged.update(hits[0]["results"] if hits else {})
            continue
        fn = E.ANALYSES.get(a)
        if fn is None:
            merged[f"_{a}_error"] = "analysis not wired for GUI (likely slow/solver)"
            continue
        merged.update(fn() or {})
    return merged


def _pick(results, output_key):
    """Extract the fields for one output from a merged results dict."""
    return {f: results.get(f) for f in R.OUTPUTS[output_key]["fields"]}


def _fresh_measure(model, changes_to_apply, analyses):
    """Relaunch OpenVSP fresh, apply changes to the geometry FIRST, then measure.

    Order is load-bearing (verified): changing a parameter and measuring in a
    PRISTINE session updates the geometry correctly; measuring first (opening the
    Mass Prop dialog) and then editing leaves the result frozen. So every
    measurement gets its own clean session with edits applied before any analysis
    dialog is opened. Returns (merged_results, [(change, before, target)])."""
    L.quit(); time.sleep(1.0)
    L.launch(model)
    E.sweep_dialogs()

    applied = []
    for ch in changes_to_apply:
        before = E.read_input(ch.input_key)   # current (pristine) value
        target = ch.target(before)
        E.set_input(ch.input_key, target)     # edit BEFORE any analysis dialog
        applied.append((ch, before, target))

    return _measure(analyses), applied


def run(req: Request, model="boeing777200.vsp3") -> dict:
    """Execute a request via relaunch-per-measurement (reliable, ~1 launch each).

    Baseline and the modified design each run in their own fresh OpenVSP session,
    so results are always correct (no stale-dialog / state-corruption issues)."""
    t0 = time.time()

    slow = [o for o in req.outputs if R.OUTPUTS[o]["speed"] == "slow"]
    if slow and req.fast_only:
        return {"error": f"outputs {slow} need an external solver (minutes, not <15s). "
                         f"Re-ask with fast_only=False to run them.",
                "outputs": req.outputs}

    analyses = _needed_analyses(req.outputs)
    bkey = f"{model}|{','.join(sorted(analyses))}"

    # baseline: reuse the cached pristine measurement if we have it (saves a
    # full launch); otherwise measure it once in a fresh session and cache.
    base = _load_baseline(bkey)
    cached = base is not None
    if not cached:
        base, _ = _fresh_measure(model, [], analyses)
        _save_baseline(bkey, base)

    # modified: fresh session, changes applied first
    after, applied = _fresh_measure(model, req.changes, analyses)

    report = {"changes": [], "outputs": {}, "baseline_cached": cached, "seconds": None}
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

    report["seconds"] = round(time.time() - t0, 1)
    return report
