# Otto — Technical Summary

**One line:** Otto is a computer-use agent that *learns to operate* desktop
engineering software (with **no API**) by seeing the screen and driving the
mouse/keyboard, then runs many copies in parallel to answer design questions in
seconds.

Proven on **OpenVSP** (NASA-origin aircraft design tool). Perception is **H
Company's Holo** vision model. Parallelism is a **Docker fleet** of isolated GUI
workers (the NVIDIA/NemoClaw track).

---

## 1. The problem it solves

Critical engineering (aerospace, energy, defense) runs on desktop apps with no
usable API — OpenVSP, CATIA, NASTRAN, legacy MES/SCADA. They can't be scripted,
so engineers operate them **by hand**, one design at a time. A trade study
("how does mass change as I grow the wing?") is dozens of manual repetitions.
That manual loop caps how many designs a team can explore.

Otto automates the GUI itself — so it generalizes to any tool, not just ones
with an API — and parallelizes it.

---

## 2. Core idea: discover once, operate forever

Two tiers, and the split is the whole trick:

| Tier | What | Mechanism | Cost |
|------|------|-----------|------|
| **Discovery** | *Learn* where each control is | Holo grounds a screenshot → coordinate | vision, ~4-5s/call |
| **Execution** | *Operate* the workflow, repeatedly | Replay baked coordinates via mouse/keyboard | instant per action |

The workflow is fixed, so a control is grounded **once**, its location cached,
then replayed with **zero vision**. Vision then fires only to *read the result*
(a number that changes every run — content, not location). This is what makes it
both **general** (works on any GUI) and **fast** (after learning).

**Measured impact:** a run went from ~35s (7 vision calls) → ~9s (baked clicks +
1 vision read) — a ~4× per-run speedup, on top of the fleet parallelism.

---

## 3. GUI-only, never the API (the discipline)

Everything reads pixels and emits mouse/keyboard events. It never imports or
calls OpenVSP's Python API — even though OpenVSP has one. That constraint is the
product: the value is operating software that *can't* be integrated. (The API is
only ever used out-of-band to sanity-check a GUI-produced number.)

---

## 4. Architecture

```
   plain-language question ("sweep wingspan 8→20 in 6 points, plot mass")
            │
   ┌────────▼─────────────────────────────────────────────┐
   │  MASTER AGENT  (orchestrator/agent.py + server.py)     │
   │  parse question → plan → decide N workers → dispatch    │
   └───────┬───────────────────────────────────────────────┘
           │ fan out (one design point per worker, in parallel)
   ┌───────▼──────┐  ┌──────────────┐        ┌──────────────┐
   │ Worker 1     │  │ Worker 2     │  ...   │ Worker N     │  Docker containers
   │ Xvfb :99     │  │ Xvfb :99     │        │ Xvfb :99     │  (isolated X11)
   │ OpenVSP GUI  │  │ OpenVSP GUI  │        │ OpenVSP GUI  │
   │ HTTP :8080   │  │ HTTP :8080   │        │ HTTP :8080   │
   │ noVNC :6080  │  │ noVNC :6080  │        │ noVNC :6080  │
   └──────┬───────┘  └──────┬───────┘        └──────┬───────┘
          │ screenshot ↑ / click,type ↓ (xdotool, scrot)
          ▼
   ┌───────────────────┐
   │  H Company HOLO    │  perception: grounds controls + reads results
   │  holo3-1-35b-a3b   │  from screenshots (0–1000 normalized coords)
   └───────────────────┘
          │
   results collected → smooth curve chart in the dashboard
```

---

## 5. Components (what each file does)

### Fleet / parallel path — `orchestrator/` + `container_worker/` (the demo)

- **`container_worker/linux_desktop.py`** — Linux/X11 primitives inside one
  container: `screenshot()` (scrot), `click/type/key` (xdotool), `locate()`
  (Holo grounding), `read_values()` (Holo reads a results panel → JSON),
  `execute(action)` (dispatch one JSON action). Holo calls have retry+jitter for
  parallel rate limits.
- **`container_worker/server.py`** — HTTP control plane per container:
  `POST /actions` (run an action list as a job), `GET /jobs/<id>` (poll),
  `GET /screenshot`, `GET /health`, `POST /locate`.
- **`orchestrator/docker_fleet.py`** — manages workers via the Docker CLI:
  `start_worker`/`ensure_workers`/`stop_all`, port isolation, health polling.
  Trajectories: `wing_span_mass_actions` (vision), `wing_span_mass_actions_baked`
  (fast), `discover_coords` (learn+cache control coords), `mass_from_job`.
- **`orchestrator/agent.py`** — the master agent's planner: NL question →
  `{values, workers}` (ranges, lists, single points; regex, stdlib-only).
- **`orchestrator/server.py`** — dashboard + orchestration API:
  `POST /api/ask` (plan → run), `POST /api/sweep`, `_parallel_mass_sweep`
  (fan out with threads, collect, compute honest speedup), worker mgmt,
  and voice endpoints (Gradium). Serves `index.html`.
- **`orchestrator/index.html`** — single-page dashboard: ask box + chips, live
  worker desktops (noVNC), smooth **spline curve** (Catmull-Rom + gradient fill),
  metric toggle (Mass / Roll inertia / Yaw inertia / CG), speedup banner, voice.

### Local single-machine path — `bot/` + `cu/` (origin of the approach)

- **`cu/desktop.py`** — macOS control (screencapture, cliclick, AppleScript) +
  Holo grounding. Fixed the Holo reasoning-model truncation (read `reasoning`
  field + raise max_tokens).
