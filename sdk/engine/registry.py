"""Registry — the vocabulary of what the GUI product can change and measure.

An agent answers arbitrary design questions by composing entries from two
registries, all driven through the GUI (no OpenVSP API):

  INPUTS   a variable the user can change -> where its field lives in the GUI
           (geom tree row, editor tab, field description) + a baked coordinate
           offset once grounded. e.g. wing span, wing chord, tail span.

  OUTPUTS  a quantity the user can ask about -> which analysis produces it and
           which result field holds it. e.g. mass, CG, wetted area, volume.
           Each carries a `speed` tag so the dispatcher knows if it fits <15s.

Coordinates are stored as window-relative offsets (survive the dialog moving).
Anything NOT in the registry can still be handled by grounding it on demand
with Holo (the two-tier fallback) — the registry just makes the common cases
instant.
"""

# window size signatures (see bot/windows.py)
GEOM_BROWSER = (275, 673)
GEOM_EDITOR = (460, 828)
MASSPROP = (300, 508)

# ---- geoms: tree row offset from the Geom Browser corner --------------------
GEOMS = {
    "wing": {"tree_off": (60, 170), "label": "Wing"},
    "htail": {"tree_off": (94, 254), "label": "horizontal stabilizer"},
    "vtail": {"tree_off": (94, 273), "label": "vertical stabilizer"},
    "fuselage": {"tree_off": (70, 152), "label": "fuselage"},
}

# ---- editor tabs: offset from the geom-editor corner ------------------------
TABS = {
    "Gen": (28, 65), "XForm": (66, 65), "Mass": (110, 65), "Sub": (150, 65),
    "Plan": (203, 65), "Sect": (247, 65), "Airfoil": (300, 65),
}

# ---- INPUT variables: what can be changed -----------------------------------
# geom      -> key into GEOMS
# tab       -> which editor tab the field is on
# field_off -> baked offset from editor corner (None => ground on demand)
# desc      -> Holo description used to ground it if field_off is None
# aliases   -> natural-language names that map to this input
INPUTS = {
    "wing_span": {
        "geom": "wing", "tab": "Plan", "field_off": (424, 106),
        "desc": "the Span numeric value field on the right of the Span row in Total Planform",
        "aliases": ("wingspan", "wing span", "span"), "unit": "m",
    },
    "wing_chord": {
        "geom": "wing", "tab": "Plan", "field_off": (318, 146),
        "desc": "the Chord numeric value field in the Total Planform panel",
        "aliases": ("wing chord", "chord", "root chord"), "unit": "m",
    },
    "wing_area": {
        "geom": "wing", "tab": "Plan", "field_off": (424, 167),
        "desc": "the Area numeric value field in the Total Planform panel",
        "aliases": ("wing area", "reference area", "wing surface"), "unit": "m^2",
    },
    "htail_span": {
        "geom": "htail", "tab": "Plan", "field_off": (424, 106),
        "desc": "the Span numeric value field in the Total Planform panel",
        "aliases": ("tail span", "tail size", "horizontal tail span", "htail span"), "unit": "m",
    },
    "htail_area": {
        "geom": "htail", "tab": "Plan", "field_off": (424, 167),
        "desc": "the Area numeric value field in the Total Planform panel",
        "aliases": ("tail area", "horizontal tail area"), "unit": "m^2",
    },
    "vtail_span": {
        "geom": "vtail", "tab": "Plan", "field_off": (424, 106),
        "desc": "the Span numeric value field in the Total Planform panel",
        "aliases": ("vertical tail span", "fin span", "vtail span"), "unit": "m",
    },
}

# ---- OUTPUT quantities: what can be measured --------------------------------
# analysis  -> which analysis dialog to run
# fields    -> result keys to read
# speed     -> "instant" (<15s, in-GUI) | "slow" (external solver, minutes)
OUTPUTS = {
    "mass": {"analysis": "massprop", "fields": ("Total_Mass",), "speed": "instant",
             "aliases": ("mass", "weight", "total mass")},
    "cg": {"analysis": "massprop", "fields": ("X_Cg", "Y_Cg", "Z_Cg"), "speed": "instant",
           "aliases": ("cg", "center of gravity", "centre of gravity", "balance")},
    "inertia": {"analysis": "massprop", "fields": ("Ixx", "Iyy", "Izz"), "speed": "instant",
                "aliases": ("inertia", "moment of inertia", "roll inertia")},
    "wetted_area": {"analysis": "compgeom", "fields": ("Total_Wet_Area",), "speed": "instant",
                    "aliases": ("wetted area", "surface area", "wet area")},
    "volume": {"analysis": "compgeom", "fields": ("Total_Wet_Vol",), "speed": "instant",
               "aliases": ("volume", "wetted volume", "internal volume")},
    "ld": {"analysis": "vspaero", "fields": ("L_D", "CL", "CD"), "speed": "slow",
           "aliases": ("l/d", "lift to drag", "drag", "lift", "aerodynamic efficiency")},
}


def resolve_input(name):
    """Map a natural-language variable name to an INPUTS key (or None)."""
    n = name.lower().strip()
    for key, spec in INPUTS.items():
        if n == key or n in spec["aliases"] or any(a in n for a in spec["aliases"]):
            return key
    return None


def resolve_output(name):
    """Map a natural-language quantity name to an OUTPUTS key (or None)."""
    n = name.lower().strip()
    for key, spec in OUTPUTS.items():
        if n == key or n in spec["aliases"] or any(a in n for a in spec["aliases"]):
            return key
    return None
