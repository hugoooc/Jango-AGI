"""Trade study orchestrator — compare an output across geometry variants, GUI-only.

Composes the GUI building blocks into the thing the user asked for:

  "do a trade study on the mass between a 10% increase in wingspan and a
   10% increase in tail size"

A variant is: {geom, tab, field, field_desc, op, amount}. For each variant we:
  1. reload the baseline model fresh (File>Open) so changes never compound
  2. open the geom's editor, go to the tab, READ the field's current value
  3. compute the new value (op: 'scale' *(1+amount) or 'set' =amount or 'delta' +amount)
  4. SET it in the GUI
  5. run Mass Properties and READ the result off the screen
  6. record {variant, param_before, param_after, mass, cg, ...}

Then it prints a comparison table + which variant changed the metric most.

Everything is Holo + clicks on the live window. No openvsp API anywhere.

Usage (as a library):
  from bot.trade_study import study, VARIANTS_EXAMPLE
  study('boeing777200.vsp3', VARIANTS_EXAMPLE)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.dirname(__file__))
import desktop as d          # noqa: E402
import geom as G             # noqa: E402
import lifecycle as L        # noqa: E402
from replay import replay    # noqa: E402


# Example spec matching the user's prompt. `field_desc` is what Holo grounds/reads.
VARIANTS_EXAMPLE = [
    {"name": "baseline", "geom": None},
    {"name": "+10% wingspan", "geom": "Wing", "tab": "Plan",
     "field_desc": "the Span numeric value field in the Total Planform panel (top row)",
     "op": "scale", "amount": 0.10},
    {"name": "+10% tail size", "geom": "horizontal stabilizer", "tab": "Plan",
     "field_desc": "the Span numeric value field in the Total Planform panel (top row)",
     "op": "scale", "amount": 0.10,
     "tree_desc": "the horizontal stabilizer item in the geometry tree"},
]


def _close_dialogs():
    """Close the Mass Prop and geom-editor windows so they don't cover the
    Geom Browser tree when the next variant opens a geom. Pure GUI (clicks the
    window's close button by AppleScript). Idempotent."""
    import subprocess
    for sig in ((300, 508), (460, 828)):   # Mass Prop, geom editor
        win = G.W.find_by_size(*sig)
        if win:
            subprocess.run(["osascript", "-e",
                f'tell application "System Events" to tell process "vsp" to '
                f'click button 1 of window {win["idx"]}'], capture_output=True)
            time.sleep(0.4)


def _reload_baseline(model):
    """Reopen the pristine model via GUI File>Open (so variant edits reset).
    Simplest robust path: quit + relaunch. On this machine relaunch is the only
    fully reliable reset for FLTK dialogs, and it keeps the demo honest (each
    variant starts from the true baseline)."""
    L.quit()
    time.sleep(1.0)
    L.launch(model)


def _apply_variant(v):
    """Open geom, read current field, compute + set new value. Returns
    (before, after) or (None, None) for the baseline."""
    if not v.get("geom"):
        return None, None
    G.open_geom(v["geom"], v.get("tree_desc"))
    G.goto_tab(v["tab"])
    before = G.read_field(v["field_desc"])
    if before is None:
        raise RuntimeError(f"could not read {v['geom']} {v['field_desc']!r} — "
                           f"is the editor/tab actually open?")
    op, amt = v.get("op", "scale"), v.get("amount", 0)
    if op == "scale":
        after = round(before * (1 + amt), 4)
    elif op == "delta":
        after = round(before + amt, 4)
    else:  # set
        after = amt
    # set, then VERIFY it stuck (retry once). Loud failure beats a silent
    # baseline result.
    for attempt in range(2):
        G.set_field(v["field_desc"], after)
        chk = G.read_field(v["field_desc"])
        if chk is not None and abs(chk - after) < max(1e-3, abs(after) * 1e-3):
            return before, after
    raise RuntimeError(f"set {v['field_desc']!r}={after} did not stick "
                       f"(read back {chk}); aborting to avoid a wrong result")


def _measure_mass(slices=20, traj="massprop", reset=True):
    """Run Mass Properties via the recorded GUI trajectory and read results.
    reset=False right after a field edit: the reset esc would revert the FLTK
    field and silently undo the geometry change."""
    log = replay(traj, reset=reset, slices=str(slices))
    hits = [e for e in log if e.get("op") == "read_results"]
    return hits[0]["results"] if hits else {}


def study_fast(model, variants=VARIANTS_EXAMPLE, slices=20):
    """FAST trade study: launch OpenVSP ONCE, and between variants REVERT the
    changed field back to its baseline value in the GUI instead of relaunching.
    Same results, but pays one ~40s launch instead of one per variant.

    Relies on read-before-write: _apply_variant reads `before`, so we can
    restore it. Still 100% GUI (Holo + clicks) — no API, no file reload."""
    L.launch(model)
    records = []
    base_res = _measure_mass(slices, "massprop_fast")
    records.append({"variant": "baseline", "param_before": None,
                    "param_after": None, "mass": base_res.get("Total_Mass"),
                    "cg_x": base_res.get("X_Cg"), "izz": base_res.get("Izz"),
                    "results": base_res})
    print(f"  baseline  Total Mass = {base_res.get('Total_Mass')}  X_Cg = {base_res.get('X_Cg')}")

    for v in variants:
        if not v.get("geom"):
            continue
        print(f"\n=== variant: {v['name']} ===")
        _close_dialogs()                       # clear Mass Prop off the tree
        before, after = _apply_variant(v)      # reads before, sets after
        print(f"  {v['geom']} {v.get('field_desc','')[:30]}: {before} -> {after}")
        res = _measure_mass(slices, "massprop_fast", reset=False)
        records.append({"variant": v["name"], "param_before": before,
                        "param_after": after, "mass": res.get("Total_Mass"),
                        "cg_x": res.get("X_Cg"), "izz": res.get("Izz"),
                        "results": res})
        print(f"  Total Mass = {res.get('Total_Mass')}  |  X_Cg = {res.get('X_Cg')}")
        # revert the field so the next variant starts from the true baseline
        if before is not None:
            _close_dialogs()
            G.open_geom(v["geom"], v.get("tree_desc"))
            G.goto_tab(v["tab"])
            G.set_field(v["field_desc"], before)

    _report(records)
    L.quit()
    return records


def study(model, variants=VARIANTS_EXAMPLE, slices=20):
    """Run the full GUI trade study. Returns a list of per-variant records."""
    records = []
    for v in variants:
        print(f"\n=== variant: {v['name']} ===")
        _reload_baseline(model)
        before, after = _apply_variant(v)
        if before is not None:
            print(f"  {v['geom']} {v.get('field_desc','')[:30]}: {before} -> {after}")
        res = _measure_mass(slices)
        rec = {"variant": v["name"], "param_before": before,
               "param_after": after, "mass": res.get("Total_Mass"),
               "cg_x": res.get("X_Cg"), "izz": res.get("Izz"), "results": res}
        records.append(rec)
        print(f"  Total Mass = {rec['mass']}  |  X_Cg = {rec['cg_x']}")

    _report(records)
    return records


def _report(records):
    base = next((r for r in records if r["variant"] == "baseline"), None)
    print("\n" + "=" * 66)
    print("  TRADE STUDY — Mass sensitivity")
    print("=" * 66)
    print(f"  {'variant':20} {'mass':>10} {'d mass':>10} {'X_Cg':>9}")
    for r in records:
        m = r["mass"]
        dm = "" if (base is None or m is None or base["mass"] is None) \
            else f"{m - base['mass']:+.2f}"
        print(f"  {r['variant']:20} {str(m):>10} {dm:>10} {str(r['cg_x']):>9}")
    # which non-baseline variant moved mass most
    deltas = [(r["variant"], abs(r["mass"] - base["mass"]))
              for r in records if base and r["mass"] and r is not base]
    if deltas:
        win = max(deltas, key=lambda t: t[1])
        print(f"\n  Largest mass impact: {win[0]} ({win[1]:+.2f})")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "boeing777200.vsp3"
    study(model)
