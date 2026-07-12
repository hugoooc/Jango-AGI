"""Executor — the GUI actuator the dispatcher calls to set inputs and read outputs.

Robust, registry-driven, GUI-only. Reliability lessons from fastquery baked in:
  - always sweep stray dialogs before acting (baked coords need a clean layout)
  - opening a geom editor: deselect-then-double-click (a double-click on an
    already-selected FLTK row enters rename, not open) + verify it opened
  - after selecting a tab, VERIFY the field set actually stuck (read-back) —
    a mis-clicked tab makes the field coordinate hit nothing
  - one Holo vision read per measurement; everything else is baked cliclick

Nothing here calls OpenVSP's API — it operates the interface.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import desktop as d          # noqa: E402
import windows as W          # noqa: E402

from . import registry as R  # noqa: E402

_CORE = (R.GEOM_BROWSER, (757, 802), (757, 855))


def _is_core(w):
    return any(abs(w["w"] - c[0]) < 40 and abs(w["h"] - c[1]) < 40 for c in _CORE)


def sweep_dialogs():
    """Close every transient dialog (keep only core windows). Re-lists after
    each close since indices renumber."""
    import subprocess
    for _ in range(8):
        t = next((w for w in W.list_windows() if not _is_core(w)), None)
        if t is None:
            return
        subprocess.run(["osascript", "-e",
            f'tell application "System Events" to tell process "vsp" to '
            f'click button 1 of window {t["idx"]}'], capture_output=True)
        time.sleep(0.25)


def _abs(sig, off):
    win = W.find_by_size(*sig)
    if not win:
        raise RuntimeError(f"window {sig} not open")
    return W.to_absolute(off[0], off[1], win)


def open_geom(geom_key):
    """Open a geom's editor via its tree row. Verifies the editor appeared."""
    g = R.GEOMS[geom_key]
    d.focus_app("vsp"); time.sleep(0.2)
    W.raise_by_size(*R.GEOM_BROWSER); time.sleep(0.15)
    bx, by = _abs(R.GEOM_BROWSER, g["tree_off"])
    for _ in range(3):
        d.key("esc")
        d.click(bx, by + 44)            # deselect (sibling row)
        time.sleep(0.2)
        d.click(bx, by, double=True)    # fresh double-click opens editor
        time.sleep(0.6)
        if W.find_by_size(*R.GEOM_EDITOR):
            return
    raise RuntimeError(f"could not open editor for {geom_key}")


def goto_tab(tab):
    x, y = _abs(R.GEOM_EDITOR, R.TABS[tab])
    d.click(x, y); time.sleep(0.4)


def set_input(input_key, value):
    """Set one input variable to an absolute value through the GUI. Opens the
    right geom + tab, sets the field, and VERIFIES the value stuck (retry once).
    Returns the read-back value."""
    spec = R.INPUTS[input_key]
    sweep_dialogs()
    open_geom(spec["geom"])
    goto_tab(spec["tab"])
    for attempt in range(2):
        x, y = _field_xy(spec)
        d.triple_click(x, y); d.type_text(str(value)); d.commit_return()
        time.sleep(0.3)
        back = _read_field(spec)
        if back is not None and abs(back - float(value)) < max(1e-3, abs(float(value)) * 1e-3):
            return back
        goto_tab(spec["tab"])           # re-assert tab in case the click missed
    raise RuntimeError(f"set {input_key}={value} did not stick (read back {back})")


def read_input(input_key):
    """Read an input's current value (used for relative changes like +10%)."""
    spec = R.INPUTS[input_key]
    sweep_dialogs()
    open_geom(spec["geom"])
    goto_tab(spec["tab"])
    return _read_field(spec)


# ---- analyses: run + read result (one vision read) --------------------------

def run_massprop():
    """Analysis > Mass Prop... > Compute, then read the Results panel."""
    sweep_dialogs()
    d.focus_app("vsp"); time.sleep(0.2)
    d.click_menu(["Analysis", "Mass Prop..."]); time.sleep(0.6)
    cx, cy = _abs(R.MASSPROP, (145, 180))     # Compute button
    d.click(cx, cy); time.sleep(0.6)
    return _read_panel("Mass Properties Results",
                       ["Total_Mass", "X_Cg", "Y_Cg", "Z_Cg", "Ixx", "Iyy", "Izz"])


def run_compgeom():
    """Analysis > CompGeom... > Execute, then read wetted area/volume."""
    sweep_dialogs()
    d.focus_app("vsp"); time.sleep(0.2)
    d.click_menu(["Analysis", "CompGeom..."]); time.sleep(0.6)
    # CompGeom dialog: find + click Execute, then read the totals off screen
    _, im = d.screenshot("cg")
    try:
        ex, ey = d.locate(im, "the Execute button in the CompGeom dialog")
        d.click(ex, ey); time.sleep(1.0)
    except Exception:
        pass
    return _read_panel("CompGeom Results",
                       ["Total_Wet_Area", "Total_Wet_Vol", "Total_Theo_Area"])


ANALYSES = {"massprop": run_massprop, "compgeom": run_compgeom}


# ---- private helpers --------------------------------------------------------

def _field_xy(spec):
    if spec.get("field_off"):
        return _abs(R.GEOM_EDITOR, spec["field_off"])
    # ground on demand (two-tier fallback) and cache back onto the spec
    _, im = d.screenshot("gnd")
    ax, ay = d.locate(im, spec["desc"])
    ed = W.find_by_size(*R.GEOM_EDITOR)
    spec["field_off"] = (ax - ed["x"], ay - ed["y"])
    return ax, ay


def _read_field(spec):
    _, im = d.screenshot("rf")
    ans = d.ask(f"Read the numeric value in {spec['desc']}. Answer ONLY the number.",
                im, max_tokens=500)
    m = re.search(r"-?\d+\.?\d*", ans or "")
    return float(m.group(0)) if m else None


def _read_panel(panel, fields):
    _, im = d.screenshot("rp")
    ans = d.ask(f"Read the OpenVSP {panel} panel. Return ONLY JSON with these keys "
                f"(numbers shown): {list(fields)}.", im, max_tokens=700)
    m = re.search(r"\{.*\}", ans or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
