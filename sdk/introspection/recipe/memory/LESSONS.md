# Distilled operating lessons

Cross-mission rules the Chief reads at the start of every objective, immediately after
`engineering.list_capabilities`. Lessons constrain *process*, never physics or solver
outputs. This file is part of the immutable recipe: every change is a commit, a new
runtime version, and therefore comparable against its predecessor.

## Write rules

- Only the retro task (see `../retro/RETRO_PROMPT.md`) may add or edit lessons, via PR.
- Every lesson cites evidence: pattern ids, mission ids, or judgement trends. No
  evidence, no lesson.
- Maximum 25 active lessons. At the cap, merge near-duplicates or retire the
  weakest-evidence rule before adding.
- A lesson unconfirmed by evidence across 3 consecutive retros is retired (moved to
  the Retired section with the retirement date, never silently deleted).
- Lessons must not restate doctrine already in SYSTEM.md or a skill — a lesson is
  something the trajectories taught us that the doctrine missed.

## Format

`[L-NNN] <rule, one sentence, imperative> — scope: <chief|specialist|critic|maintainer|all> — evidence: <pattern/mission ids, count> — added: <date> — last confirmed: <date>`

## Active lessons

(none yet — first retro populates this)

## Retired lessons

(none)
