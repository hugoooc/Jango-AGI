---
name: evidence-loop
description: Run and review engineering missions using deterministic gates, durable events, and artifact provenance.
---

# Evidence-driven execution

Before launch, record the typed study contract. Launch exactly one numerical study for that contract and retain its returned run id.

Run with `jango_run_study`, then use its run id and `jango_read_study` for independent review. Interpret events as follows:

- `DOMAIN_FANOUT` establishes multidisciplinary numerical dispatch;
- `INCUMBENT` proves candidate exploration and records objective/gate progress;
- `VERIFICATION_COMPLETED` records deterministic replay;
- `STUDY_COMPLETED` is terminal but does not itself prove feasibility.

At review, compare baseline and winner on the same canonical metrics. Verify every active constraint has a finite observed value and zero violation within tolerance. State fidelity, candidate id, metric delta, geometry fingerprint, artifacts, failures, and residual uncertainty. Never infer feasibility from prose or agent opinion.
