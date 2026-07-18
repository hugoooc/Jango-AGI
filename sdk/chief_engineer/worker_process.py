"""One-shot, crash-contained OpenVSP API worker.

The controller launches this module once per candidate.  It deliberately uses
files for its response because OpenVSP and VSPAERO write verbose native output
to stdout and may terminate the process on a meshing failure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import OpenVSPDirectApi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.request.read_text())
        progress_path = Path(payload.get("progress_path", args.response.with_suffix(".progress.jsonl")))

        def publish_progress(item: dict) -> None:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            with progress_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item) + "\n")

        api = OpenVSPDirectApi(
            payload["model_path"],
            vspaero_path=payload.get("vspaero_path"),
            workdir=payload["workdir"],
            progress_sink=publish_progress,
        )
        try:
            metrics = api.evaluate(payload.get("design", {}), payload.get("analyses", []))
            response = {"metrics": dict(metrics), "artifacts": api.artifacts()}
        finally:
            api.close()
        args.response.write_text(json.dumps(response))
        return 0
    except Exception as exc:
        args.response.parent.mkdir(parents=True, exist_ok=True)
        args.response.write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
