"""GUI geometry driver — read/set any geom parameter through the interface.

This is the vocabulary a trade study needs, all via the GUI (Holo + clicks,
never the API):

  open_geom(name)        double-click a geom in the Geom Browser tree -> editor
  goto_tab(tab)          click a tab in the geom editor (Gen/Plan/Sect/XForm/...)
  read_field(desc)       Holo reads one numeric field's value off the screen
  read_panel(fields)     Holo reads several labelled values at once -> dict
  set_field(desc, value) triple-click the field, type, commit (FLTK-safe)

Coordinates are grounded by Holo on first use and cached in knowledge/
gui_anchors.json keyed by (geom-editor, description), so repeated runs reuse
them with no vision — the same record-once/replay pattern as the bot, applied
to the geom editor. The editor window is matched by its size signature so
anchors survive it opening at a different position.

Everything here reads/writes the LIVE window. Nothing touches openvsp/the API.
"""
import os
import sys
import json
import time
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.dirname(__file__))
import desktop as d          # noqa: E402
import windows as W          # noqa: E402

KNOW = os.path.join(os.path.dirname(__file__), "..", "legacypilot", "knowledge")
ANCHORS = os.path.join(KNOW, "gui_anchors.json")

# window size signatures (w, h) discovered on this machine
GEOM_BROWSER = (275, 673)
GEOM_EDITOR = (460, 828)


def _load():
    if os.path.exists(ANCHORS):
        return json.load(open(ANCHORS))
    return {}


def _save(a):
    os.makedirs(KNOW, exist_ok=True)
    json.dump(a, open(ANCHORS, "w"), indent=2)


def _focus():
    # Only re-raise if vsp doesn't already own the menu bar. In an uninterrupted
    # automated run it almost always does, so this skips a ~0.4s sleep per op.
    try:
        if "vsp" in [m.lower() for m in d.frontmost_menus()]:
            return
    except Exception:
        pass
    d.focus_app("vsp")
    time.sleep(0.3)
    d.require_frontmost("vsp")


def _ground(key, desc, win_sig):
    """Return absolute (x,y) for a control, grounding+caching on first sight.
    Cached as an offset from the window matching win_sig, so it survives moves."""
    anchors = _load()
    win = W.find_by_size(*win_sig)
    if key in anchors:
        off = anchors[key]["off"]
        if win:
            return W.to_absolute(off[0], off[1], win)
    # first sight: ground with Holo
    _, im = d.screenshot("ground")
    x, y = d.locate(im, desc)
    if win:
        off = list(W.to_offset(x, y, win))
        anchors[key] = {"off": off, "anchor": list(win_sig), "desc": desc}
        _save(anchors)
    return x, y


# --------------------------------------------------------------- operations

# analysis dialogs that overlap the Geom Browser tree; cleared before geom ops
_ANALYSIS_DIALOGS = [(300, 508), (1000, 828)]   # Mass Prop, VSPAERO


def clear_analysis_dialogs():
    """Close any analysis dialog covering the tree. Window discipline: baked
    tree coordinates are only valid when nothing overlaps the Geom Browser."""
    for sig in _ANALYSIS_DIALOGS:
        if W.close_by_size(*sig):
            time.sleep(0.35)


def open_geom(name, tree_desc=None):
    """Open a geom's editor by double-clicking it in the Geom Browser tree.
    Clears overlapping analysis dialogs first and raises the Geom Browser so the
    baked tree coordinate can't land on the wrong window."""
    _focus()
    clear_analysis_dialogs()
    W.raise_by_size(*GEOM_BROWSER); time.sleep(0.2)
    desc = tree_desc or f"the {name} item in the geometry tree list"
    x, y = _ground(f"tree::{name}", desc, GEOM_BROWSER)
    d.click(x, y, double=True)
    time.sleep(0.6)


def goto_tab(tab):
    """Click a tab in the geom editor top tab bar (Gen/XForm/Plan/Sect/...)."""
    _focus()
    x, y = _ground(f"tab::{tab}", f"the {tab} tab in the geom editor top tab bar",
                   GEOM_EDITOR)
    d.click(x, y)
    time.sleep(0.4)


def read_field(desc):
    """Holo reads a single numeric field value off the screen. Returns float."""
    _focus()
    _, im = d.screenshot("readf")
    ans = d.ask(f"In this OpenVSP screenshot, read the numeric value shown in "
                f"the field: {desc}. Answer with ONLY the number.", im, max_tokens=120)
    m = re.search(r"-?\d+\.?\d*", ans or "")
    return float(m.group(0)) if m else None


def read_panel(panel, fields):
    """Holo reads several labelled values at once -> {field: value}."""
    _focus()
    _, im = d.screenshot("readp")
    ans = d.ask(f"This is the {panel} panel of OpenVSP. Return ONLY a JSON object "
                f"with these keys: {fields}. Use the numbers shown next to each "
                f"label.", im, max_tokens=500)
    m = re.search(r"\{.*\}", ans or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {"_raw": ans}


def set_field(desc, value, win_sig=GEOM_EDITOR):
    """Set a numeric field (FLTK-safe: triple-click, type, AppleScript Return)."""
    _focus()
    key = f"field::{desc[:40]}"
    x, y = _ground(key, desc, win_sig)
    d.triple_click(x, y)
    d.type_text(str(value))
    d.commit_return()
    time.sleep(0.2)
