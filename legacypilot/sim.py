"""sim — the agent's simulation front door (headless, any analysis).

An agent flow:
  1. `list`            see every analysis it can run
  2. `schema <name>`   see that analysis's inputs (name/type/default/doc)
  3. `run <name> ...`  configure inputs and execute; get structured results

Everything runs in-process through OpenVSP's Analysis Manager — deterministic,
no GUI, no external clicking. This is the reliable execution path.

CLI:
  python -m legacypilot.sim list
  python -m legacypilot.sim schema MassProp
  python -m legacypilot.sim run MassProp --model boeing777200.vsp3 --in NumMassSlices=40
  python -m legacypilot.sim run CompGeom --model boeing777200.vsp3
  python -m legacypilot.sim run VSPAEROSweep --model wing.vsp3 --in AlphaStart=0 --in AlphaNpts=5

Values: --in Name=Value  (repeatable). Comma-separated => vector, e.g.
  --in AlphaStart=0 --in AlphaEnd=10        or   --in Machs=0.1,0.3,0.6
"""
import sys
import json
from . import analyses


def _parse_inputs(args):
    """Turn ['NumMassSlices=40','Machs=0.1,0.3'] into {name: value|list}."""
    out = {}
    for a in args:
        if "=" not in a:
            continue
        k, v = a.split("=", 1)
        parts = v.split(",")
        conv = []
        for p in parts:
            try:
                conv.append(int(p))
            except ValueError:
                try:
                    conv.append(float(p))
                except ValueError:
                    conv.append(p)
        out[k] = conv if len(conv) > 1 else conv[0]
    return out


def cmd_list():
    for a in analyses.catalog():
        print(f"{a['name']:26} ({a['num_inputs']:2} inputs)  {a['doc'][:60]}")


def cmd_schema(name):
    s = analyses.schema(name)
    print(f"# {s['analysis']} — {s['doc']}")
    print(f"{'input':22} {'type':7} {'default':16} doc")
    for i in s["inputs"]:
        print(f"{i['name']:22} {i['type']:7} {str(i['default']):16} {i['doc'][:55]}")


# result keys worth surfacing per analysis (rest available in --json)
_HIGHLIGHT = {
    "MassProp": ["Total_Mass", "Total_CG", "Total_Ixx", "Total_Iyy", "Total_Izz",
                 "Num_Comps", "Num_Total_Tris", "Analysis_Duration_Sec"],
    "CompGeom": ["Total_Wet_Area", "Total_Wet_Vol", "Num_Comps"],
    "WaveDrag": ["CDWave", "Mach"],
}


def cmd_run(name, model, inputs, as_json=False):
    out = analyses.run(name, model=model, inputs=inputs)
    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return out
    print(f"# {name}  model={model}  inputs={out['inputs'] or '(defaults)'}")
    res = out["results"]
    keys = _HIGHLIGHT.get(name) or list(res.keys())
    for k in keys:
        if k not in res:
            continue
        v = res[k]
        if isinstance(v, list) and len(v) > 6:
            v = f"{v[:6]} ... ({len(v)} vals)"
        print(f"  {k:24} = {v}")
    if name not in _HIGHLIGHT:
        print(f"  ({len(res)} result fields; use --json for all)")
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        cmd_list()
    elif cmd == "schema":
        cmd_schema(rest[0])
    elif cmd == "run":
        name = rest[0]
        model = None
        ins = []
        as_json = False
        i = 1
        while i < len(rest):
            tok = rest[i]
            if tok == "--model":
                model = rest[i + 1]; i += 2
            elif tok == "--in":
                ins.append(rest[i + 1]); i += 2
            elif tok == "--json":
                as_json = True; i += 1
            else:
                i += 1
        cmd_run(name, model, _parse_inputs(ins), as_json=as_json)
    else:
        print(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
