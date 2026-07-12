"""Analysis catalog + generic runner — the agent's simulation interface.

OpenVSP's Analysis Manager is self-describing: every analysis exposes its input
names, types, defaults, and doc strings, and every result is a named, typed
value. So instead of hard-coding one analysis, we expose the WHOLE menu to an
agent:

  catalog()            -> list every analysis with a one-line doc
  schema(name)         -> full input schema (name, type, default, doc) for one
  run(name, model,     -> configure inputs + execute + return structured results
      inputs=..., set=)

An agent can therefore: read catalog(), pick an analysis, read schema() to see
what it can set, then call run() with just the inputs it cares about (the rest
fall back to OpenVSP defaults). This is the "choose and run any simulation"
layer — MassProp, CompGeom, VSPAERO sweep, WaveDrag, etc. all go through it.

Headless and in-process: no GUI, no external clicking, deterministic. The GUI
record/replay bot (bot/) is the *discovery* half; this is the reliable
*execution* half the demo actually reports numbers from.
"""
import os
import openvsp as vsp

# openvsp data-type enum -> friendly name
_T = {
    vsp.INT_DATA: "int",
    vsp.DOUBLE_DATA: "double",
    vsp.STRING_DATA: "string",
    vsp.VEC3D_DATA: "vec3d",
}
MODELS = os.path.join(os.path.dirname(__file__), "..", "models")


# ---------------------------------------------------------------- catalog

def catalog():
    """Every available analysis: [{name, doc, num_inputs}], sorted."""
    out = []
    for name in sorted(vsp.ListAnalysis()):
        out.append({
            "name": name,
            "doc": vsp.GetAnalysisDoc(name).strip(),
            "num_inputs": len(vsp.GetAnalysisInputNames(name)),
        })
    return out


def _read_default(name, inp, t):
    try:
        if t == vsp.INT_DATA:
            v = list(vsp.GetIntAnalysisInput(name, inp))
        elif t == vsp.DOUBLE_DATA:
            v = list(vsp.GetDoubleAnalysisInput(name, inp))
        elif t == vsp.STRING_DATA:
            v = list(vsp.GetStringAnalysisInput(name, inp))
        else:
            return None
        return v[0] if len(v) == 1 else v
    except Exception:
        return None


def schema(name):
    """Full input schema for one analysis: every input's name, type, default
    value, and doc. This is what an agent reads before choosing values.
    Requires SetAnalysisInputDefaults to have populated defaults, so we do it."""
    if name not in vsp.ListAnalysis():
        raise ValueError(f"unknown analysis {name!r}; see catalog()")
    vsp.SetAnalysisInputDefaults(name)
    inputs = []
    for inp in vsp.GetAnalysisInputNames(name):
        t = vsp.GetAnalysisInputType(name, inp)
        inputs.append({
            "name": inp,
            "type": _T.get(t, str(t)),
            "default": _read_default(name, inp, t),
            "doc": vsp.GetAnalysisInputDoc(name, inp).strip(),
        })
    return {"analysis": name, "doc": vsp.GetAnalysisDoc(name).strip(),
            "inputs": inputs}


# ---------------------------------------------------------------- runner

def _set_input(name, inp, value):
    """Set one analysis input, dispatching on its declared type. Accepts a
    scalar or a list (VSP inputs are vectors)."""
    t = vsp.GetAnalysisInputType(name, inp)
    vals = value if isinstance(value, (list, tuple)) else [value]
    if t == vsp.INT_DATA:
        vsp.SetIntAnalysisInput(name, inp, [int(v) for v in vals])
    elif t == vsp.DOUBLE_DATA:
        vsp.SetDoubleAnalysisInput(name, inp, [float(v) for v in vals])
    elif t == vsp.STRING_DATA:
        vsp.SetStringAnalysisInput(name, inp, [str(v) for v in vals])
    else:
        raise TypeError(f"cannot set input {inp!r} of type {_T.get(t, t)}")


def _read_result(rid, key):
    t = vsp.GetResultsType(rid, key)
    try:
        if t == vsp.DOUBLE_DATA:
            v = list(vsp.GetDoubleResults(rid, key))
        elif t == vsp.INT_DATA:
            v = list(vsp.GetIntResults(rid, key))
        elif t == vsp.STRING_DATA:
            v = list(vsp.GetStringResults(rid, key))
        elif t == vsp.VEC3D_DATA:
            pts = vsp.GetVec3dResults(rid, key)
            v = [[p.x(), p.y(), p.z()] for p in pts]
        else:
            return None
    except Exception:
        return None
    return v[0] if isinstance(v, list) and len(v) == 1 else v


def run(name, model=None, inputs=None, set_geom=None):
    """Configure and execute an analysis; return {analysis, inputs, results}.

    name    : analysis name from catalog()
    model   : path (or models/-relative) to load first; None keeps current model
    inputs  : {input_name: value|[values]} overrides (rest use VSP defaults)
    set_geom: convenience — sets the 'Set' input (geometry set index)
    """
    if name not in vsp.ListAnalysis():
        raise ValueError(f"unknown analysis {name!r}; see catalog()")
    if model:
        if not os.path.isabs(model):
            model = os.path.join(MODELS, model)
        vsp.ClearVSPModel()
        vsp.ReadVSPFile(os.path.abspath(model))

    vsp.SetAnalysisInputDefaults(name)
    applied = {}
    if set_geom is not None:
        _set_input(name, "Set", int(set_geom)); applied["Set"] = int(set_geom)
    for inp, val in (inputs or {}).items():
        _set_input(name, inp, val); applied[inp] = val
    vsp.Update()

    rid = vsp.ExecAnalysis(name)
    results = {k: _read_result(rid, k) for k in vsp.GetAllDataNames(rid)}
    return {"analysis": name, "inputs": applied, "results": results}
