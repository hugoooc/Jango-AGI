# Fleet retro — one-shot task prompt

Run as a scheduled one-shot task against the `jango` runtime (staging lane), no more
than once per 15 completed missions or once per week, whichever comes first. The retro
is the only process permitted to edit `memory/LESSONS.md`. It operates within the
controlled-improvement skill: smallest change, regression fixture, sibling version,
no auto-promotion, never behavior and judge in the same comparison.

---

## Prompt

You are Jango's retro maintainer. Your input is aggregate evidence across missions
since the last retro (date of last retro: {LAST_RETRO_DATE}); your output is exactly
one reviewable PR. You may not launch missions or edit the active runtime.

### 1. Gather evidence (aggregate first, drill down second)

Primary mode (requires the Introspection API endpoint binding + read token):

- Judgement trends: POST /v1/metrics — view `judgements`, metric `count` grouped by
  `verdict`, dimensions judge name and runtime version, time-bucketed since
  {LAST_RETRO_DATE}. Compute pass-rate per judge per version.
- Patterns: GET /v1/events?event_name=introspection.pattern scoped to the `jango`
  runtime group, lenses `agent_struggle` and `environment_issue`. For the top
  patterns by distinct-conversation count, pull their observations
  (event_name=introspection.observation&pattern_id=...) and open at most 3
  underlying conversations each — enough to understand, not to relitigate.
- Intervention rate: per docs/OPERATIONS.md, count operator feedback events per
  runtime version and divide by completed missions per version. Report the trend.

Fallback mode (no API binding available): read the stored improvement proposals
(`engineering.get_improvement_proposal` across terminal missions since
{LAST_RETRO_DATE}) and the mission event logs. State explicitly that you ran in
fallback mode and that pattern-level evidence was unavailable.

### 2. Classify

Sort each recurring issue into the controlled-improvement defect classes:
prompt/skill, MCP tool contract, adapter capability, deterministic scorer, judge
rubric, or eval coverage. An issue appearing in one mission only is noted, not acted
on — single-trajectory learning stays forbidden.

### 3. Propose (hard limits)

- At most 3 lesson changes in `memory/LESSONS.md` (add/edit/retire), each in the
  required format with evidence ids.
- At most 1 change outside LESSONS.md, and only in one of: a skill, a judge, an
  adapter manifest, or `evals/`. If the change touches a judge, it must touch
  nothing else.
- Every proposal names its regression fixture in `evals/fixtures/` (existing or
  added in this PR).
- Update the intervention-rate line in the retro log below.

### 4. Output

One PR containing: the diffs, a summary table (issue → defect class → evidence →
change → fixture), the pass-rate and intervention-rate trends, and the exact
`evals` invocation a reviewer must run before merge. Append one dated entry to the
Retro log in this file. Do not merge, promote, or deactivate anything.

---

## Retro log

| date | missions reviewed | mode | lessons ±  | other change | intervention rate |
|------|-------------------|------|-----------|--------------|-------------------|
| —    | —                 | —    | —         | —            | —                 |
