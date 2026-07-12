"""Seed the registry with the skills we PROVED this session on OpenVSP.

These are hand-authored from verified GUI interactions, so the system is
useful immediately. The discovery loop adds more of the same shape.
"""
from .skills import Registry

WING_FIELDS = [
    ("set_wing_span",   "Span",           "wing root section span",           "m"),
    ("set_wing_root_chord", "Root_Chord", "wing root chord length",           "m"),
    ("set_wing_tip_chord",  "Tip_Chord",  "wing tip chord length",            "m"),
    ("set_wing_sweep",  "Sweep",          "wing leading-edge sweep angle",    "deg"),
    ("set_wing_dihedral", "Dihedral",     "wing dihedral angle",              "deg"),
    ("set_wing_twist",  "Twist",          "wing section twist angle",         "deg"),
]


def field_skill(name, parm_name, summary, unit):
    label = parm_name.replace("_", " ")
    return {
        "name": name,
        "summary": f"Set the {summary}.",
        "kind": "field",
        "params": [{"name": "value", "type": "float", "unit": unit}],
        "preconditions": ["model_loaded", "wing_editor_open", "sect_tab"],
        "steps": [
            {"op": "set_field", "key": f"wing.sect.{parm_name.lower()}",
             "desc": f"the numeric value box on the far right of the {label} row",
             "value": "{value}"},
        ],
        "verify": {"read_label": "Projected Span"} if parm_name == "Span" else {},
        "parm": {"geom": "Wing", "group": "XSec_1", "name": parm_name},
        "confidence": 0.9,
        "recovery": "re-localize field then retry once",
        "verified": True,
    }


COMPOSITES = [
    {
        "name": "load_model",
        "summary": "Open a .vsp3 model via File > Open.",
        "kind": "composite",
        "params": [{"name": "dir", "type": "path"}, {"name": "file", "type": "str"}],
        "preconditions": ["app_focused"],
        "steps": [
            # Proven recipe: open dialog via menu, set the directory, then
            # DOUBLE-CLICK the file in the list (typing races/appends), then
            # Accept. All in-dialog clicks use no_guard so re-raising vsp can't
            # steal focus from the modal dialog.
            {"op": "menu", "path": ["File", "Open..."]},
            {"op": "wait", "seconds": 2.0},
            {"op": "fill_field", "key": "open.pathfield",
             "desc": "the folder path text field at the top of the Open dialog",
             "value": "{dir}"},
            {"op": "wait", "seconds": 0.5},
            {"op": "key", "name": "return"},
            {"op": "wait", "seconds": 1.5},
            {"op": "click", "key": "open.file_in_list", "double": True,
             "no_guard": True,
             "desc": "the file named {file} in the dialog file list"},
            {"op": "wait", "seconds": 0.8},
            {"op": "click", "key": "open.accept", "no_guard": True,
             "desc": "the Accept button at the bottom left of the dialog"},
            {"op": "wait", "seconds": 3.0},
        ],
        "verify": {"title_contains": "{file}"},
        "confidence": 0.85,
        "recovery": "re-localize dialog fields",
        "verified": True,
    },
    {
        "name": "open_wing_editor",
        "summary": "Open the Wing geometry editor and select its Sect tab.",
        "kind": "composite",
        "params": [],
        "preconditions": ["model_loaded"],
        "steps": [
            {"op": "click", "key": "geom.wing_node", "double": True,
             "desc": "the word Wing in the Geom Browser tree, indented under fuselage"},
            {"op": "wait", "seconds": 1.5},
            {"op": "click", "key": "wing.tab.sect",
             "desc": "the Sect tab in the Wing editor tab row (between Plan and Airfoil)"},
            {"op": "wait", "seconds": 1.0},
        ],
        "verify": {"read_label": "Projected Span"},
        "confidence": 0.85,
        "recovery": "re-localize tree node and tab",
        "verified": True,
    },
    {
        "name": "save_model",
        "summary": "Save the current model (Cmd+S).",
        "kind": "composite",
        "params": [],
        "preconditions": ["model_loaded"],
        "steps": [{"op": "cmd", "letter": "s"}, {"op": "wait", "seconds": 2.0}],
        "verify": {},
        "confidence": 0.95,
        "recovery": "",
        "verified": True,
    },
]


def seed():
    r = Registry()
    for name, parm, summary, unit in WING_FIELDS:
        r.save(field_skill(name, parm, summary, unit))
    for c in COMPOSITES:
        r.save(c)
    return r


if __name__ == "__main__":
    r = seed()
    print(f"seeded {len(r.skills)} skills\n")
    print(r.manifest())
