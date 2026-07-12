# OpenVSP Navigation Mapper — Working Plan

## Objective

Build a Python tool that explores the OpenVSP macOS user interface and produces a replayable navigation graph.

The graph will contain:

- Nodes representing meaningful OpenVSP UI states, such as the main workspace, manager windows, dialogs, tabs, and materially different panels.
- Metadata for each node, including screenshots, semantic descriptions, accessibility information, fingerprints, and the OpenVSP version.
- Directed edges describing how to move between nodes through the UI.
- Replay information for each edge, including robust locators, expected results, reversibility, risk, and validation history.

The initial target is OpenVSP 3.51.0 installed at:

```text
/Applications/OpenVSP.app
```

## Working Agreement

We will implement one milestone at a time.

For every milestone:

1. Codex implements only the scoped milestone.
2. Codex runs safe automated checks where possible.
3. Codex provides an exact manual test procedure.
4. The user runs the manual test and reports the result.
5. We fix the current milestone until its acceptance gate passes.
6. Only then do we begin the next milestone.

This document is the source of truth for scope and progress. We will update it as the design changes and mark milestones complete only after manual validation.

## Core Design Principles

### A node is a navigational state, not a screenshot

Screenshots can change because of window position, hover effects, timestamps, selection highlights, animations, or document content. These changes should not automatically create new nodes.

A node represents a stable, meaningful UI state. OpenVSP is not page-oriented like a website, so nodes may include:

- Main workspaces.
- Manager or browser windows.
- Modal and non-modal dialogs.
- Tabs with materially different content.
- Menus or popovers when they are necessary navigation surfaces.
- Meaningful panel modes.
- External transitions, recorded but not initially explored.

### Python owns the exploration loop

Holo will provide visual and semantic interpretation, but it will not own the entire exploration process. The Python application will control:

- Observation.
- State identity and deduplication.
- Candidate filtering.
- Action execution.
- Safety policy.
- Graph persistence.
- Recovery and replay.

### Prefer hybrid UI understanding

For each state, the mapper should collect both:

- A screenshot of the OpenVSP window.
- The macOS Accessibility tree, when available.

OpenVSP uses FLTK, so macOS may expose less accessibility information than it would for a native Cocoa application. Milestone 1 will measure this before we choose an interaction strategy.

The preferred action order is:

1. A stable macOS Accessibility action.
2. A keyboard shortcut or keyboard navigation.
3. Visual localization and coordinate-based interaction.

### Every transition needs evidence

Every attempted action must record:

- The source observation.
- The intended action.
- The actual action performed.
- Before and after screenshots.
- The resulting state.
- Whether the expected postcondition was satisfied.
- Any error, timeout, or recovery attempt.

## Safety Defaults

Until explicitly expanded, all exploration will follow these rules:

- Start with a blank, unsaved OpenVSP model.
- Never save, overwrite, import, or export files.
- Never run an analysis.
- Never add, delete, rename, or modify geometry.
- Never submit forms or enter free-form text.
- Never change permissions or system settings.
- Never open external websites during automated exploration.
- Never intentionally close OpenVSP.
- Explore only one action away from a known state until restoration is proven.
- Require a configurable confidence threshold before an automated action.
- Stop if OpenVSP is no longer the active target application.
- Capture evidence before and after every action.
- Keep actions observable so the user can interrupt the run.

Candidate actions will eventually use these risk levels:

```text
safe_navigation
reversible_state_change
external_transition
destructive
unknown
```

Only `safe_navigation` actions are initially eligible for automatic execution.

## Milestone 1 — Read-Only Observation

### Goal

Reliably detect and observe OpenVSP without controlling it.

### Planned interface

```bash
python -m app_mapper doctor
python -m app_mapper observe
```

### Scope

- Scaffold the Python project.
- Detect `/Applications/OpenVSP.app`.
- Report the installed OpenVSP version.
- Detect whether OpenVSP is running.
- Identify its process and visible windows.
- Report Screen Recording and Accessibility readiness.
- Capture the relevant OpenVSP window.
- Dump the macOS Accessibility information available for the app.
- Store observations under `artifacts/observations/`.
- Do not move the mouse, type, click, or alter OpenVSP.

### Manual test

1. Open OpenVSP with a blank, unsaved model.
2. Run `python -m app_mapper doctor`.
3. Resolve any requested macOS permissions.
4. Run `python -m app_mapper observe`.
5. Confirm that the screenshot contains the intended OpenVSP window.
6. Inspect the generated accessibility JSON for recognizable labels, roles, or controls.

### Acceptance gate

- OpenVSP is detected reliably.
- Its version is reported correctly.
- The correct OpenVSP window can be captured.
- Observation artifacts are readable and organized.
- We know whether future interaction should be accessibility-first, vision-first, or hybrid.