- **`cu/holo_client.py`** — dependency-light Holo client.
- **`bot/lifecycle.py`** — launch/quit the local OpenVSP GUI (pgrep-gated, not
  AppleScript, to avoid a ~30s accessibility-handshake stall).
- **`bot/geom.py`, `windows.py`, `recorder.py`, `replay.py`, `fastquery.py`** —
  record-once/replay, window-relative coordinate anchoring, FLTK-safe field
  commits, the sub-15s single-query path.

### Reasoning layer — `engine/`

- **`registry.py`** — vocabulary: INPUTS (wing/tail span, chord, area…) ×
  OUTPUTS (mass, CG, inertia, wetted area, volume, L/D), tagged instant vs slow.
- **`parser.py`** — NL → structured request.
- **`dispatcher.py`** — runs only the needed analyses; before/after/delta;
  relaunch-per-measurement for correctness; baseline caching.
- **`executor.py`** — the GUI actuator (open geom, tab, set+verify, read panel).

---

## 6. How a request flows (the demo path)

1. User asks in plain English (typed or **voice** via Gradium STT).
2. `agent.plan()` parses → e.g. 6 span values → 6 workers.
3. `_parallel_mass_sweep` recreates 6 fresh containers (each = pristine 737).
4. **Discovery once**: Holo grounds the 6 controls on worker 1, caches coords.
5. **Fan out**: 6 threads each POST a *baked* action list to its worker; workers
   set span → open Mass Prop → Compute → `vision_read` the mass/CG/inertia.
6. Wait for all, collect points, compute speedup (sum of worker times ÷ wall).
7. Dashboard renders the smooth curve; metric toggle re-plots Ixx etc.

---

## 7. Performance (measured)

| Metric | Value |
|---|---|
| Container start → healthy | ~7s |
| Vision-grounded run (7 Holo calls) | ~35s |
| **Baked run (1 Holo call)** | **~9s** |
| 6-point parallel sweep (wall-clock) | ~21s |
| Sequential estimate (sum of runs) | ~63s |
| **Speedup** | **~3× end-to-end** (≈6× on pure compute; the delta is one-time spin-up + discovery shared across the batch) |

---

## 8. Sponsor alignment

- **H Company (core track):** Holo `holo3-1-35b-a3b` is the perception engine —
  grounds every control and reads every result from pixels. The product does not
  exist without it.
- **NVIDIA / NemoClaw:** each worker is an isolated, containerized agent on its
  own virtual desktop — the sandboxed, governable model for running agents in
  regulated settings. The fleet is the parallel realization of that.
- **Gradium (voice):** speak the question (STT) — wired into the same ask box.

---

## 9. Honest limitations (say these before a judge finds them)

- **Rate limit:** the shared Holo key throttles under heavy concurrent vision;
  baking (1 call/run) keeps ~6 workers safe. Retry+jitter absorbs occasional
  429s. A production key removes the ceiling.
- **Speedup is ~3× end-to-end, ~6× on compute** — the gap is one-time container
  spin-up + the discovery pass, amortized across the batch. Warming the fleet
  first pushes the visible number toward 6×.
- **Fresh containers per sweep** — reusing a worker keeps its prior geometry and
  corrupts the curve, so each sweep recreates them (costs the ~7s start).
- **Coordinate baking assumes a stable layout** — true on the fixed 1440×900
  container desktop; a moved/resized window would require re-discovery (the
  `desc` is kept so any single control can be re-grounded on demand).
- **AMD64 emulation on Apple Silicon** — first image build is slow; runtime is
  fine.

---

## 10. Q&A cheat-sheet

- **"Is it hard-coded coordinates?"** — No. Discovery grounds every control by
  vision with zero prior knowledge; caching is the *optimization* after learning.
  Point it at a new tool and it re-learns.
- **"Why not the OpenVSP API?"** — The product is for the thousands of tools with
  *no* API. Driving the GUI generalizes; the API doesn't.
- **"What does Holo do?"** — It's the eyes: grounds controls and reads results
  from screenshots. Everything else is orchestration and actuation.
- **"How is this parallel if OpenVSP is one app?"** — Each worker is its own
  container with its own X11 display and OpenVSP process — truly independent, so
  N run at once on one machine (and scale to a cluster).
- **"Does it ever cheat with the API?"** — No. Pixels in, mouse/keyboard out.
  Verifiable: workers only have xdotool/scrot + Holo, not the openvsp module.

---

## 11. Repo map & how to run

```
sdk/
  orchestrator/   master agent + fleet + dashboard   → python -m orchestrator.server  (http://localhost:8765)
  container_worker/  per-container GUI control plane  (baked into the Docker image)
  docker/         Dockerfile.worker + entrypoint      → docker build ... -t legacypilot-openvsp-worker:dev
  engine/         NL→analysis reasoning (local path)
  bot/ , cu/      local single-machine GUI driver + Holo primitives
  models/         boeing777200.vsp3 (the 737 demo model)
```

Run the fleet demo:
```bash
cd sdk
docker build --platform linux/amd64 -f docker/Dockerfile.worker -t legacypilot-openvsp-worker:dev .
cp .env.example .env    # add HCOMPANY_API_KEY (hk-...)
../.venv/bin/python -m orchestrator.server   # → http://localhost:8765
```

Branches: `main` · `agent/containerized-openvsp-gui-fleet` (the fleet demo) ·
`multianalysis` (local sweeps) · `nemoclaw` (governance) · `codex/voice-web`
(Gradium voice) · `sdk`, `hugo`, `testagentdemo`.
