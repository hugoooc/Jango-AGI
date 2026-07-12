"""Governed trade study — the same GUI flow as bot/trade_study.study(), but
every risky action passes through the Governor (policy + approval + audit).

This is the concrete wiring of the NemoClaw-pattern layer into LegacyPilot. It
reuses the exact building blocks (lifecycle, geom driver, replay) so behaviour
is identical to the ungoverned study; the only difference is that:

  - launching / quitting OpenVSP        -> gov.check("navigate", ...)
  - opening a geom / navigating tabs    -> gov.check("navigate", ...)
  - SETTING a geometry parameter        -> gov.check("mutate", ...)   [may need approval]
  - running an analysis                 -> gov.check("navigate", ...)
  - every Holo vision call (egress)     -> gov.check_egress(url)      [allowlist]

Under 'standard' posture, mutate is allowed and only destructive actions need
approval; under 'strict', every mutate and egress call needs an operator OK.
Either way, the full run is captured in governance/audit.log.jsonl.

Usage:
  python -m governance.integration                       # standard posture
  python -m governance.integration strict                # require approvals
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))

import trade_study as TS          # noqa: E402
import lifecycle as L             # noqa: E402
import geom as G                  # noqa: E402
import desktop as d               # noqa: E402
from replay import replay         # noqa: E402

from .governor import govern      # noqa: E402
from .approval import AutoApprover, ConsoleApprover  # noqa: E402
from .policy import PolicyDenied  # noqa: E402


HOLO_ENDPOINT = "https://api.hcompany.ai/v1"


def _install_egress_guard(gov):
    """Wrap the Holo grounding/reading calls so every outbound model call is
    checked against the egress allowlist and audited. Monkey-patches at the
    desktop primitive layer so ALL vision traffic is covered, wherever it
    originates. Returns a restore() to undo."""
    orig_locate, orig_ask = d.locate, d.ask

    def guarded_locate(*a, **k):
        gov.check_egress(HOLO_ENDPOINT, action="holo_locate")
        return orig_locate(*a, **k)

    def guarded_ask(*a, **k):
        gov.check_egress(HOLO_ENDPOINT, action="holo_read")
        return orig_ask(*a, **k)

    d.locate, d.ask = guarded_locate, guarded_ask
    return lambda: (setattr(d, "locate", orig_locate), setattr(d, "ask", orig_ask))


def governed_study(model, variants=None, posture="standard", slices=20,
                   approver=None, clock=None):
    """Run the trade study with NemoClaw-pattern governance. Same result shape
    as bot.trade_study.study()."""
    variants = variants if variants is not None else TS.VARIANTS_EXAMPLE
    gov = govern(posture, approver=approver or ConsoleApprover(), clock=clock)
    restore = _install_egress_guard(gov)
    records = []
    try:
        for v in variants:
            print(f"\n=== variant: {v['name']} ===")
            gov.check("navigate", "relaunch_baseline", {"model": model})
            TS._reload_baseline(model)

            before = after = None
            if v.get("geom"):
                gov.check("navigate", "open_geom",
                          {"geom": v["geom"], "tab": v.get("tab")})
                G.open_geom(v["geom"], v.get("tree_desc"))
                G.goto_tab(v["tab"])
                before = G.read_field(v["field_desc"])
                after = _compute_after(before, v)
                # the one true mutation — gated (and approved under strict)
                gov.check("mutate", "set_geom_param",
                          {"geom": v["geom"], "field": v["field_desc"][:30],
                           "before": before, "after": after,
                           "pct": v.get("amount")})
                G.set_field(v["field_desc"], after)
                print(f"  {v['geom']} span {before} -> {after}")

            gov.check("navigate", "run_massprop", {"slices": slices})
            res = TS._measure_mass(slices)
            records.append({"variant": v["name"], "param_before": before,
                            "param_after": after, "mass": res.get("Total_Mass"),
                            "cg_x": res.get("X_Cg"), "izz": res.get("Izz"),
                            "results": res})
            print(f"  Total Mass = {res.get('Total_Mass')} | X_Cg = {res.get('X_Cg')}")

        TS._report(records)
        return records
    except PolicyDenied as e:
        print(f"\n[GOVERNANCE] action blocked: {e}")
        print("  (see governance/audit.log.jsonl for the full trail)")
        return records
    finally:
        restore()


def _compute_after(before, v):
    if before is None:
        return None
    op, amt = v.get("op", "scale"), v.get("amount", 0)
    if op == "scale":
        return round(before * (1 + amt), 4)
    if op == "delta":
        return round(before + amt, 4)
    return amt


def main(argv):
    posture = argv[0] if argv else "standard"
    model = argv[1] if len(argv) > 1 else "boeing777200.vsp3"
    # headless demo: auto-approve so 'strict' still runs, but the approval gate
    # is recorded in the audit log (never a silent bypass).
    approver = AutoApprover(decision=True) if posture == "strict" else ConsoleApprover()
    governed_study(model, posture=posture, approver=approver)


if __name__ == "__main__":
    main(sys.argv[1:])
