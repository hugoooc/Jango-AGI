# OpenVSP Navigation Mapper

An incremental Python prototype for observing and mapping the OpenVSP user interface on macOS.

The project implements Milestones 1–3 from [PLAN.md](PLAN.md): observation, one allowlisted reversible interaction, and read-only Holo interpretation. It does not modify or save an OpenVSP model.

## Development commands

```bash
uv sync
uv run python -m app_mapper doctor
uv run python -m app_mapper observe
uv run python -m app_mapper exercise-about
uv run python -m app_mapper interpret
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

## Milestone 3 Holo interpretation

Create a free Portal-H API key, export it without writing it into the repository, and run:

```bash
export HAI_API_KEY="your-key"
uv run python -m app_mapper interpret
```

This command captures the visible OpenVSP workspace and accessibility landmarks, sends them to Holo using a constrained JSON schema, and writes results under `artifacts/interpretations/`. It never clicks or executes any proposed target. The screenshot and compact OpenVSP accessibility context are sent to H Company's Models API; the key is never written to an artifact.

Important files in each run:

- `screenshot.png`: the exact image Holo analyzed.
- `request.json`: model, image metadata, accessibility context, and schema, with no API key or embedded image bytes.
- `raw-response.json`: the full API response.
- `interpretation.json`: locally validated safe targets plus targets rejected by risk, keyword, or confidence policy.

Optional configuration is documented in `.env.example`. `HOLO_MODEL` defaults to `holo3-1-35b-a3b`, and accepted target confidence defaults to `0.5`.
