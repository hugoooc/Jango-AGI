# OpenVSP Navigation Mapper

An incremental Python prototype for observing and mapping the OpenVSP user interface on macOS.

The project implements Milestones 1 and 2 from [PLAN.md](PLAN.md): read-only observation plus one allowlisted, reversible interaction with the About dialog. It does not modify or save an OpenVSP model.

## Development commands

```bash
uv sync
uv run python -m app_mapper doctor
uv run python -m app_mapper observe
uv run python -m app_mapper exercise-about
uv run pytest
```

Generated observations are written to `artifacts/observations/` and ignored by Git.
Milestone 2 traces and before/destination/after captures are written to `artifacts/transitions/`.

## Milestone 2 manual test

1. Open OpenVSP with a blank, unsaved model and leave its main window visible.
2. Run `uv run python -m app_mapper doctor` and confirm both permissions are granted.
3. Run `uv run python -m app_mapper exercise-about` and watch About OpenVSP open and close.
4. Inspect the newest `artifacts/transitions/` directory. `trace.json` should report success, and the before, destination, and after captures should show the complete reversible transition.

The command only presses an exact enabled OpenVSP About menu item (`About vsp` in OpenVSP 3.51.0), then an allowlisted close, OK, Cancel, or accessibility-cancel control belonging to the newly detected window. It stops on unexpected UI or after a bounded timeout.
