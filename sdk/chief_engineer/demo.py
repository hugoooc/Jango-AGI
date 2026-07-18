"""Run the API-first chief-engineer loop without a GUI or external model key.

From ``sdk/``:

    python -m chief_engineer.demo "Optimize L/D while staying stable"

Use ``--model`` to switch the workers to the direct OpenVSP Python API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import OpenVSPDirectApi, SubprocessOpenVSPApi, SyntheticApi
from .mission import AutonomousChief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Hacknation chief engineer")
    parser.add_argument("goal", nargs="?", default="Optimize L/D while staying stable")
    parser.add_argument("--iterations", "--cycles", dest="cycles", type=int, default=3)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--model", type=Path, help="Use OpenVSPDirectApi with this .vsp3 model")
    parser.add_argument("--vspaero", type=Path, default=None)
    parser.add_argument("--workdir", type=Path, default=Path("./chief-engineer-runs"))
    args = parser.parse_args(argv)

    if args.model:
        model_path = args.model.resolve()
        vspaero_path = args.vspaero.resolve() if args.vspaero else None
        args.workdir.mkdir(parents=True, exist_ok=True)
        api_factory = lambda handle: SubprocessOpenVSPApi(
            model_path=model_path,
            vspaero_path=vspaero_path,
            workdir=args.workdir / handle.metadata.get("candidate_id", handle.id),
        )
    else:
        api_factory = lambda _handle: SyntheticApi()

    engine = AutonomousChief(
        "cli-mission",
        api_factory=api_factory,
        parameter_specs=OpenVSPDirectApi.PARAMETER_SPECS if args.model else (),
        metric_specs=OpenVSPDirectApi.METRIC_SPECS if args.model else (),
    )
    report = engine.run(
        args.goal,
        max_cycles=args.cycles,
        worker_budget=args.workers or 12,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
