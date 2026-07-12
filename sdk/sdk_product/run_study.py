"""Entry point: run a parallel mass trade study on the fleet, then aggregate.

  python -m sdk_product.run_study                 # local desktop worker (1 machine)
  python -m sdk_product.run_study --workers 3     # simulate a 3-worker pool spec

Aggregates each variant's structured MassResult into a comparison table and
names the largest-impact variant — the same payoff as bot/trade_study, but the
runs execute through the SDK fleet (parallel where the pool allows).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .fleet import run_fleet
from .vsp_worker import desktop_worker, span_variants, MassResult


def _api_key() -> str:
    k = os.environ.get("HAI_API_KEY")
    if k:
        return k
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith(("HAI_API_KEY=", "HCOMPANY_API_KEY=")):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("no HAI_API_KEY / HCOMPANY_API_KEY found")


def aggregate(results) -> None:
    ok = [r for r in results if r.ok and r.answer]
    print("\n" + "=" * 70)
    print("  PARALLEL MASS TRADE STUDY  (via hai-agents fleet)")
    print("=" * 70)
    if not ok:
        print("  no successful runs:")
        for r in results:
            print(f"    {r.variant:16} [{r.worker}] FAILED: {r.error}")
        return

    base = next((r for r in ok if r.variant == "baseline"), None)
    base_mass = _mass(base.answer) if base else None
    print(f"  {'variant':16} {'mass':>10} {'d mass':>10} {'X_Cg':>9}  worker")
    for r in ok:
        m = _mass(r.answer)
        dm = "" if base_mass is None or m is None else f"{m - base_mass:+.2f}"
        cg = _field(r.answer, "cg_x")
        print(f"  {r.variant:16} {str(m):>10} {dm:>10} {str(cg):>9}  {r.worker}")
    deltas = [(r.variant, abs(_mass(r.answer) - base_mass)) for r in ok
              if base_mass is not None and _mass(r.answer) is not None and r is not base]
    if deltas:
        win = max(deltas, key=lambda t: t[1])
        print(f"\n  Largest mass impact: {win[0]} ({win[1]:+.2f})")


def _mass(answer):
    return _field(answer, "total_mass")


def _field(answer, key):
    if isinstance(answer, dict):
        return answer.get(key)
    return getattr(answer, key, None)


def main(argv) -> int:
    workers = [desktop_worker("local-desktop")]  # 1 machine = 1 desktop worker
    variants = span_variants()
    key = _api_key()
    print(f"Dispatching {len(variants)} variants across {len(workers)} worker(s)...")
    print("(local desktop is serialized to 1; add remote/cloud workers to parallelize)")
    results = run_fleet(key, variants, workers, posture="standard")
    aggregate(results)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
