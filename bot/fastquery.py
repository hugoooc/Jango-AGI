"""fastquery — sub-15s natural-language mass queries on OpenVSP, GUI-only.

Answers questions like "what's the impact on mass if I change the wingspan to 12m?"
by driving the live OpenVSP GUI with BAKED coordinates (zero grounding vision) and
a SINGLE vision read of the result panel.

Speed strategy (the whole point):
  - No discovery at query time: control coordinates for the 737 layout are baked
    (window-relative offsets, grounded once). Replay is instant cliclick.
  - Absolute-set questions ("set span to 12") need NO read of the current value —
    saves a vision round-trip. Only the final result needs vision (~2.5s).
  - Minimal sleeps: just enough for OpenVSP to repaint, tuned empirically.
  - One focus at the start, not per action.

Requires OpenVSP already running with boeing777200.vsp3 (the demo keeps it warm).
Baseline mass/CG are cached on first call so deltas are instant thereafter.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.dirname(__file__))
import desktop as d          # noqa: E402
import windows as W          # noqa: E402

# window size signatures (see bot/windows.py)
GEOM_BROWSER = (275, 673)
GEOM_EDITOR = (460, 828)
MASSPROP = (300, 508)

# baked window-relative offsets, grounded once via Holo (bot/geom.py cache).
# These are the ONLY control locations the fast path needs.
OFF = {
    "wing_tree": (GEOM_BROWSER, (64, 170)),         # "Wing" row in the geom tree
    "plan_tab": (GEOM_EDITOR, (203, 65)),           # "Plan" tab in the wing editor
    "span_field": (GEOM_EDITOR, (426, 108)),        # Span value box, Total Planform
    "compute_btn": (MASSPROP, (145, 180)),          # Compute button (mass prop dialog)
}

_baseline = None   # cached {mass, cg_x, ...} of the unmodified model


def _abs(key):
    (sig, off) = OFF[key]
    win = W.find_by_size(*sig)
    if not win:
        raise RuntimeError(f"window {sig} not open (need it for {key})")
    return W.to_absolute(off[0], off[1], win)


def _fast_click(key, double=False):
    x, y = _abs(key)
    d.click(x, y, double=double)


_CORE_WINDOWS = (GEOM_BROWSER, (757, 802), (757, 855))   # browser, GL view, main


def _is_core(w):
    return any(abs(w["w"] - cw[0]) < 40 and abs(w["h"] - cw[1]) < 40 for cw in _CORE_WINDOWS)


def _sweep_dialogs():
    """Close every transient dialog (anything that isn't a core window), so
    stray/partial dialogs can't occupy a control's baked coordinate.

    Re-lists after each close: closing a window renumbers the higher indices, so
    a cached index list would close the wrong window. Idempotent, bounded."""
    import subprocess
    for _ in range(6):                       # bound: never more than a few dialogs
        transient = next((w for w in W.list_windows() if not _is_core(w)), None)
        if transient is None:
            break
        subprocess.run(["osascript", "-e",
            f'tell application "System Events" to tell process "vsp" to '
            f'click button 1 of window {transient["idx"]}'], capture_output=True)
        time.sleep(0.25)


def _open_wing_editor():
    """Open the Wing geom editor. OpenVSP's FLTK tree opens the editor on a
    double-click only when the row is NOT already selected (a double-click on an
    already-selected row enters inline-rename instead). So: Escape any rename,
    click a sibling row to move selection off Wing, then double-click Wing."""
    if W.find_by_size(*GEOM_EDITOR):
        return                              # already open
    bx, by = _abs("wing_tree")
    for attempt in range(3):
        d.key("esc")                        # exit any inline-rename
        d.click(bx, by + 44)                # sibling row below Wing -> deselect
        time.sleep(0.2)
        d.click(bx, by, double=True)        # fresh double-click opens the editor
        time.sleep(0.6)
        if W.find_by_size(*GEOM_EDITOR):
            return
    raise RuntimeError("could not open the Wing editor after 3 tries")


def _read_massprops():
    """One vision read of the Mass Prop result panel -> dict of floats."""
    _, im = d.screenshot("fq")
    # Holo is a reasoning model: it emits thought before the JSON, so a small
    # budget truncates the answer. Give headroom (desktop.ask already falls back
    # to the reasoning field when content is empty).
    ans = d.ask("Read the OpenVSP Mass Properties Results panel. Return ONLY JSON "
                "with keys Total_Mass, X_Cg, Y_Cg, Z_Cg using the numbers shown.",
                im, max_tokens=700)
    m = re.search(r"\{.*\}", ans or "", re.S)
    import json
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def set_span_and_measure(span_m):
    """Set wing span to an absolute value (metres) and measure mass — the fast path.
    No read of the current span (absolute set), one result vision read."""
    d.focus_app("vsp"); time.sleep(0.3)
    # clean slate: close Mass Prop + any stray dialog so baked coords are valid
    # and we always get a FRESH compute on the updated geometry.
    _sweep_dialogs()
    # open wing editor -> Plan tab -> set span (baked coords, instant)
    W.raise_by_size(*GEOM_BROWSER); time.sleep(0.15)
    _open_wing_editor()
    _fast_click("plan_tab"); time.sleep(0.3)
    x, y = _abs("span_field")
    d.triple_click(x, y); d.type_text(str(span_m)); d.commit_return(); time.sleep(0.3)
    # run mass properties on the UPDATED geometry (fresh dialog + fresh Compute)
    d.click_menu(["Analysis", "Mass Prop..."]); time.sleep(0.6)
    _fast_click("compute_btn"); time.sleep(0.5)
    return _read_massprops()


def baseline():
    """Measure (and cache) the unmodified model's mass/CG. Call once at startup."""
    global _baseline
    d.focus_app("vsp"); time.sleep(0.3)
    d.click_menu(["Analysis", "Mass Prop..."]); time.sleep(0.6)
    _fast_click("compute_btn"); time.sleep(0.5)
    _baseline = _read_massprops()
    return _baseline


def query_span(span_m):
    """Full query: set span, measure, and report the delta vs cached baseline."""
    t0 = time.time()
    res = set_span_and_measure(span_m)
    dt = time.time() - t0
    out = {"span_m": span_m, "result": res, "seconds": round(dt, 1)}
    if _baseline and res.get("Total_Mass") is not None:
        out["d_mass"] = round(res["Total_Mass"] - _baseline["Total_Mass"], 3)
        out["d_cg_x"] = round(res.get("X_Cg", 0) - _baseline.get("X_Cg", 0), 4)
    return out
