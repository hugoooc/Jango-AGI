"""Analysis engine — jobs the agent can run once the software is APIized.

Job types:
  - single(params)                    one design point
  - sweep(variable, values)           1-D parameter sweep
  - grid(variables, values_lists)     N-D full-factorial trade study
  - polar(alpha_range)                lift/drag polar at fixed geometry
  - optimize(variable, values, metric) pick the best design over a sweep

Every job resolves human variable names to backend parm keys via the skill
registry's `parameters()`, so you say "Span" not "Wing:XSec_1:Span".
"""
import itertools
from . import backend


def _resolve(registry, variable):
    """Map a human variable name (skill name or parm name) to a backend key
    'Geom:Group:Name'."""
    for p in registry.parameters():
        parm = p["parm"]
        if variable in (p["skill"], parm.get("name")):
            return f'{parm["geom"]}:{parm["group"]}:{parm["name"]}'
    # allow direct key
    if variable.count(":") == 2:
        return variable
    raise KeyError(f"unknown variable '{variable}'. Known: "
                   f"{[p['skill'] for p in registry.parameters()]}")


def single(registry, params, alpha=4.0, mach=0.1, workers=1):
    keyed = {_resolve(registry, k): v for k, v in params.items()}
    jobs = [{"idx": 0, "tag": "single", "params": keyed,
             "alpha": [alpha, alpha, 1], "mach": [mach, mach, 1]}]
    return backend.run_jobs(jobs, workers=1)[0]


def sweep(registry, variable, values, base=None, alpha=4.0, mach=0.1, workers=6):
    key = _resolve(registry, variable)
    base = {_resolve(registry, k): v for k, v in (base or {}).items()}
    jobs = []
    for i, v in enumerate(values):
        p = dict(base); p[key] = v
        jobs.append({"idx": i, "tag": f"sweep_{variable}", "params": p,
                     "alpha": [alpha, alpha, 1], "mach": [mach, mach, 1]})
    results = backend.run_jobs(jobs, workers=workers)
    for r, v in zip(sorted(results, key=lambda r: r["idx"]), values):
        r["variable"] = variable
        r["value"] = v
    return sorted(results, key=lambda r: r["idx"])


def grid(registry, variables, values_lists, alpha=4.0, mach=0.1, workers=6):
    """Full-factorial trade study over N variables."""
    keys = [_resolve(registry, v) for v in variables]
    combos = list(itertools.product(*values_lists))
    jobs = []
    for i, combo in enumerate(combos):
        p = {k: val for k, val in zip(keys, combo)}
        jobs.append({"idx": i, "tag": "grid", "params": p,
                     "alpha": [alpha, alpha, 1], "mach": [mach, mach, 1]})
    results = backend.run_jobs(jobs, workers=workers)
    for r, combo in zip(sorted(results, key=lambda r: r["idx"]), combos):
        r["combo"] = dict(zip(variables, combo))
    return sorted(results, key=lambda r: r["idx"])


def polar(registry, params=None, alpha_start=0.0, alpha_end=10.0, npts=6,
          mach=0.1):
    """Alpha polar at a fixed geometry."""
    keyed = {_resolve(registry, k): v for k, v in (params or {}).items()}
    job = {"idx": 0, "tag": "polar", "params": keyed,
           "alpha": [alpha_start, alpha_end, npts], "mach": [mach, mach, 1]}
    res = backend.run_jobs([job], workers=1)[0]
    return res.get("curve", [])


def optimize(registry, variable, values, metric="L_D", maximize=True,
             base=None, alpha=4.0, mach=0.1, workers=6):
    """Sweep a variable and return the best design by `metric`."""
    results = sweep(registry, variable, values, base=base, alpha=alpha,
                    mach=mach, workers=workers)
    valid = [r for r in results if metric in r]
    if not valid:
        return {"error": "no valid results", "results": results}
    best = (max if maximize else min)(valid, key=lambda r: r[metric])
    return {"best": best, "metric": metric, "results": results}


def pareto(results, x="CD", y="L_D", maximize_y=True):
    """Pareto frontier: minimize x, maximize/minimize y."""
    pts = [r for r in results if x in r and y in r]
    pts = sorted(pts, key=lambda r: r[x])
    front, best_y = [], (-1e18 if maximize_y else 1e18)
    for r in pts:
        if (maximize_y and r[y] > best_y) or (not maximize_y and r[y] < best_y):
            front.append(r); best_y = r[y]
    return front
