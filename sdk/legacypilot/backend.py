"""Fast execution backend for compiled workflows.

Once the agent has LEARNED (via GUI + Holo) which GUI field maps to which
underlying parameter, the skill's `parm` block records that mapping. This
backend replays that knowledge at scale — no screen, no vision — to run the
hundreds of experiments a trade study needs.

This is the "compiled" tier: the agent discovers the workflow through the GUI
once, and it is executed here for throughput. We read the coefficients from the
.polar files VSPAERO exports (the application's own output).

Handles the OpenVSP-specific gotchas learned this session:
  - thick bodies (fuselage/engine/pod) segfault VSPAERO -> analyze the wing as a
    THIN lifting surface via a dedicated geometry Set
  - the 'engine' subtree is cut so it doesn't drag the wing's mesh down
"""
import os
import multiprocessing as mp

HERE = os.path.dirname(__file__)
RESOURCES = os.path.abspath(os.path.join(HERE, "..", "vendor", "openvsp"))
MODEL = os.path.abspath(os.path.join(HERE, "..", "models", "boeing777200.vsp3"))
WORK = os.path.abspath(os.path.join(HERE, "..", "models", "lp_runs"))
os.makedirs(WORK, exist_ok=True)


def _is_float(s):
    try:
        float(s); return True
    except ValueError:
        return False


def read_polar(path):
    """Parse a VSPAERO .polar file: header row (Mach/CLtot/L/D) + one data row
    per alpha. Returns the LAST row (highest alpha of the point/sweep)."""
    if not os.path.exists(path):
        return {}
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
    want = {"AoA": "alpha", "Mach": "Mach", "CLtot": "CL", "CDtot": "CD",
            "L/D": "L_D", "CMytot": "CMy", "E": "e"}
    return {dst: rows[-1][cols[s]] for s, dst in want.items()
            if s in cols and cols[s] < len(rows[-1])}


def read_polar_all(path):
    """All rows (for alpha/mach sweeps)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [ln for ln in f if ln.strip()]
    header, rows = None, []
    for ln in lines:
        toks = ln.split()
        if "Mach" in toks and "CLtot" in toks and "L/D" in toks:
            header, rows = toks, []
        elif header and toks and _is_float(toks[0]):
            rows.append([float(t) for t in toks if _is_float(t)])
    if not header:
        return []
    cols = {n: i for i, n in enumerate(header)}
    want = {"AoA": "alpha", "Mach": "Mach", "CLtot": "CL", "CDtot": "CD",
            "L/D": "L_D", "CMytot": "CMy"}
    return [{dst: r[cols[s]] for s, dst in want.items() if s in cols and cols[s] < len(r)}
            for r in rows]


def _worker(job):
    """Run ONE design point in an isolated process (OpenVSP is not thread-safe).

    job = {
      idx, tag,
      params: {parm_key: value, ...}   # parm_key = "Geom:Group:Name"
      alpha: [start, end, npts], mach: [start, end, npts],
    }
    """
    import openvsp as vsp
    idx = job["idx"]
    tag = f'{job["tag"]}_{idx:04d}'
    base = os.path.join(WORK, tag)

    vsp.ClearVSPModel()
    vsp.ReadVSPFile(MODEL)

    # cut engine subtree (thick bodies break VSPAERO meshing)
    for g in list(vsp.FindGeoms()):
        if vsp.GetGeomName(g).lower() == "engine":
            vsp.DeleteGeom(g)
    vsp.Update()

    name_to_id = {vsp.GetGeomName(g): g for g in vsp.FindGeoms()}

    # apply each swept parameter
    applied = {}
    for pk, val in job["params"].items():
        geom, group, name = pk.split(":")
        gid = name_to_id.get(geom)
        if gid is None:
            continue
        vsp.SetParmVal(gid, name, group, float(val))
        applied[pk] = float(val)
    vsp.Update()

    # analyze the wing as a thin lifting surface
    wing = name_to_id.get("Wing")
    SET = 3
    for g in vsp.FindGeoms():
        vsp.SetSetFlag(g, SET, g == wing)
    vsp.WriteVSPFile(base + ".vsp3", vsp.SET_ALL)
    vsp.SetVSPAEROPath(RESOURCES)

    a0, a1, an = job.get("alpha", [4.0, 4.0, 1])
    m0, m1, mn = job.get("mach", [0.1, 0.1, 1])
    for a in ("VSPAEROComputeGeometry", "VSPAEROSweep"):
        vsp.SetAnalysisInputDefaults(a)
        vsp.SetIntAnalysisInput(a, "GeomSet", [-1])
        vsp.SetIntAnalysisInput(a, "ThinGeomSet", [SET])
        if a == "VSPAEROSweep":
            vsp.SetDoubleAnalysisInput(a, "AlphaStart", [a0])
            vsp.SetDoubleAnalysisInput(a, "AlphaEnd", [a1])
            vsp.SetIntAnalysisInput(a, "AlphaNpts", [int(an)])
            vsp.SetDoubleAnalysisInput(a, "MachStart", [m0])
            vsp.SetDoubleAnalysisInput(a, "MachEnd", [m1])
            vsp.SetIntAnalysisInput(a, "MachNpts", [int(mn)])
        vsp.ExecAnalysis(a)

    single = read_polar(base + ".polar")
    result = {"idx": idx, "params": applied, **single}
    if int(an) > 1 or int(mn) > 1:
        result["curve"] = read_polar_all(base + ".polar")
    return result


def run_jobs(jobs, workers=6):
    """Run a list of design-point jobs in parallel. Returns list of results."""
    with mp.Pool(processes=workers) as pool:
        return pool.map(_worker, jobs)
