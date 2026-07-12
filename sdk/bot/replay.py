"""Replay bot: fire a recorded trajectory with ZERO grounding vision.

This is the dumb, fast, deterministic half. It loads a baked trajectory and
executes each step:

  - menu steps         -> AppleScript by name path (position-independent)
  - coordinate steps   -> re-read the target window's LIVE rect (AppleScript,
                          no vision), add the baked offset, click there. So if
                          the dialog opened at a different position than during
                          recording, the click still lands correctly.
  - type/key/cmd/wait  -> straight primitives

The only place it may look at pixels is `wait_console`, which polls Holo to
read a console area until a completion token appears (genuine async waiting,
not grounding, opt-in per trajectory).

Self-contained start: replay() resets to a known baseline (app frontmost, stray
modal dismissed) before running, so it does NOT assume the target dialog is
already open — the trajectory's own menu step opens it.

Safety: before the FIRST coordinate action, require OpenVSP to own the menu bar
(same guard as the adapter). Wrong app in front -> abort with NO clicks.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
sys.path.insert(0, os.path.dirname(__file__))
import desktop as d                       # noqa: E402
import windows as W                       # noqa: E402
from trajectory import load, fill, params_of  # noqa: E402


def _reset(app="vsp"):
    # Fast path: if vsp already owns the menu bar, a single esc to clear any
    # stray menu is enough — skip the double focus + long sleeps.
    try:
        if app in [m.lower() for m in d.frontmost_menus()]:
            d.key("esc"); time.sleep(0.15)
            return
    except Exception:
        pass
    d.focus_app(app); time.sleep(0.4)
    d.key("esc");     time.sleep(0.2)
    d.focus_app(app); time.sleep(0.2)


def _resolve(step):
    """Return the absolute (x, y) for a coordinate step, resolving a
    window-relative offset against the live window rect when present."""
    if "off" in step and "anchor" in step:
        w, h = step["anchor"]
        win = W.find_by_size(w, h)
        if win is None:
            raise RuntimeError(
                f"replay: no window matching size {step['anchor']} on screen — "
                f"is the dialog open? (step: {step.get('desc')})")
        return W.to_absolute(step["off"][0], step["off"][1], win)
    return step["x"], step["y"]           # legacy absolute step


def _read_results(step):
    """GUI-only results read: Holo reads a panel off the screen and returns the
    numbers as JSON. No file, no API — pure vision on the live window."""
    import json
    import re
    fields = step.get("fields", [])
    _, im = d.screenshot("replay_results")
    prompt = (f"This is the {step.get('panel','results')} panel of a desktop app. "
              f"Read these values and return ONLY a JSON object with exactly these "
              f"keys: {fields}. Use the numbers shown next to each label.")
    ans = d.ask(prompt, im, max_tokens=400)
    m = re.search(r"\{.*\}", ans or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {"_raw": ans}


def _wait_console(step, kwargs):
    token = step.get("contains", "Done")
    timeout = step.get("timeout", 120)
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, im = d.screenshot("replay_console")
        txt = d.ask(f"Read the console text near the bottom of this screenshot. "
                    f"Does it contain the word '{token}'? Answer YES or NO, then the "
                    f"last line you can read.", im, max_tokens=120)
        low = (txt or "").lower()
        if token.lower() in low or low[:5].startswith("yes"):
            return True
        time.sleep(3)
    return False


def replay(name, guard=True, app="vsp", reset=True, **kwargs):
    """Execute trajectory `name`, substituting {placeholders} from kwargs."""
    doc = load(name)
    steps = doc["steps"]
    needed = params_of(steps)
    missing = [p for p in needed if p not in kwargs]
    if missing:
        raise ValueError(f"trajectory '{name}' needs params {needed}; missing {missing}")

    if reset:
        _reset(app)
    guarded = False
    log = []

    for i, s in enumerate(steps):
        op = s["op"]
        is_coord = op in ("click", "set_field", "fill_field")

        if guard and is_coord and not (op == "click" and s.get("no_guard")):
            # Re-raise OpenVSP first (Terminal/other apps steal focus between
            # steps), THEN hard-verify it owns the menu bar before any click.
            d.focus_app(app); time.sleep(0.4)
            d.require_frontmost(app)      # raises (no clicks) if still wrong app
            guarded = True

        if op == "menu":
            d.click_menu(s["path"])
            detail = s["path"]
        elif op == "click":
            x, y = _resolve(s)
            d.click(x, y, double=s.get("double", False))
            detail = f"({x},{y})"
        elif op == "set_field":
            x, y = _resolve(s)
            d.triple_click(x, y); d.type_text(str(fill(s["value"], kwargs))); d.commit_return()
            detail = f"({x},{y})={fill(s['value'], kwargs)}"
        elif op == "fill_field":
            x, y = _resolve(s)
            d.triple_click(x, y); d.type_text(str(fill(s["value"], kwargs)))
            detail = f"({x},{y})={fill(s['value'], kwargs)}"
        elif op == "type":
            d.type_text(str(fill(s["text"], kwargs))); detail = fill(s["text"], kwargs)
        elif op == "key":
            d.key(s["name"]); detail = s["name"]
        elif op == "cmd":
            d.cmd_key(s["letter"], shift=s.get("shift", False)); detail = f"cmd-{s['letter']}"
        elif op == "wait":
            time.sleep(s["seconds"]); detail = f"{s['seconds']}s"
        elif op == "wait_console":
            ok = _wait_console(s, kwargs)
            log.append({"step": i, "op": op, "ok": ok})
            print(f"  [{i}] wait_console '{s.get('contains')}' -> {'DONE' if ok else 'TIMEOUT'}")
            continue
        elif op == "read_results":
            vals = _read_results(s)
            log.append({"step": i, "op": op, "results": vals})
            print(f"  [{i}] read_results -> {vals}")
            continue
        else:
            raise ValueError(f"unknown op {op!r} in trajectory")

        log.append({"step": i, "op": op, "detail": detail})
        print(f"  [{i}] {op} {detail}")

    return log


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python bot/replay.py <trajectory_name> [k=v ...]")
        sys.exit(1)
    name = sys.argv[1]
    kw = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    replay(name, **kw)
