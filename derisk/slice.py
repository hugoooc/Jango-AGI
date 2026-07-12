"""De-risk vertical slice: build a wing, set VSPAERO, run analysis, read CL/CD/CM.

This proves the scale-out tier end to end via the OpenVSP Python API (headless).
Run inside the 3.11 venv with the bundled openvsp packages installed.
"""
import os
import time
import openvsp as vsp

RESOURCES = os.environ.get(
    "VSP_RESOURCES",
    os.path.join(os.path.dirname(__file__), "..", "vendor", "openvsp"),
)
WORKDIR = os.path.join(os.path.dirname(__file__), "run")
os.makedirs(WORKDIR, exist_ok=True)


def build_wing(span=8.0, root_chord=1.5, tip_chord=1.0, sweep=15.0):
    """Create a single tapered wing and return the model file path."""
    vsp.ClearVSPModel()
    wing_id = vsp.AddGeom("WING")

    # Section geometry parms live on the XSecSurf; set span/chords/sweep.
    vsp.SetParmVal(wing_id, "Span", "XSec_1", span)
    vsp.SetParmVal(wing_id, "Root_Chord", "XSec_1", root_chord)
    vsp.SetParmVal(wing_id, "Tip_Chord", "XSec_1", tip_chord)
    vsp.SetParmVal(wing_id, "Sweep", "XSec_1", sweep)
    vsp.Update()

    fname = os.path.join(WORKDIR, "wing.vsp3")
    vsp.WriteVSPFile(fname, vsp.SET_ALL)
    return wing_id, fname


def run_vspaero(alpha=4.0, mach=0.1):
    """Compute degen geom, run VSPAERO sweep analysis, return result CL/CD/CM."""
    vsp.SetVSPAEROPath(RESOURCES)

    # DegenGeom is the input VSPAERO consumes.
    analysis = "VSPAEROComputeGeometry"
    vsp.SetAnalysisInputDefaults(analysis)
    vsp.ExecAnalysis(analysis)

    analysis = "VSPAEROSweep"
    vsp.SetAnalysisInputDefaults(analysis)
    vsp.SetDoubleAnalysisInput(analysis, "AlphaStart", [alpha])
    vsp.SetIntAnalysisInput(analysis, "AlphaNpts", [1])
    vsp.SetDoubleAnalysisInput(analysis, "MachStart", [mach])
    vsp.SetIntAnalysisInput(analysis, "MachNpts", [1])
    vsp.Update()

    rid = vsp.ExecAnalysis(analysis)
    return rid


def read_polar(polar_path):
    """Parse a VSPAERO .polar file (header row + one data row per alpha)."""
    with open(polar_path) as f:
        lines = [ln for ln in f if ln.strip()]
    # Last two non-empty lines that look tabular: header then data.
    # The real column header is the line whose tokens include 'Mach' and 'CLtot'.
    header = None
    data_rows = []
    for ln in lines:
        toks = ln.split()
        if "Mach" in toks and "CLtot" in toks and "L/D" in toks:
            header = toks
            data_rows = []
        elif header and toks and _is_float(toks[0]):
            data_rows.append([float(t) for t in toks if _is_float(t)])
    if not header or not data_rows:
        return {}
    cols = {name: i for i, name in enumerate(header)}
    row = data_rows[-1]  # highest alpha point
    want = {"Mach": "Mach", "AoA": "Alpha", "CLtot": "CL",
            "CDtot": "CD", "L/D": "L_D", "CMytot": "CMy", "E": "E"}
    out = {}
    for src, dst in want.items():
        idx = cols.get(src)
        if idx is not None and idx < len(row):
            out[dst] = row[idx]
    return out


def _is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    t0 = time.time()
    wing_id, fname = build_wing()
    print(f"[build] wing model -> {fname}")
    rid = run_vspaero()
    print(f"[vspaero] analysis id = {rid}")
    print("[results]")
    res = read_polar(os.path.join(WORKDIR, "wing.polar"))
    for k, v in res.items():
        print(f"   {k:8s} = {v:.5f}")
    print(f"[done] {time.time()-t0:.1f}s")
