"""Run VSPAERO on the CU-modified Boeing model and read results.

This closes the loop: the model was opened, modified (Span 5.48 -> 9.0), and saved
entirely by the Holo computer-use agent driving the GUI. Here we run the aero
analysis on that saved file and extract the performance metrics.
"""
import os
import sys
import openvsp as vsp

HERE = os.path.dirname(__file__)
RESOURCES = os.path.abspath(os.path.join(HERE, "..", "vendor", "openvsp"))
MODEL = os.path.join(HERE, "..", "models", "boeing777200.vsp3")
WORKDIR = os.path.join(HERE, "..", "models", "aero_run")
os.makedirs(WORKDIR, exist_ok=True)


def _is_float(s):
    try:
        float(s); return True
    except ValueError:
        return False


def read_polar(path):
    with open(path) as f:
        lines = [ln for ln in f if ln.strip()]
    header, rows = None, []
    for ln in lines:
        toks = ln.split()
        if "Mach" in toks and "CLtot" in toks and "L/D" in toks:
            header, rows = toks, []
        elif header and toks and _is_float(toks[0]):
            rows.append([float(t) for t in toks if _is_float(t)])
    if not header or not rows:
        return {}
    cols = {n: i for i, n in enumerate(header)}
    row = rows[-1]
    want = {"AoA": "alpha", "Mach": "Mach", "CLtot": "CL",
            "CDtot": "CD", "L/D": "L_D", "CMytot": "CMy"}
    return {dst: row[cols[s]] for s, dst in want.items()
            if s in cols and cols[s] < len(row)}


def main(alpha=4.0, mach=0.1):
    vsp.ClearVSPModel()
    vsp.ReadVSPFile(MODEL)

    wing = [g for g in vsp.FindGeoms() if vsp.GetGeomName(g).lower() == "wing"][0]
    print(f"[model] loaded; Wing Span(XSec_1) = {vsp.GetParmVal(wing, 'Span', 'XSec_1')}")

    # Save a copy into the run dir so VSPAERO writes there.
    base = os.path.join(WORKDIR, "boeing")
    vsp.WriteVSPFile(base + ".vsp3", vsp.SET_ALL)

    vsp.SetVSPAEROPath(RESOURCES)
    print("[aero] computing degenerate geometry...")
    vsp.SetAnalysisInputDefaults("VSPAEROComputeGeometry")
    vsp.ExecAnalysis("VSPAEROComputeGeometry")

    print(f"[aero] running VSPAERO sweep at alpha={alpha}, mach={mach} ...")
    vsp.SetAnalysisInputDefaults("VSPAEROSweep")
    vsp.SetDoubleAnalysisInput("VSPAEROSweep", "AlphaStart", [alpha])
    vsp.SetIntAnalysisInput("VSPAEROSweep", "AlphaNpts", [1])
    vsp.SetDoubleAnalysisInput("VSPAEROSweep", "MachStart", [mach])
    vsp.SetIntAnalysisInput("VSPAEROSweep", "MachNpts", [1])
    vsp.ExecAnalysis("VSPAEROSweep")

    res = read_polar(base + ".polar")
    print("\n[RESULTS] Boeing 777-200 (CU-modified wing, Span=9.0):")
    for k, v in res.items():
        print(f"   {k:6s} = {v:.5f}")
    return res


if __name__ == "__main__":
    a = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    main(alpha=a)