### Status

`Implemented — awaiting manual validation`

### Implementation notes

- The project uses a `uv`-managed Python 3.12 environment so the system Python is not modified.
- `doctor` performs non-prompting permission preflight checks.
- `observe` does not launch, activate, focus, click, or type into OpenVSP.
- Window capture uses the selected Core Graphics window ID.
- Accessibility traversal is bounded by depth and element count.
- A stopped observation still preserves a diagnostic manifest when possible.

## Milestone 2 — One Safe, Reversible Interaction

### Goal

Execute and verify one harmless navigation transition.

### Initial target transition

```text
OpenVSP main state -> informational dialog -> OpenVSP main state
```

The About dialog is the preferred target if it can be accessed reliably. Another harmless informational window may be substituted based on Milestone 1 findings.

### Scope

- Capture the source state.
- Locate one safe target using the best available mechanism.
- Perform one click or keyboard action.
- Wait for the interface to become stable.
- Capture the destination state.
- Close or dismiss the destination safely.
- Verify that OpenVSP returned to the original state.
- Record a structured trace of the attempt.

### Manual test

1. Start from the required blank OpenVSP state.
2. Run the milestone command provided during implementation.
3. Watch the complete interaction.
4. Confirm the intended dialog or window opened.
5. Confirm it closed safely.
6. Compare the before and after artifacts.

### Acceptance gate

- The same transition succeeds repeatedly.
- The interaction does not modify or save the model.
- The script detects both the destination and the return to the source.
- Failures time out safely and leave useful evidence.

### Status

`Implemented — awaiting repeated manual validation`

## Milestone 3 — Read-Only Holo Interpretation

### Goal

Use an H Company Holo model to describe the current state and identify potential navigation controls without executing them.

### Proposed Holo output

```json
{
  "state": {
    "title": "OpenVSP Main Window",
    "type": "workspace",
    "description": "Blank vehicle workspace"
  },
  "navigation_targets": [
    {
      "label": "Example target",
      "action_type": "click",
      "bounding_box": [0, 0, 0, 0],
      "expected_destination": "Example destination",
      "risk": "safe_navigation",
      "confidence": 0.95
    }
  ]
}
```

### Scope

- Add Holo API configuration without committing secrets.
- Send the full screenshot and useful accessibility context to Holo.
- Require structured output validated by a local schema.
- Store the raw response and validated interpretation.
- Reject invalid, incomplete, or unsafe results.
- Do not let Holo initiate actions during this milestone.

### Manual test

1. Capture several known OpenVSP states.
2. Run Holo interpretation on each observation.
3. Compare its state descriptions with the visible UI.
4. Check whether proposed targets are real navigation controls.
5. Check visual bounding boxes or accessibility matches.
6. Record important false positives and missed controls.

### Acceptance gate

- Holo responses validate against the local schema.
- Holo identifies multiple real OpenVSP navigation controls.
- Its visual locations or accessibility matches are usable.
- Unsafe actions are classified or filtered out reliably enough for the next controlled experiment.

### Status

`Implemented — manually validated`

## Milestone 4 — Node Identity and Deduplication

### Goal

Define when two observations represent the same navigational state.

### Scope

- Create the node schema.
- Normalize accessibility data.
- Compute structural fingerprints where possible.
- Compute visual or perceptual fingerprints.
- Record semantic landmarks from Holo.
- Combine deterministic and semantic signals for state matching.
- Preserve both the stable node identity and individual observations.

### Required tests

- Capture the main screen twice: same node.
- Move the window: same node.
- Hover over a control: same node.
- Capture minor transient visual changes: normally the same node.
- Open an informational dialog: different node.
- Close the dialog: return to the original node.

### Candidate node metadata

- Stable node ID.
- Semantic name and description.
- State type.
- Application and version.
- Window role, title, and geometry.
- Stable UI landmarks.
- Interactive control summary.
- Accessibility fingerprint.
- Visual fingerprint.
- Holo semantic signature.
- Representative screenshot.
- First-seen and last-seen timestamps.
- Number of observations.

### Acceptance gate

- Repeated observations do not create unnecessary duplicate nodes.
- Meaningfully different OpenVSP states remain distinct.
- Ambiguous matches are surfaced for review rather than silently merged.

### Status

`Implemented — core validation passed, awaiting full manual matrix`

## Milestone 5 — First Replayable Graph Edge

### Goal

Persist and replay the first verified transition between two nodes.

### Target graph

```text
OpenVSP workspace --[open informational dialog]--> informational dialog
informational dialog --[dismiss]--> OpenVSP workspace
```

### Candidate edge metadata

