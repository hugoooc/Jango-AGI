# Experiment runbook — candidate vs incumbent

How a proposed recipe version earns promotion. Complements the
controlled-improvement skill; nothing here overrides `auto_promote: false`.

## Goal metric

- Primary experiment goal: **engineering_goal_adherence pass-rate**. It measures the
  one thing every mission must do — solve the user's actual objective.
- Guard metrics (must not regress beyond noise while the primary improves):
  evidence_traceability and chief_process_quality pass-rates, p95 conversation
  duration, cost per mission.
- improvement_discipline is a standing invariant, not an experiment goal: any fail
  on either arm blocks promotion regardless of the primary.

## Invariants

- Same judge versions on both arms. A PR that changes a judge may not also change
  behavior; judge changes ship alone and re-baseline the trends.
- One environment lane per experiment (staging first; production experiments only
  for changes that passed a staging experiment).
- Sticky per-subject assignment is platform-provided — do not restart tasks to
  reshuffle arms.
- Offline gate before any experiment: all `evals/` fixtures pass on the candidate.

## Procedure

1. Candidate exists as a pushed commit → the integration creates the immutable
   version in the `jango` runtime group.
2. `evals` green on the candidate (synthetic set at minimum; real-solver fixtures
   if the change touches an adapter or scorer).
3. Pin staging to the incumbent baseline; define the experiment with arms
   {incumbent, candidate}, goal = engineering_goal_adherence pass-rate.
4. Run until each arm has enough missions for the decision to be evidence, not
   noise — as a working floor, ≥20 applicable judgements per arm and a stable gap;
   record both counts in the decision note.
5. End the experiment (stops evidence collection), write a one-paragraph decision
   note in the PR: primary delta, guard metrics, fixture run, residual risk.
6. Ship the winner as the Git decision: merge to main promotes production. Keep the
   incumbent version available; `runtimes deactivate` is the rollback.

## Anti-patterns

- Peeking and stopping the moment the candidate leads.
- Comparing arms across lanes or across judge versions.
- Promoting on vibes from a handful of hand-picked conversations — that is what the
  experiment exists to replace.
