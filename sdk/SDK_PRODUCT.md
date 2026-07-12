# SDK Product — parallel simulation fleet on `hai-agents`

The `sdk` branch rebuilds LegacyPilot on H Company's official **`hai-agents` SDK**,
adds a **fleet controller** that runs simulation variants **in parallel**, and keeps
the **NemoClaw-pattern governance**. This is the hackathon product.

## What's here

```
sdk_product/
  fleet.py        Fleet controller: fan variants across workers, run concurrently (asyncio)
  vsp_worker.py   OpenVSP desktop worker + custom @tool (FLTK-safe span commit) + variant specs
  run_study.py    entry point: dispatch a mass trade study, aggregate → comparison table
  __init__.py
governance/       NemoClaw-pattern policy/approval/egress/audit (every dispatch is gated)
bot/, cu/         prior hand-rolled stack — its GUI determinism survives as custom tools
```

Run: `python -m sdk_product.run_study`

## The parallelism model (verified against the SDK)

The goal is "launch multiple instances to run parallel simulations, get results faster."
Here is exactly what the SDK allows, tested:

- **Local desktop is one-per-machine.** `Desktop(host="user_device")` drives the
  real OpenVSP on *this* Mac via a local bridge (`hai-agents[desktop]` →
  `PyautoguiDesktopBridge`). But `BridgeManager.ensure()` raises
  **`"cannot serve two local desktop environments from one machine"`** — one screen,
  one mouse, one OpenVSP. So local desktop fan-out on a single laptop is bounded to **1**.
- **Parallelism scales with the worker pool, not the laptop.** `AsyncClient` +
  `async_run_session` + `asyncio.gather` run **N sessions truly concurrently**. Two
  ways to get N:
  1. **N worker machines**, each running one desktop bridge + one OpenVSP (the honest
     way to parallelize the *desktop* app — a small fleet of Macs / VMs).
  2. **Cloud sessions** (browser, or a cloud desktop worker) which have no
     one-per-machine limit.
- The `Fleet` controller expresses both: local-desktop workers are serialized to 1
  (respecting the SDK rule); cloud/remote workers run at pool width. **Same code, the
  parallelism scales with the pool.**

**Proven concurrency (mocked 1s-per-run backend, `sdk_product/fleet.py`):**

| Pool | 6 variants | vs sequential |
|---|---|---|
| 3 cloud workers | **2.0 s** | 6.0 s → **3× faster** |
| 1 local desktop | 6.0 s | serialized (as the SDK requires) |

## What we adopt from the SDK (and why it's better)

- **`Desktop(host="user_device")`** — H's own computer-use harness sees+clicks our GUI.
  Replaces our hand-rolled `cu/desktop.py` screenshot→Holo→cliclick loop and the
  window-offset/coordinate-baking machinery that made `study_fast` brittle.
- **Custom `@tool`s** — our hard-won determinism survives where the model shouldn't
  guess: `set_wing_span_exact` does the FLTK-safe triple-click + AppleScript-Return
  commit (OpenVSP ignores a normal Return). This is the demos' `counterfeit_detection`
  pattern: cloud/agent operates, local tools do the precise machine-side work.
- **`answer_format` (JSON schema)** — the agent returns a validated `MassResult`
  (total_mass, cg_x/y/z, span before/after) instead of us OCR-ing a panel.
- **`max_steps` / `max_time_s`** — a real budget per run.
- Still **GUI-only** — the agent operates the interface; it never calls OpenVSP's API.

## Governance (NemoClaw)

Every fleet dispatch passes `gov.check("navigate", "dispatch_variant", ...)`; geometry
mutations inside a run go through approved tools; the full run is audited. The
`nemoclaw` branch's `governance/` layer is reused unchanged. The strongest NVIDIA
story (from the demos' real integration): run the **orchestrator/planner inside a
NemoClaw OpenShell sandbox**, reach H's hosted platform through the shipped egress
policy, and keep the thin desktop actuator on each worker host.

## HONEST STATUS — the current blocker

The full product code is built, imports/compiles cleanly, and the fleet concurrency
is proven. **What is NOT yet demonstrated end-to-end is a live agent run**, because:

- Our key is the **promo Holo key** (`HCOMPANY_API_KEY`, `hk-...`). It authenticates
  to the platform and lists agents fine, and token quota is healthy (60M, ~17k used).
- But **sessions do not execute**: browser sessions sit in `queued` (steps=0)
  indefinitely, and the desktop bridge's trajectory channel returns **HTTP 429** on
  `/agents/surferh/trajectories`. This is a **capacity / rate limit on this key**, not
  a code bug (the local desktop driver constructs and screenshots fine; the bridge is
  wired correctly).

**To go live, we need the actual hackathon `HAI_API_KEY`** (created at
`platform.hcompany.ai → API Keys`) with real agent-execution capacity, exported as
`HAI_API_KEY`. The product will run unchanged the moment sessions execute — the code
path is complete and validated up to the backend call.

## Next steps to a winning live demo

1. Export the real hackathon `HAI_API_KEY`.
2. `python -m sdk_product.run_study` with OpenVSP launched → one desktop worker runs
   the span sweep, governed, and prints the aggregated table.
3. Add a 2nd/3rd worker machine (or cloud desktop) to `run_study.main()` → the same
   study runs N-wide, visibly faster. That is the money shot.
4. Stand up the NemoClaw sandbox for the orchestrator (NVIDIA prize) using the egress
   policy captured in `SDK_DEEPDIVE.md` / `governance/`.