- Stable edge ID.
- Source and destination node IDs.
- Semantic action description.
- Action mechanism.
- Accessibility locator, if available.
- Keyboard locator, if available.
- Visual locator and bounding box.
- Preconditions.
- Expected postconditions.
- Risk classification.
- Reversibility and reverse action.
- Before and after evidence.
- Replay attempts and success rate.

### Planned interface

```bash
python -m app_mapper graph show
python -m app_mapper replay <edge-id>
```

### Scope

- Add graph persistence.
- Record the two safe transitions.
- Export JSON.
- Export GraphML for external graph tools.
- Replay an edge only after verifying its expected source state.
- Verify the resulting state after replay.

### Manual test

1. Inspect the graph export.
2. Select the recorded edge.
3. Put OpenVSP in the required source state.
4. Replay the edge.
5. Confirm that the correct destination opens.
6. Replay or perform the safe reverse transition.

### Acceptance gate

- The edge is understandable from its stored metadata.
- It can be replayed from the verified source state.
- The observed destination matches the recorded destination node.
- Replay refuses to proceed from the wrong source state.

### Status

`Implemented — manually validated`

## Milestone 6 — Whitelisted One-Hop Discovery

### Goal

Discover and test multiple safe navigation transitions from the main OpenVSP state.

### Initially allowed targets

- Informational dialogs.
- Manager windows that do not immediately modify the model.
- View and display panels.
- Menus that only expose further choices.
- Tabs in an already open window.

### Initially excluded targets

- Save, overwrite, import, or export.
- Delete, clear, remove, or reset.
- Analysis execution.
- Geometry creation or mutation.
- External websites.
- Application exit.
- Free-form text entry.
- Any action classified as `unknown`.

### Scope

- Ask Holo for navigation candidates.
- Apply a deterministic local safety policy.
- Execute only whitelisted candidates.
- Explore one candidate at a time.
- Restore the main state between candidates.
- Record successful, failed, and rejected candidates.
- Allow a human-reviewed allowlist when classification is uncertain.

### Manual test

1. Review the proposed candidates before execution.
2. Run the one-hop explorer.
3. Watch every attempted action.
4. Confirm the generated nodes and edges.
5. Inspect rejected and failed candidates.

### Acceptance gate

- Several real OpenVSP states are discovered.
- Every attempted action has a complete trace.
- The script returns to a known state after each candidate.
- The model remains unmodified and unsaved.

### Status

`Implemented — manually validated`

## Milestone 7 — Bounded Autonomous Exploration

### Goal

Explore a small portion of OpenVSP with predictable limits and resumable progress.

### Planned interface

```bash
python -m app_mapper explore \
  --app OpenVSP \
  --max-depth 3 \
  --max-nodes 20 \
  --risk safe_navigation
```

### Exploration loop

For each eligible candidate:

1. Restore or replay the source state.
2. Verify the source fingerprint.
3. Capture a fresh source observation.
4. Execute one approved action.
5. Wait for visual stability.
6. Capture the destination observation.
7. Match or create the destination node.
8. Record the edge and evidence.
9. Return to a known state.
10. Stop on any unrecoverable uncertainty.

### Scope

- Implement bounded breadth-first exploration.
- Add maximum depth, node, action, time, and retry limits.
- Persist the exploration queue.
- Support pause and resume.
- Relaunch and replay a known path when simple reversal is unreliable.
- Detect loops and duplicate states.
- Stop safely when focus or application identity changes unexpectedly.

### Manual test

1. Review all configured bounds.
2. Begin with a very small node and depth limit.
3. Watch the exploration.
4. Interrupt and resume it.
5. Inspect the graph and traces.
6. Replay a sample of discovered edges.

### Acceptance gate

- The mapper discovers approximately 10–20 meaningful states.
- It respects every configured bound.
- It terminates predictably.
- It resumes without losing graph consistency.
- It does not enter excluded workflows or alter the model.

### Status

`Implemented — manually validated`

## Milestone 8 — Graph Viewer and Validation

### Goal

Make the graph easy to inspect and measure its replay reliability.

### Viewer features

- Node screenshots.
- Node type and semantic metadata.
- Directed navigation edges.
- Action instructions and locators.
- Confidence and risk indicators.
- Replay status and success rate.
- Duplicate-node candidates.
- Failed transitions.
- Rejected and unexplored controls.

### Validation features

- Sample recorded edges.
- Restore each required source state.
- Replay the action.
- Verify the destination.
- Record success, failure, and drift.
- Flag stale locators and ambiguous state matches.

### Acceptance gate

- A human can understand the mapped OpenVSP UI from the viewer.
- Recorded navigation can be sampled and replayed.
- Broken, unsafe, or ambiguous edges are clearly visible.
- We have a measurable baseline for graph coverage and replay reliability.

