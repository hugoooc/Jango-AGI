"""De-risk the scale-out tier: run N wing-span experiments in parallel.

Each worker runs in its own process (OpenVSP global state is not thread-safe),
builds a wing with different parameters, solves with VSPAERO, and returns the
polar coefficients. Demonstrates Phase 3 (parallel experiments) + Phase 4
(aggregate to insight).
"""
import os
import time
import subprocess
from concurrent.futures import ProcessPoolExecutor

import openvsp as vsp

HERE = os.path.dirname(__file__)
RESOURCES = os.path.join(HERE, "..", "vendor", "openvsp")
VSPAERO = os.path.join(RESOURCES, "vspaero")


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
    want = {"AoA": "alpha", "CLtot": "CL", "CDtot": "CD", "L/D": "L_D", "CMytot": "CMy"}
    return {dst: row[cols[s]] for s, dst in want.items() if s in cols and cols[s] < len(row)}


def experiment(job):
    """One isolated design point. job = (idx, span, sweep, alpha)."""
    idx, span, sweep_deg, alpha = job
    workdir = os.path.join(HERE, "runs", f"exp_{idx:03d}")
    os.makedirs(workdir, exist_ok=True)
    base = os.path.join(workdir, "wing")

    vsp.ClearVSPModel()
    wid = vsp.AddGeom("WING")
    vsp.SetParmVal(wid, "Span", "XSec_1", span)
    vsp.SetParmVal(wid, "Root_Chord", "XSec_1", 1.5)
    vsp.SetParmVal(wid, "Tip_Chord", "XSec_1", 1.0)
    vsp.SetParmVal(wid, "Sweep", "XSec_1", sweep_deg)
    vsp.Update()
    vsp.WriteVSPFile(base + ".vsp3", vsp.SET_ALL)

    # DegenGeom via API (writes the .vspgeom the solver needs)
    vsp.SetVSPAEROPath(os.path.abspath(RESOURCES))
    vsp.SetAnalysisInputDefaults("VSPAEROComputeGeometry")
    vsp.ExecAnalysis("VSPAEROComputeGeometry")

    # Write a minimal .vspaero setup then invoke the solver directly (robust).
    vsp.SetAnalysisInputDefaults("VSPAEROSweep")
    vsp.SetDoubleAnalysisInput("VSPAEROSweep", "AlphaStart", [alpha])
    vsp.SetIntAnalysisInput("VSPAEROSweep", "AlphaNpts", [1])
    vsp.SetDoubleAnalysisInput("VSPAEROSweep", "MachStart", [0.1])
    vsp.SetIntAnalysisInput("VSPAEROSweep", "MachNpts", [1])
    vsp.ExecAnalysis("VSPAEROSweep")

    res = read_polar(base + ".polar")
    res.update({"idx": idx, "span": span, "sweep": sweep_deg, "alpha": alpha})
    return res


def make_jobs():
    jobs, idx = [], 0
    for span in (4, 6, 8, 10, 12):
        for sweep in (0, 15, 30, 45):
            jobs.append((idx, float(span), float(sweep), 4.0))
            idx += 1
    return jobs


if __name__ == "__main__":
    jobs = make_jobs()
    t0 = time.time()
    print(f"[sweep] launching {len(jobs)} experiments in parallel...")
    with ProcessPoolExecutor(max_workers=6) as ex:
        results = [r for r in ex.map(experiment, jobs) if r and "L_D" in r]
    dt = time.time() - t0

    results.sort(key=lambda r: r["L_D"], reverse=True)
    print(f"\n[done] {len(results)}/{len(jobs)} solved in {dt:.1f}s "
          f"({dt/len(jobs):.2f}s/exp wall, ~{len(jobs)} on 6 workers)\n")
    print(f"{'span':>5} {'sweep':>6} {'CL':>8} {'CD':>9} {'L/D':>8} {'CMy':>9}")
    for r in results:
        print(f"{r['span']:5.0f} {r['sweep']:6.0f} {r['CL']:8.4f} "
              f"{r['CD']:9.5f} {r['L_D']:8.2f} {r['CMy']:9.4f}")

    best = results[0]
    worst = min(results, key=lambda r: r["CMy"])  # most nose-down / pitchy
    print(f"\n[insight] Best L/D: span={best['span']:.0f} sweep={best['sweep']:.0f} "
          f"-> L/D={best['L_D']:.1f}")
    print(f"[insight] Most pitch-heavy (|CMy| max): span={worst['span']:.0f} "
          f"sweep={worst['sweep']:.0f} -> CMy={worst['CMy']:.3f}")
