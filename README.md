# OpenVSP Navigation Mapper

An incremental Python prototype for observing and mapping the OpenVSP user interface on macOS.

The project implements Milestones 1–10 from [PLAN.md](PLAN.md): observation, guarded interaction, Holo interpretation, stable node identity, graph validation, read-only expansion inventory, and guarded safe-screen expansion. It does not modify or save an OpenVSP model.

## Development commands

```bash
uv sync
uv run python -m app_mapper doctor
uv run python -m app_mapper observe
uv run python -m app_mapper exercise-about
uv run python -m app_mapper interpret
uv run python -m app_mapper capture-node
uv run python -m app_mapper graph show
uv run python -m app_mapper graph viewer
uv run python -m app_mapper inventory-menus
uv run python -m app_mapper expand-safe --max-candidates 3
uv run python -m app_mapper validate --sample-size 5
uv run python -m app_mapper discover-one-hop
uv run python -m app_mapper explore --max-depth 1 --max-nodes 10 --max-actions 18
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

## Milestone 6 bounded one-hop discovery

Start with a blank, unsaved OpenVSP workspace and no open menu or dialog. First generate a review plan; this performs no UI action:

```bash
uv run python -m app_mapper discover-one-hop
```

Expected plan:

- Holo's POD dropdown, Vehicle item, and Add proposals are recorded as rejected because none has an exact executable allowlist entry.
- File, View, and Model top-level menus are `approved | proposed`.
- `Actions executed: 0` confirms review-only mode.

Open the printed `artifacts/discovery-runs/<timestamp>/discovery.json` and review every policy reason. If the plan is correct, explicitly execute it:

```bash
uv run python -m app_mapper discover-one-hop --execute
```

Watch OpenVSP. The script should open and cancel File, then View, then Model—without selecting any item inside those menus. After each candidate it must return to the same workspace node before continuing.

Expected result:

```text
menu-file  | approved | succeeded
menu-view  | approved | succeeded
menu-model | approved | succeeded
Summary: rejected=3, succeeded=3
Actions executed: 6
```

Inspect each successful candidate directory under the printed run path. It must contain `before/`, `destination/`, `returned/`, and `trace.json`; `return_verified` must be true in `discovery.json`. Confirm OpenVSP still shows `Unnamed.vsp3` and no geometry was added or modified.

Finally run `uv run python -m app_mapper graph show`. If the Milestone 5 About graph was already recorded, a complete Milestone 6 run normally expands it from 2 nodes/2 edges to 5 nodes/8 edges: three menu nodes and an open/cancel edge pair for each. Failed or rejected candidates must not add graph edges.

## Milestone 7 bounded resumable exploration

The autonomous queue contains eight exact top-level menus plus About. It never selects a command inside a menu. Destinations have no deeper executable candidates, so the current safe policy naturally stops at depth one even when a larger depth bound is configured.

First test pause and resume with deliberately small bounds:

```bash
uv run python -m app_mapper explore \
  --max-depth 1 \
  --max-nodes 4 \
  --max-actions 4 \
  --max-seconds 60 \
  --pause-after-actions 2
```

Expected first result: `paused`, one completed task, two actions, and a printed resume command. Run that exact command. The resumed run should preserve the first task and stop predictably at `max_actions reached` after two total tasks and four actions.

Then run the full safe bound:

```bash
uv run python -m app_mapper explore \
  --max-depth 1 \
  --max-nodes 10 \
  --max-actions 18 \
  --max-seconds 180 \
  --max-retries 1