### Status

`Not started`

## Likely Technical Stack

- Python 3.12.
- PyObjC for macOS application, window, Accessibility, and event integration.
- Native macOS screenshot APIs or a compatible command-line fallback.
- Pillow for image processing.
- Pydantic for configuration and validated schemas.
- H Company Holo model API for semantic UI interpretation and visual grounding.
- SQLite for canonical state, graph, observation, and trace storage.
- NetworkX for in-memory traversal and analysis.
- JSON and GraphML for export.
- A lightweight local HTML graph viewer in the final milestone.

The exact dependencies should be selected incrementally. Milestone 1 should not install Holo or graph-viewer dependencies that it does not need.

## Proposed Project Layout

This structure is provisional and may evolve:

```text
.
├── PLAN.md
├── pyproject.toml
├── src/
│   └── app_mapper/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── macos/
│       │   ├── applications.py
│       │   ├── accessibility.py
│       │   ├── screenshots.py
│       │   └── actions.py
│       ├── perception/
│       │   ├── holo.py
│       │   └── schemas.py
│       ├── graph/
│       │   ├── models.py
│       │   ├── fingerprints.py
│       │   └── store.py
│       ├── exploration/
│       │   ├── policy.py
│       │   ├── explorer.py
│       │   └── replay.py
│       └── artifacts.py
├── tests/
└── artifacts/
    ├── observations/
    ├── screenshots/
    ├── traces/
    └── exports/
```

Generated artifacts and secrets must not be committed by default.

## Node and Edge Sketches

These are design sketches, not final schemas.

### Node

```python
Node(
    id="openvsp.workspace.blank",
    app="OpenVSP",
    app_version="3.51.0",
    state_type="workspace",
    semantic_title="Blank OpenVSP workspace",
    landmarks=["Geometry Browser", "3D viewport"],
    representative_screenshot="artifacts/screenshots/node-id.png",
    accessibility_snapshot="artifacts/observations/node-id.ax.json",
    structural_fingerprint="...",
    visual_fingerprint="...",
    semantic_fingerprint="...",
)
```

### Edge

```python
Edge(
    id="edge-id",
    source="openvsp.workspace.blank",
    target="openvsp.dialog.about",
    action={
        "kind": "press",
        "label": "About OpenVSP",
        "accessibility_locator": None,
        "visual_bbox": [0, 0, 0, 0],
        "keyboard_shortcut": None,
    },
    preconditions=["OpenVSP is active", "blank workspace is visible"],
    expected_postconditions=["About dialog is visible"],
    risk="safe_navigation",
    reversible=True,
    replay_attempts=0,
    replay_successes=0,
)
```

## Deferred Questions

These should be answered through experiments instead of assumed upfront:

- How much of OpenVSP's FLTK interface is exposed through macOS Accessibility?
- Can individual OpenVSP windows be captured reliably without including other apps?
- Which actions are most reliable: accessibility, keyboard, or visual clicks?
- What visual changes should be ignored when deduplicating states?
- Should menus be persistent nodes or transition-only observations?
- Should selecting a different geometry or tree item create a new node?
- How should non-modal windows and multiple simultaneous OpenVSP windows be represented?
- How reliably can the mapper restore a state using reverse actions?
- When should restoration relaunch OpenVSP and replay a known path?
- What Holo model and prompting strategy give the best accuracy/cost tradeoff?
- How should we measure graph coverage when the complete OpenVSP UI graph is unknown?

## Future Work Outside the Initial Milestones

- Controlled geometry-editing exploration in a disposable model.
- Use the OpenVSP Python API to create fixtures and reset known model states.
- Map context-dependent UI states for different geometry types.
- Compare discovered navigation against OpenVSP documentation or source code.
- Add human review and approval queues.
- Support other macOS applications.
- Add graph differencing across application versions.
- Test navigation robustness across display scaling and window sizes.
- Run OpenVSP in an isolated VM or test account for broader exploration.

## Progress Summary

| Milestone | Description | Status |
|---|---|---|
| 1 | Read-only observation | Implemented — awaiting manual validation |
| 2 | One safe, reversible interaction | Implemented — manually validated |
| 3 | Read-only Holo interpretation | Implemented — manually validated |
| 4 | Node identity and deduplication | Implemented — awaiting full manual matrix |
| 5 | First replayable graph edge | Implemented — manually validated |
| 6 | Whitelisted one-hop discovery | Implemented — manually validated |
| 7 | Bounded autonomous exploration | Implemented — manually validated |
| 8 | Graph viewer and validation | Not started |

## Immediate Next Step

Review the completed Milestone 7 state, traces, and graph bounds, then build the Milestone 8 graph viewer and validation metrics.
