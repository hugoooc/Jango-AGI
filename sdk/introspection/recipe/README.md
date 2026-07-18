# Jango on Introspection

This recipe is self-contained. The Chief, specialist agents, deterministic multidisciplinary optimizer, OpenVSP geometry mutation, evidence ledger, and deployment validation all execute inside the Introspection task. There is no MCP binding, tunnel, external worker API, Docker daemon, or local Jango service in the runtime path.

The objective is data-driven rather than prompt-specific. It supports target, minimize, or maximize studies over payload/people, range, take-off distance, climb, cruise/max speed, MTOW, landing distance, altitude, attitude, L/D, stability, stall speed, and fuel, with arbitrary cross-metric constraints.

Every run writes `study.json`, `events.jsonl`, `result.json`, `report.md`, a mutated `candidate.vsp3`, and a sidecar design manifest under `jango-runs/<run-id>/`. The bundled solver is conceptual/preliminary fidelity and does not claim to be CFD, FEA, or certification evidence.

Validate from the repository root:

```bash
npx @introspection-ai/cli recipes validate --path .introspection/jango.yaml
npx @introspection-ai/pi-recipes check sdk/introspection/recipe --profile publish
```

No credential belongs in this directory and no endpoint binding is required.
