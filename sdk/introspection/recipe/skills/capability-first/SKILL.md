---
name: capability-first
description: Convert arbitrary aircraft engineering requests into contracts grounded only in task-local Jango capabilities.
---

# Capability-first planning

Call `jango_capabilities` before interpreting a new objective. Build a catalog keyed by canonical metric and design-variable name. Aliases help interpret user language, but all executable goals must use canonical names.

For every request, produce:

- objective: one measurable canonical metric plus `target`, `minimize`, or `maximize`;
- constraints: canonical metric, operator, value, and unit where declared;
- required disciplines and their supplying specialist roles;
- parameters grouped by catalog-declared domains and bounded by declared minima/maxima;
- baseline source, fan-out, cycle budget, acceptance threshold, and stop conditions;
- missing capability or ambiguity, if any.

The catalog is extensible. A future thermal, acoustic, cost, manufacturing, or higher-fidelity solver tool must be discoverable without rewriting the Chief around one demo prompt.

If no task-local tool exposes the requested metric, stop and name the smallest missing tool contract: required input state, analysis, output metric, units, artifacts, and concurrency mode.
