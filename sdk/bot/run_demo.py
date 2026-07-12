"""Full demo orchestrator — cold start to quit, repeatable on demand.

The lifecycle the user asked for:

  1. launch OpenVSP with the model (cold: app opens + model loads in one step)
  2. replay the recorded GUI trajectory that drives VSPAERO
       open dialog -> set flow condition -> Prepare -> Launch -> wait for Done
  3. read the results the solver wrote (.polar / .history next to the model)
  4. quit OpenVSP, leaving nothing running so the next demand starts cold

Discovery (Holo grounding) already happened once at record time; this run is
pure replay — deterministic coordinates, no grounding vision. The only screen
read is wait_console polling for solver completion.

Usage:
  python bot/run_demo.py                       # defaults
  python bot/run_demo.py model=boeing777200.vsp3 alpha=4 alpha_end=10 mach=0.1
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
import lifecycle as L          # noqa: E402
from replay import replay      # noqa: E402

MODELS = os.path.join(os.path.dirname(__file__), "..", "models")


def read_results(model_stem):
    """Parse the .polar the GUI solver wrote next to the model. Reuses the
    deterministic table parser proven in derisk (header w/ Mach/CLtot/L/D)."""
    polar = os.path.join(MODELS, f"{model_stem}.polar")
    if not os.path.exists(polar):
        return None
    with open(polar) as f:
        lines = [ln for ln in f if ln.strip()]
    header, rows = None, []
    for ln in lines:
        toks = ln.split()
        if "Mach" in toks and "CLtot" in toks and "L/D" in toks:
            header, rows = toks, []
        elif header and toks:
            try:
                rows.append([float(t) for t in toks])
            except ValueError:
                pass
    if not header or not rows:
        return None
    cols = {n: i for i, n in enumerate(header)}
    want = {"Mach": "Mach", "AoA": "Alpha", "CLtot": "CL",
            "CDtot": "CD", "L/D": "L_D", "CMytot": "CMy"}
    out = []
    for row in rows:
        rec = {}
        for src, dst in want.items():
            i = cols.get(src)
            if i is not None and i < len(row):
                rec[dst] = row[i]
        if rec:
            out.append(rec)
    return out


def run(model="boeing777200.vsp3", alpha="4", alpha_end="10", mach="0.1",
        keep_open=False):
    stem = os.path.splitext(os.path.basename(model))[0]
    print(f"=== VSPAERO demo: {model} | alpha {alpha}->{alpha_end} mach {mach} ===")
    try:
        print("[1/4] launching OpenVSP (cold)...")
        t0 = time.time()
        L.launch(model)
        print(f"      up in {time.time()-t0:.0f}s, model loaded")

        print("[2/4] replaying GUI solve trajectory (zero grounding vision)...")
        replay("vspaero_solve", reset=True,
               alpha=alpha, alpha_end=alpha_end, mach=mach)

        print("[3/4] reading results...")
        res = read_results(stem)
        if res:
            print(f"      {'Alpha':>6} {'Mach':>6} {'CL':>9} {'CD':>9} {'L/D':>8} {'CMy':>9}")
            for r in res:
                print(f"      {r.get('Alpha',0):6.2f} {r.get('Mach',0):6.3f} "
                      f"{r.get('CL',0):9.4f} {r.get('CD',0):9.5f} "
                      f"{r.get('L_D',0):8.2f} {r.get('CMy',0):9.4f}")
        else:
            print("      no .polar found — solve may not have completed")
        return res
    finally:
        if not keep_open:
            print("[4/4] quitting OpenVSP...")
            ok = L.quit()
            print(f"      closed: {ok}")


if __name__ == "__main__":
    kw = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    if "keep_open" in kw:
        kw["keep_open"] = kw["keep_open"].lower() in ("1", "true", "yes")
    run(**kw)
