"""LegacyPilot agent — the front door.

Two capabilities:
  1. GUI mode  — drive the live app via learned skills (needs OpenVSP frontmost).
  2. Analysis mode — run sweeps/trade-studies/optimize/polar on the fast backend
                     (no screen needed).

CLI:
  python -m legacypilot.agent api                       # print the learned API
  python -m legacypilot.agent sweep Span 5 7 9 11 13     # 1-D sweep
  python -m legacypilot.agent optimize Sweep 0 15 30 45  # best design
  python -m legacypilot.agent grid Span:6,9,12 Sweep:10,30,45   # trade study
  python -m legacypilot.agent polar 0 10 6               # alpha polar
  python -m legacypilot.agent set Span 9.0               # GUI: set a parameter live
  python -m legacypilot.agent discover-menus             # GUI: dump menu API
"""
import sys
import json

from .skills import Registry
from . import analysis


def api():
    print(Registry().manifest())


def _fmt_rows(rows, cols):
    hdr = " ".join(f"{c:>9}" for c in cols)
    print(hdr)
    for r in rows:
        print(" ".join(f"{r.get(c, float('nan')):9.4f}"
                        if isinstance(r.get(c), (int, float)) else f"{str(r.get(c)):>9}"
                        for c in cols))


def cmd_sweep(var, values):
    reg = Registry()
    res = analysis.sweep(reg, var, [float(v) for v in values])
    print(f"# sweep {var}")
    _fmt_rows([{"value": r.get("value"), **{k: r[k] for k in ("CL", "CD", "L_D", "CMy") if k in r}}
               for r in res], ["value", "CL", "CD", "L_D", "CMy"])
    return res


def cmd_optimize(var, values, metric="L_D"):
    reg = Registry()
    out = analysis.optimize(reg, var, [float(v) for v in values], metric=metric)
    b = out["best"]
    print(f"# optimize {var} for {metric}")
    print(f"BEST: {var}={b['value']}  {metric}={b[metric]:.3f}")
    return out


def cmd_grid(specs):
    """specs like ['Span:6,9,12', 'Sweep:10,30,45']"""
    reg = Registry()
    variables, values = [], []
    for s in specs:
        name, vals = s.split(":")
        variables.append(name)
        values.append([float(v) for v in vals.split(",")])
    res = analysis.grid(reg, variables, values)
    print(f"# grid {variables}")
    for r in sorted(res, key=lambda r: -r.get("L_D", 0)):
        if "L_D" in r:
            print(f"  {r['combo']}  L/D={r['L_D']:.2f}  CMy={r['CMy']:.4f}")
    front = analysis.pareto(res, x="CMy", y="L_D")
    print("# pareto (max L/D vs min |CMy|)")
    for r in front:
        print(f"  {r['combo']}  L/D={r['L_D']:.2f}  CMy={r['CMy']:.4f}")
    return res


def cmd_polar(a0, a1, npts):
    reg = Registry()
    curve = analysis.polar(reg, alpha_start=float(a0), alpha_end=float(a1), npts=int(npts))
    print("# polar")
    _fmt_rows(curve, ["alpha", "CL", "CD", "L_D"])
    return curve


# ---- GUI mode (live app) ----------------------------------------------

def cmd_set(var, value):
    """Set a parameter LIVE in the GUI via learned skills."""
    from .adapter import Adapter
    from .runtime import Runtime
    reg = Registry()
    a = Adapter()
    rt = Runtime(a, reg)
    a.guard()  # aborts if OpenVSP not frontmost
    # ensure editor open, then set the field
    rt.run("open_wing_editor")
    skill = next((s for s in reg.list()
                  if s.get("parm", {}).get("name", "").lower() == var.lower()
                  or s["name"].lower().endswith(var.lower())), None)
    if not skill:
        print(f"no skill for variable {var}")
        return
    r = rt.run(skill["name"], value=value)
    print(json.dumps(r, indent=2))


def cmd_discover_menus():
    from .adapter import Adapter
    from . import discover
    tree = discover.map_menus(Adapter())
    print(json.dumps(tree, indent=2))


def main(argv):
    if not argv:
        api(); return
    cmd, rest = argv[0], argv[1:]
    if cmd == "api":
        api()
    elif cmd == "sweep":
        cmd_sweep(rest[0], rest[1:])
    elif cmd == "optimize":
        cmd_optimize(rest[0], rest[1:])
    elif cmd == "grid":
        cmd_grid(rest)
    elif cmd == "polar":
        cmd_polar(*rest)
    elif cmd == "set":
        cmd_set(rest[0], float(rest[1]))
    elif cmd == "discover-menus":
        cmd_discover_menus()
    else:
        print(f"unknown command: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
