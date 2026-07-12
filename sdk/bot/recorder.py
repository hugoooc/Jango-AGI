"""Recorder: the GUI discovery pass.

You describe a workflow as a list of high-level intents. The recorder walks
them ONCE on the live app: for every intent that targets a control, it takes a
screenshot and asks Holo (cu/desktop.locate, the fixed grounding loop) WHERE
that control is, then BAKES that location into the step as an OFFSET from the
target window's top-left corner (window found by AppleScript, no vision). Menu/
type/key/wait/cmd intents need no vision and pass straight through.

The output is a trajectory JSON (see trajectory.py) the replay bot can fire
forever with zero Holo calls — and, because coords are window-relative, even
if the dialog opens at a different position next time.

Self-contained by design: pass `reset=True` (default) and recording begins
from a KNOWN baseline — OpenVSP brought frontmost, any stray modal dismissed
with Escape — so the trajectory does not assume anything was already open. The
intents themselves should then OPEN whatever dialog they need (via a `menu`
step), so replay reproduces the whole flow from that baseline.

Intent shapes (what you author):
  {"do":"menu",  "path":["Analysis","Aero","VSPAERO..."]}
  {"do":"click", "find":"the Launch Solver button", "win":[1000,828], "double":false}
  {"do":"set",   "find":"the Span value box on the far right", "win":[1000,828], "value":"{span}"}
  {"do":"type",  "text":"{file}"}
  {"do":"key",   "name":"return"}
  {"do":"cmd",   "letter":"s"}
  {"do":"wait",  "seconds":2.0}
  {"do":"wait_console","find":"the solver console text area","win":[1000,828],"contains":"Done","timeout":120}

`win` is the [w, h] size signature of the window the control lives in (e.g. the
VSPAERO dialog is ~1000x828). Coordinate intents must supply it so the recorder
can anchor the offset; omit it only for full-screen or single-window targets.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.dirname(__file__))
import desktop as d          # noqa: E402
import windows as W          # noqa: E402
from trajectory import save  # noqa: E402


def _reset(app="vsp"):
    """Bring the app frontmost and dismiss any stray modal, so recording starts
    from a known baseline no matter what was on screen."""
    d.focus_app(app)
    time.sleep(0.5)
    d.key("esc")             # close a stray dialog/menu if one is up
    time.sleep(0.3)
    d.focus_app(app)
    time.sleep(0.3)


def _anchor_offset(x, y, win_sig):
    """Convert an absolute grounded point to (dx, dy) offset from the window
    whose size matches win_sig. Returns (off, anchor) or (None, None) if the
    step is absolute (no win given)."""
    if not win_sig:
        return None, None
    win = W.find_by_size(win_sig[0], win_sig[1])
    if win is None:
        raise RuntimeError(f"recorder: no window matching size {win_sig} to anchor to")
    return list(W.to_offset(x, y, win)), list(win_sig)


def record(name, intents, app="vsp", reset=True, settle=0.8):
    """Walk `intents` once on the live app, grounding coords with Holo and
    baking them as window-relative offsets. Persists a trajectory. Returns the
    baked steps."""
    if reset:
        _reset(app)
    steps = []
    for i, intent in enumerate(intents):
        do = intent["do"]
        tag = f"[rec {i+1}/{len(intents)}] {do}"

        if do == "menu":
            d.click_menu(intent["path"])
            steps.append({"op": "menu", "path": intent["path"]})
            print(f"{tag} {intent['path']}")

        elif do in ("click", "set", "fill", "wait_console"):
            _, im = d.screenshot("rec")
            x, y = d.locate(im, intent["find"])          # vision — recording only
            off, anchor = _anchor_offset(x, y, intent.get("win"))
            loc = {"desc": intent["find"]}
            if off is not None:
                loc.update({"off": off, "anchor": anchor})
                where = f"off={off} of win{anchor}"
            else:
                loc.update({"x": x, "y": y})
                where = f"abs=({x},{y})"
            print(f"{tag} '{intent['find']}' -> {where}")

            if do == "click":
                step = {"op": "click", **loc,
                        "double": intent.get("double", False),
                        "no_guard": intent.get("no_guard", False)}
                d.click(x, y, double=step["double"])
            elif do == "set":
                step = {"op": "set_field", **loc, "value": intent["value"]}
                d.triple_click(x, y); d.type_text(str(intent["value"])); d.commit_return()
            elif do == "fill":
                step = {"op": "fill_field", **loc, "value": intent["value"]}
                d.triple_click(x, y); d.type_text(str(intent["value"]))
            else:  # wait_console
                step = {"op": "wait_console", **loc,
                        "contains": intent.get("contains", "Done"),
                        "timeout": intent.get("timeout", 120)}
            steps.append(step)

        elif do == "read_results":
            # Holo reads a whole panel off-screen at replay time; nothing to
            # ground now. Just record what to read.
            steps.append({"op": "read_results",
                          "panel": intent.get("panel", "results"),
                          "fields": intent["fields"]})
            print(f"{tag} panel={intent.get('panel','results')} fields={intent['fields']}")

        elif do == "type":
            d.type_text(str(intent["text"]))
            steps.append({"op": "type", "text": intent["text"]})
            print(f"{tag} {intent['text']!r}")

        elif do == "key":
            d.key(intent["name"])
            steps.append({"op": "key", "name": intent["name"]})
            print(f"{tag} {intent['name']}")

        elif do == "cmd":
            d.cmd_key(intent["letter"], shift=intent.get("shift", False))
            steps.append({"op": "cmd", "letter": intent["letter"],
                          "shift": intent.get("shift", False)})
            print(f"{tag} cmd-{intent['letter']}")

        elif do == "wait":
            time.sleep(intent["seconds"])
            steps.append({"op": "wait", "seconds": intent["seconds"]})
            print(f"{tag} {intent['seconds']}s")

        else:
            raise ValueError(f"unknown intent do={do!r}")

        time.sleep(settle)

    p = save(name, steps, meta={"app": app, "recorded_intents": len(intents),
                                "starts_from": "clean baseline (reset)" if reset else "current state"})
    print(f"\n[recorded] {len(steps)} baked steps -> {p}")
    return steps
