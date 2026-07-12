"""Trajectory: the shared record/replay data model.

The idea (the user's design): run a workflow ONCE through the GUI with Holo
grounding each control, BAKE the resulting coordinate into every step, then
replay the exact same coordinates forever with NO vision.

A trajectory is a flat, ordered list of steps. Each step is one primitive
action. Steps that touch a screen location carry a baked {x, y} in LOGICAL
POINTS (what cliclick uses) plus the `desc` Holo was asked, so the coordinate
can be re-grounded later if the layout ever changes.

Coordinate steps are ANCHORED to a window, not the raw screen. Each carries
`off`: [dx, dy] = the offset from the target window's top-left corner, plus the
window `anchor` size signature used to re-find it at replay. This makes replay
survive the dialog opening at a different position: replay re-reads the live
window rect and reconstructs the absolute point. (If `anchor` is absent the
step falls back to absolute x/y for backward compatibility.)

Step ops (mirror cu/desktop.py primitives):
  {"op":"menu",   "path":["Analysis","Aero","VSPAERO..."]}   # no coords, AppleScript
  {"op":"click",  "off":[261,107],"anchor":[1000,828],"desc":"Launch Solver","double":false}
  {"op":"set_field","off":[..,..],"anchor":[1000,828],"desc":"Span value box","value":"{span}"}
  {"op":"type",   "text":"{span}"}
  {"op":"key",    "name":"return"}
  {"op":"cmd",    "letter":"s","shift":false}
  {"op":"wait",   "seconds":2.0}
  {"op":"wait_console","off":[..,..],"anchor":[1000,828],"desc":"solver console","contains":"Done","timeout":120}

`anchor` is the [w, h] size signature of the window the offset is relative to.
{name} placeholders in `value`/`text` are filled from replay kwargs, so ONE
recorded trajectory parameterizes (e.g. any span value) without re-recording.
"""
import os
import json
import re

HERE = os.path.dirname(__file__)
TRAJ_DIR = os.path.join(HERE, "trajectories")
os.makedirs(TRAJ_DIR, exist_ok=True)

_PLACE = re.compile(r"\{(\w+)\}")

# ops that carry a baked screen coordinate
COORD_OPS = {"click", "set_field", "fill_field"}


def path_for(name):
    return os.path.join(TRAJ_DIR, f"{name}.json")


def load(name):
    with open(path_for(name)) as f:
        return json.load(f)


def save(name, steps, meta=None):
    doc = {"name": name, "meta": meta or {}, "steps": steps}
    with open(path_for(name), "w") as f:
        json.dump(doc, f, indent=2)
    return path_for(name)


def fill(value, kwargs):
    """Substitute {name} placeholders from kwargs (leaves unknown ones intact)."""
    if isinstance(value, str):
        return _PLACE.sub(lambda m: str(kwargs.get(m.group(1), m.group(0))), value)
    return value


def params_of(steps):
    """Every {name} placeholder referenced across the trajectory's steps."""
    names = set()
    for s in steps:
        for key in ("value", "text"):
            v = s.get(key)
            if isinstance(v, str):
                names.update(_PLACE.findall(v))
    return sorted(names)
