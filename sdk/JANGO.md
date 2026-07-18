# Jango execution architecture

The active hosted product is `introspection/recipe`: a commit-pinned Pi recipe whose agents and numerical tools all run in the Introspection task filesystem.

## Chief loop

1. `jango_capabilities` establishes the supported metric and design-variable contract.
2. `jango_inspect_geometry` validates and fingerprints the user `.vsp3`.
3. The Chief formalizes one objective, constraints, tolerance, budget, and seed.
4. It fans out any number of role instances through Pi's subagent mechanism.
5. It performs explicit sequential hand-offs by including prior-round geometry/design evidence in later specialist prompts.
6. `jango_run_study` executes the coupled deterministic search and persists evidence.
7. Critic and verification agents independently inspect the run id.
8. The Chief either revises and starts another run or closes accepted/infeasible.

Child agents cannot create grandchildren. This is intentional: the Chief remains accountable for team topology, information transfer, and resource use.

## Numerical kernel

`introspection/recipe/solver/` has no third-party runtime dependency. Node's standard library provides the task-local execution path:

- `catalog.mjs` — canonical metrics, aliases, units, bounds, and typed study normalization.
- `geometry.mjs` — OpenVSP integrity/provenance inspection and bounded main-wing/horizontal-tail mutation.
- `model.mjs` — atmosphere, drag polar, weights, Breguet range, field performance, climb, speed, ceiling, attitude, stability, and fuel-volume gates.
- `optimizer.mjs` — seeded global exploration, adaptive elite mutation, penalties, acceptance gates, deterministic replay, and artifacts.
- `dashboard.mjs` — self-contained live evidence cockpit that polls the JSONL ledger.
- `cli.mjs` — direct non-agent catalog, inspect, and study execution.

The kernel is deliberately preliminary fidelity. Higher-fidelity OpenVSP/VSPAERO/CFD/structures tools should be introduced as task-local executables behind additional tools and compared by the verification role; they must not be silently impersonated by this model.

## Artifact contract

Each run creates `jango-runs/<run-id>/` containing:

- `study.json`
- `source-geometry.json`
- `events.jsonl`
- `result.json`
- `report.md`
- `dashboard.html`
- `candidate.vsp3`
- `candidate.vsp3.jango.json`

`result.json` is authoritative. A run is accepted only if the objective tolerance, every explicit/preserved constraint, geometry validity, and deterministic replay gates pass.

## Legacy boundary

`chief_engineer/`, `docker/`, and `control_room.html` implement the earlier external API-worker/MCP architecture. They remain available for comparison but are neither imported nor required by the Introspection recipe. The hosted recipe has no endpoint declaration and needs no bearer token, tunnel, local service, Docker, or VM provider.
