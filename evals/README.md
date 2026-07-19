# Jango evals — golden mission fixtures

Offline regression gate required by the controlled-improvement skill and the
improvement_discipline judge. A recipe/adapter/scorer change may not be promoted
until every fixture passes on the candidate version.

## What a fixture is

A fixture is a frozen mission contract plus its expected outcome envelope. It runs
through the same mission kernel as production work (synthetic backend by default,
real solver where marked) and is scored deterministically — no LLM judging in the
gate.

## Fixture schema (`fixtures/*.yaml`)

- `name`, `description`
- `backend`: `synthetic` | `real` — real-solver fixtures run only when the solver
  is available; CI runs synthetic ones.
- `contract`: objective metric + direction, constraints (metric/operator/value/unit),
  fan-out, cycle budget, acceptance and stop conditions — canonical names only,
  exactly as capability-first requires.
- `expected`:
  - `terminal_status`: `mission.completed`
  - `winner_metric`: value + `rel_tol` (tolerance-based, never exact-equality:
    solver and optimizer nondeterminism across backends makes exact matches produce
    false failures; a small relative tolerance is standard numerical-regression
    practice)
  - `constraints_satisfied`: true (every active constraint has a finite observed
    value, zero violation within tolerance — the evidence-loop review criteria,
    mechanized)
- `invariants` (event-level, from the mission event stream):
  - at least one `worker.provisioned`
  - `agent.completed` count ≥ declared fan-out minimum
  - `artifact.transferred` present when the contract declares a sequential handoff
  - zero unexplained `agent.failed` (failed agents allowed only if the fixture
    declares an expected-failure slot)

## Runner

`python -m evals.run --fixture fixtures/<name>.yaml [--backend synthetic]`

Exit 0 = pass, 1 = fail with a diff of expected vs observed. `run.py` in this
directory is a skeleton: the two TODO hooks bind it to the mission kernel's start
and event-read APIs and are the only integration points.

## Rules

- Every retro proposal must name its fixture; a defect without a fixture that would
  have caught it gets a new fixture in the same PR.
- Fixtures are append-only in spirit: tighten tolerances via PR, never loosen one to
  make a candidate pass without a written justification in the PR body.
- First fixture to write: the mass-sensitivity trade study (baseline vs +10%
  wingspan vs +10% tail), since it exercises contract → fan-out → compare → decision
  end-to-end and has known-good reference behavior.
