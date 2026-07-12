# NemoClaw-pattern governance layer

This package wraps Otto's GUI-driving orchestrator with the governance
model of **[NVIDIA NemoClaw](https://github.com/NVIDIA/NemoClaw)** — policy
enforcement, operator approval, egress control, and audit logging — so a run
can move "from prototype to safer, governed deployment" (NemoClaw's stated goal)
in environments where privacy, monitoring, and policy controls matter
(aerospace / defense — Otto's target market).

## Why a *pattern*, not the product itself

NemoClaw's core mechanism is **kernel-level container isolation on NVIDIA
OpenShell** (Linux/GPU). Otto's GUI driver must run on the **macOS host**:
it screenshots the display and moves the real mouse/keyboard to operate
OpenVSP.app. A process sandboxed inside an OpenShell Linux container cannot see
or drive a macOS desktop GUI — the sandbox is precisely what would sever the
access the driver needs.

So we do NOT run the GUI loop inside a NemoClaw sandbox. Instead we adopt
NemoClaw's **governance model** around the orchestrator's *decisions*:

| NemoClaw concept | Here |
|---|---|
| Policy-based security, out-of-process enforcement | `policy.py` — every action checked against a policy before it executes |
| Operator approval flow | `approval.py` — destructive/irreversible actions require an operator OK |
| Network egress control | `egress.py` — model/API calls (Holo) checked against an allowlist |
| Audit logging | `audit.py` — append-only JSONL log of every action, verdict, and result |
| Posture / risk profiles | `policy.default.json` — `strict` / `standard` / `permissive` presets |
| Single CLI, guided onboarding | `cli.py` — `status`, `audit`, `policy` commands |

The layer is compatible with a future move into a real OpenShell deployment
(e.g. running the *headless* analysis tier there), but does not require it.

## What it governs

Actions the orchestrator takes, classified by risk:

- **read** (screenshot, read a field/result) — always allowed, logged.
- **navigate** (open a geom, click a tab, open a dialog) — allowed, logged.
- **mutate** (set a geometry parameter) — allowed under policy; may need approval.
- **destructive** (overwrite/save the model, delete geometry, quit with unsaved
  changes) — requires operator approval unless policy says otherwise.
- **egress** (call the Holo vision API) — checked against the host allowlist.

## Usage

```python
from governance import govern, load_policy

gov = govern(load_policy("standard"))          # or "strict" / "permissive"
gov.check("mutate", {"geom": "Wing", "field": "Span", "before": 38.4, "after": 43.0})
# -> raises PolicyDenied, or prompts approval, or returns; either way it is audited
```

The trade-study orchestrator calls `gov.check(...)` before each governed action;
see `governance/integration.py` for the wired-in hooks.

```bash
python -m governance.cli status          # posture, policy summary
python -m governance.cli audit --tail 20 # recent audited actions
python -m governance.cli policy standard # show a posture profile
```
