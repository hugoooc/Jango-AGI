---
name: evidence-loop
description: Run and review engineering missions using deterministic gates, durable events, and artifact provenance.
---

# Evidence-driven execution

Before launch, record the typed mission contract. Launch exactly one mission for that contract and retain its returned mission id.

Monitor with `engineering.wait_for_mission`; carry `next_after` into later calls. Interpret events as follows:

- `worker.provisioned` proves an isolated execution worker was allocated;
- `agent.started`, `agent.progress`, and `agent.completed` establish actual software work;
- `artifact.transferred` proves sequential state handoff between teams;
- `chief.reviewed` records iteration rationale and feedback;
- `agent.failed` or `mission.failed` must be surfaced and diagnosed;
- `mission.completed` is terminal but does not itself prove feasibility.

At review, compare baseline and winner on the same canonical metrics. Verify every active constraint has a finite observed value and zero violation within tolerance. State backend, worker runtime, candidate id, metric delta, artifacts, failures, and residual uncertainty. Never infer feasibility from prose.
