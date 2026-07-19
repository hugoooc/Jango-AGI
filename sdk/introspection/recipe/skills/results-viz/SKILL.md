---
name: results-viz
description: Render every terminal mission's results as a styled SVG figure written to outputs, from mission evidence only.
---

# Evidence-linked results figures

After a mission reaches a terminal state and you have completed the evidence-loop
review, render one results figure so the human sees the outcome at a glance.

1. Assemble an evidence JSON strictly from mission events and metrics: title,
   mission_id, canonical metric + unit, baseline value, per-variant values, and
   (when iteration events carry an objective trace) a series of
   [evaluation_index, value] points. Every number must be traceable to an event;
   omit anything unverified — never interpolate, never estimate.
2. Run: `python tools/plot_results.py --in <evidence.json> --out outputs/results_<mission_id>.svg`
   Writing under outputs/ persists the figure as a durable artifact on this
   conversation.
3. Reference the artifact in your closing engineering decision, next to the
   metric table it visualizes.

Rules:
- The figure is presentation of evidence, never evidence itself. Deterministic
  gates and event provenance remain authoritative; a plot changes nothing about
  feasibility.
- Blocked or failed missions get no results figure — a blocker report with an
  empty plot is theatre. Skip rendering and say why.
- One figure per mission by default; add a second only when a sequential handoff
  produces two genuinely distinct metric families.
