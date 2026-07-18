---
name: capability-first
description: Convert arbitrary engineering requests into contracts grounded only in connected adapter capabilities.
---

# Capability-first planning

Call `engineering.list_capabilities` before interpreting a new objective. Build a catalog keyed by canonical metric and parameter name. Aliases help interpret user language, but all executable goals must use canonical names.

For every request, produce:

- objective: one measurable canonical metric plus `min` or `max`;
- constraints: canonical metric, operator, value, and unit where declared;
- required analyses and their supplying adapters;
- parameters grouped by adapter-declared domains and bounded by declared minima/maxima;
- baseline source, fan-out, cycle budget, acceptance threshold, and stop conditions;
- missing capability or ambiguity, if any.

The connected catalog is extensible. A thermal, acoustic, cost, manufacturing, or other adapter must work without editing this skill. Do not route by a hardcoded list of aerospace examples.

If no adapter exposes the requested metric, stop and name the smallest missing API contract: required input state, analysis, output metric, units, artifacts, and concurrency mode.
