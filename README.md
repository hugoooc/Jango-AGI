# OpenVSP Navigation Mapper

An incremental Python prototype for observing and mapping the OpenVSP user interface on macOS.

The project is currently implementing Milestone 1 from [PLAN.md](PLAN.md): read-only observation. At this stage it does not click, type, focus, launch, or otherwise control OpenVSP.

## Development commands

```bash
uv sync
uv run python -m app_mapper doctor
uv run python -m app_mapper observe
uv run pytest
```

Generated observations are written to `artifacts/observations/` and ignored by Git.

