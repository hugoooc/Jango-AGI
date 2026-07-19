# Operations — intervention capture

The single number that says whether the organization is learning: **interventions ÷
completed missions, per runtime version**. It should trend down across versions.

## What counts as an intervention

- A `steer` run injected into an in-progress mission task (you corrected the Chief
  mid-flight).
- A `clear` run issued because the trajectory was unrecoverable.
- Negative operator feedback submitted on a completed conversation.
- A manual fix outside the platform (editing kernel state, killing workers by hand)
  — rarest and worst; log it as feedback on the affected conversation so it lands on
  the event stream.

## Capture protocol

1. Steer freely — never withhold a correction to keep the metric pretty.
2. After any steer/clear/manual fix, submit feedback on the affected conversation
   (dashboard or feedback API): sentiment negative, one sentence naming what the
   Chief should have done unaided. That sentence is retro input.
3. No other bookkeeping. Task runs and feedback are already on the event stream;
   the metrics below do the counting.

## Metrics queries (POST /v1/metrics)

Intervention count per version (feedback-based):

```json
{
  "view": "events",
  "metrics": [{ "agg": "count" }],
  "dimensions": ["runtime_id"],
  "filters": [
    { "field": "event_name", "op": "eq", "value": "introspection.feedback" },
    { "field": "sentiment", "op": "eq", "value": "negative" }
  ],
  "time_dimension": { "granularity": "auto" }
}
```

Completed missions per version (denominator):

```json
{
  "view": "conversations",
  "metrics": [{ "agg": "count" }],
  "dimensions": ["runtime_id"],
  "filters": [{ "field": "status", "op": "eq", "value": "completed" }],
  "time_dimension": { "granularity": "auto" }
}
```

Field names are allow-listed per view — verify against the API reference on first
use and correct here if the allow-list differs.

The retro task computes the ratio, records it in its log, and treats a rising rate
on a new version as promotion-blocking evidence.
