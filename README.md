# OpenVSP Navigation Mapper

An incremental Python prototype for observing and mapping the OpenVSP user interface on macOS.

The project implements Milestones 1–5 from [PLAN.md](PLAN.md): observation, one allowlisted reversible interaction, Holo interpretation, stable node identity, and the first replayable graph edges. It does not modify or save an OpenVSP model.

## Development commands

```bash
uv sync
uv run python -m app_mapper doctor
uv run python -m app_mapper observe
uv run python -m app_mapper exercise-about
uv run python -m app_mapper interpret
uv run python -m app_mapper capture-node
uv run python -m app_mapper graph show
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

## Milestone 4 node identity

`capture-node` takes a read-only OpenVSP-only screenshot and accessibility snapshot, then either creates a node or matches an existing one. It does not call Holo and performs no UI action. `interpret` now also assigns a node automatically after a successful Holo response.

Node data is stored under `artifacts/nodes/`. Every individual capture remains under `artifacts/node-observations/` with:

- `identity.json`: `new`, `matched`, or `ambiguous`, plus candidate scores and reasons.
- `fingerprints.json`: normalized accessibility, perceptual image, and semantic fingerprints.
- `normalized-accessibility.json`: the stable structure after geometry, focus, dates, versions, and document-name volatility are removed.

### Manual acceptance test

Use a separate registry so the expected counts are easy to inspect:

```bash
REGISTRY=artifacts/nodes-manual-test
OBSERVATIONS=artifacts/node-observations-manual-test

uv run python -m app_mapper capture-node --registry-root "$REGISTRY" --artifact-root "$OBSERVATIONS"
uv run python -m app_mapper capture-node --registry-root "$REGISTRY" --artifact-root "$OBSERVATIONS"
```

The first result should be `new`. The second should be `matched` with exactly the same node ID.

Next, move the OpenVSP windows and capture again. Hover over a control and capture once more. Both should remain `matched` to the original node; small screenshot changes are expected, but the stable node ID must not change.

Then manually open **OpenVSP → About vsp**, leave it open, and run the same `capture-node` command. It should be `new` with a different node ID. Close About and capture again; it should be `matched` to the original workspace node.

Finally inspect `$REGISTRY/index.json`. It should normally contain exactly two nodes: one workspace node with multiple observations and one dialog node. For each capture, inspect `identity.json`: `review_required` should be `false`. If a result is `ambiguous`, it must have `node_id: null`, `review_required: true`, and candidate reasons; this is a safe refusal to merge, not a test failure in the safety mechanism.

## Milestone 5 replayable graph edges

Start from the normal OpenVSP workspace with no dialog open, then record the verified round trip:

```bash
uv run python -m app_mapper graph record-about
uv run python -m app_mapper graph show
```

`graph show` should report two nodes and two directed edges:

```text
workspace --[open About]--> dialog
dialog --[dismiss About]--> workspace
```

Copy the forward edge ID from `graph show` and replay it. A successful forward replay leaves About open so you can inspect the destination:

```bash
uv run python -m app_mapper replay <open-about-edge-id>
```

Then copy and replay the dismiss edge ID. It should close About and verify the workspace node:

```bash
uv run python -m app_mapper replay <dismiss-about-edge-id>
```

To test the source-state guard, while the normal workspace is visible, try the dismiss edge again. The command must print `Replay refused safely`, state that no action was taken, and exit with code 5. In `graph show`, that edge records the refusal separately from successful replays.

Inspect `artifacts/graph/graph.json` for readable action metadata, locators, preconditions, postconditions, evidence paths, and replay statistics. `artifacts/graph/graph.graphml` is the equivalent external-tool export. Every replay stores its source and destination evidence under `artifacts/replays/`.
