---
name: controlled-improvement
description: Turn observed agent failures into reviewable recipe or adapter improvements without unsafe production self-mutation.
---

# Controlled self-improvement

Production behavior is immutable and commit-pinned. Improve through an operator loop:

1. After a mission is terminal, call `get_improvement_proposal` and cite its concrete trajectory evidence.
2. Classify the defect: prompt/skill, MCP tool contract, adapter capability, deterministic scorer, judge rubric, or eval coverage.
3. Propose the smallest change and a regression fixture.
4. Run offline validation and representative solver tests.
5. Create a new recipe commit/runtime version.
6. Compare with the incumbent using the same judge version and, when warranted, an Introspection experiment.
7. Promote only a measured winner; retain rollback.

Do not call random variation “learning.” Do not edit the running production recipe, change a judge and behavior in the same comparison, or auto-promote from one anecdotal mission.
Treat `auto_promote: false` as an invariant. A proposal is a review artifact, never permission to mutate the active runtime.