```

Expected completion:

```text
Status: completed
Stop reason: queue exhausted
Progress: tasks=9/9, nodes=10/10, actions=18/18
```

Inspect the printed `state.json`. Every task should be `succeeded`, `return_verified` should be true, attempts should be 1, and all ten node IDs should be unique. `graph show` should report 10 nodes and 18 edges. Confirm OpenVSP remains `Unnamed.vsp3` with no geometry changes.

As a replay sample, copy the workspace-to-File edge ID from `graph show` and run `uv run python -m app_mapper replay <edge-id>`. It should verify the File-menu destination node. macOS may automatically cancel a menu when that replay process exits, which is a safe return to the workspace.

You may press Ctrl+C during a run. The explorer attempts the allowlisted reverse action, persists `paused` state, and prints a resumable run path. Process and focus changes stop the run safely. `--allow-relaunch` is deliberately opt-in and should only be used with the required blank unsaved model because it may terminate and reopen OpenVSP.

## Milestone 8 graph viewer and validation

Generate the read-only viewer from the graph you already mapped:

```bash
uv run python -m app_mapper graph viewer
open artifacts/viewer/index.html
```

The HTML is self-contained, including the representative node screenshots. Select nodes to inspect their screenshot, type, semantic description, observations, and control counts. Select an arrow to inspect its exact action, Accessibility locator, risk, confidence, preconditions, expected destination, evidence, and replay success rate. The lower panels make duplicate candidates, failed transitions, rejected controls, and unexplored controls visible. The metric cards report mapped-state coverage, evidence-backed edges, control coverage, and cumulative replay reliability.

For the live acceptance test, start from the blank `Unnamed.vsp3` workspace with no menu or dialog open, then run:

```bash
uv run python -m app_mapper validate --sample-size 5 --seed 8
```

The validator reproducibly samples five safe forward edges. For every edge it verifies or restores the required source state, executes the recorded action, verifies the destination node, and follows a safe recorded path back to the source. Watch for five `success` lines and `Reliability: 5/5 (100%)`. Open the newest `artifacts/validations/<timestamp>/validation.json` and confirm each result has `source_restored`, `destination_verified`, and `return_verified` set to true. A changed UI is explicitly classified as `drift`, `stale_locator`, or `ambiguous_state`; failures remain visible in the next generated viewer.

To validate a particular edge instead of a random sample, repeat `--edge` as needed:

```bash
uv run python -m app_mapper validate --edge edge-cdd72807684a
```

Validation performs only recorded `safe_navigation`, reversible actions. It does not select commands inside menus or modify the model. Confirm the command finishes back at `Unnamed.vsp3` with no geometry present, then regenerate the viewer so its reliability metrics include the new attempts.

## Milestone 9 read-only expansion inventory

Start from OpenVSP's blank workspace and run:

```bash
uv run python -m app_mapper inventory-menus
```

The command reads the full Accessibility menu hierarchy without opening a menu or clicking a command. On OpenVSP 3.51.0 the current baseline is approximately 87 controls: 20 approved dialog/manager candidates, 37 requiring review, 27 rejected, and 3 submenu containers. The exact count can vary slightly with enabled state or application version, but the output must end with `Actions executed: 0 (read-only)`.

Open the printed `inventory.json` and check these safety examples:

- `Model > Set Editor...` is `safe_dialog | approved`.
- `Model > Geometry...` requires review because it raises an existing workspace window rather than creating a reversible destination.
- `Analysis > CompGeom...` is `analysis_workflow | review_required`.
- `File > Save...` is `file_operation | rejected`.
- `Edit > Delete` is `model_modifying | rejected`.
- `OpenVSP > Quit vsp` is `destructive | rejected`.

Then regenerate and open the viewer:

```bash
uv run python -m app_mapper graph viewer
open artifacts/viewer/index.html
```

Its lower panels now show the approved expansion frontier separately from blocked and review-required controls. Confirm OpenVSP remains on the unchanged `Unnamed.vsp3` workspace. This inventory is the review gate for the next milestone; it does not add graph nodes yet.

## Milestone 10 guarded safe-screen expansion

Review the next three unmapped candidates without clicking:

```bash
uv run python -m app_mapper expand-safe --max-candidates 3
```

Then start from the blank workspace with no open dialog and execute only that small batch:

```bash
uv run python -m app_mapper expand-safe --execute --max-candidates 3
```

For each candidate, watch one manager/dialog open and close without any field or inner button being used. Successful entries must report `succeeded`; the summary must contain `failed=0`; and actions should equal twice the number of successful new screens. Inspect `expansion.json` and confirm every success has a destination node, two edge IDs, and `return_verified: true`.

Run `uv run python -m app_mapper graph show`. Each successful distinct screen adds one node and two directed edges. Regenerate the viewer to see mapped approved screens, remaining frontier, and the safe-frontier coverage percentage:

```bash
uv run python -m app_mapper graph viewer
open artifacts/viewer/index.html
```

Do not run the full frontier until the three-screen batch is confirmed. A rerun automatically skips correctly mapped screens. Any ambiguous recovery stops instead of guessing which window to close.
