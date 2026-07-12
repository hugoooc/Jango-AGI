"""LegacyPilot showcase — the full 'APIized software' story, end to end.

Runs entirely on the compiled backend (no screen needed), demonstrating that
once the software is learned, the agent answers real engineering questions:

  1. Print the learned API (what the agent knows it can do).
  2. Single-point analysis.
  3. 1-D parameter sweep (span).
  4. Optimization (best sweep angle for L/D).
  5. Lift/drag polar.
  6. 2-D trade study (span x sweep) + Pareto frontier.
  7. A stability screen (flag designs by pitching moment).

This is the payoff: complete analyses & trade-offs from a tool that had no API.
"""
import time
from .skills import Registry
from . import analysis


def hr(t):
    print("\n" + "=" * 60 + f"\n  {t}\n" + "=" * 60)


def main():
    reg = Registry()
    t0 = time.time()

    hr("1. LEARNED API  (what the agent knows about OpenVSP)")
    print(reg.manifest())

    hr("2. SINGLE POINT  (baseline wing @ span=5.481)")
    r = analysis.single(reg, {"Span": 5.481})
    print(f"   CL={r['CL']:.4f}  CD={r['CD']:.5f}  L/D={r['L_D']:.2f}  CMy={r['CMy']:.4f}")

    hr("3. SPAN SWEEP  (5 -> 13 m)")
    sw = analysis.sweep(reg, "Span", [5, 7, 9, 11, 13])
    for x in sw:
        if "L_D" in x:
            print(f"   span {x['value']:5.1f}  ->  L/D {x['L_D']:6.2f}  (CL {x['CL']:.4f})")

    hr("4. OPTIMIZE  (best sweep angle for L/D, span=9)")
    opt = analysis.optimize(reg, "Sweep", [0, 15, 25, 35, 45],
                            metric="L_D", base={"Span": 9.0})
    b = opt["best"]
    print(f"   BEST: sweep={b['value']:.0f}deg -> L/D={b['L_D']:.2f}")

    hr("5. POLAR  (alpha 0 -> 10 deg, span=9)")
    pol = analysis.polar(reg, params={"Span": 9.0}, alpha_start=0, alpha_end=10, npts=6)
    for p in pol:
        print(f"   a={p['alpha']:4.1f}  CL={p['CL']:.4f}  L/D={p['L_D']:6.2f}")

    hr("6. TRADE STUDY  (span x sweep) + PARETO")
    grid = analysis.grid(reg, ["Span", "Sweep"], [[6, 9, 12], [10, 30, 45]])
    for r in sorted(grid, key=lambda r: -r.get("L_D", 0)):
        if "L_D" in r:
            c = r["combo"]
            print(f"   span {c['Span']:.0f}, sweep {c['Sweep']:.0f}  ->  "
                  f"L/D {r['L_D']:6.2f}, CMy {r['CMy']:.4f}")
    front = analysis.pareto(grid, x="CMy", y="L_D")
    print("   -- Pareto (max L/D vs min |pitch|):")
    for r in front:
        c = r["combo"]
        print(f"      span {c['Span']:.0f}, sweep {c['Sweep']:.0f}: "
              f"L/D {r['L_D']:.2f}, CMy {r['CMy']:.4f}")

    hr("7. STABILITY SCREEN  (flag high pitching-moment designs)")
    flagged = [r for r in grid if abs(r.get("CMy", 0)) > 0.09]
    if flagged:
        for r in flagged:
            c = r["combo"]
            print(f"   ⚠ span {c['Span']:.0f}, sweep {c['Sweep']:.0f}: "
                  f"CMy={r['CMy']:.4f} (nose-heavy)")
    else:
        print("   all designs within |CMy| < 0.09")

    print(f"\n[showcase complete] {time.time()-t0:.0f}s — all on the compiled backend")


if __name__ == "__main__":
    main()
