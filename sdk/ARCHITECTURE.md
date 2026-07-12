# LegacyPilot — System, Architecture & Process

**Teach once. Operate forever.**

This document describes the working system as built: how LegacyPilot drives
OpenVSP **entirely through its GUI** (computer vision + clicks, never the
software's API) to run real engineering analyses and trade studies on an
aircraft model.

> **Core principle: GUI-only, never the API.** The whole premise of LegacyPilot
> is operating specialized software that has *no usable integration*. OpenVSP
> happens to also ship a Python API — we deliberately do **not** use it as the
> execution path. Everything below reads and writes the live GUI. (The API is
> permitted only as an out-of-band way to *verify* a GUI-produced number, never
> to produce it.)

---

## 1. What it does today

Starting from a cold machine, LegacyPilot can, purely by operating the OpenVSP
window:

1. **Launch** OpenVSP with a model loaded (the Boeing 737-800 demo model).
2. **Open** any geometry (wing, horizontal/vertical stabilizer, fuselage) in its
   editor and navigate its tabs.
3. **Read** any geometry parameter off the screen (e.g. current wingspan).
4. **Set** any geometry parameter (e.g. +10 % wingspan, absolute area, ±% chord).
5. **Run** an analysis through its dialog — **Mass Properties** is the reliable
   demo analysis (single *Compute* button, instant, in-process).
6. **Read the results** off the screen with vision (total mass, CG, inertia).
7. **Orchestrate a trade study**: baseline → variant A → variant B → compare,
   and report which change moves the metric most.
8. **Quit** cleanly, leaving nothing running so the next run starts cold.

Verified example (all through the GUI):

```
TRADE STUDY — Mass sensitivity
  variant                    mass     d mass      X_Cg
  baseline                447.057      +0.00     18.68
  +10% wingspan            452.63      +5.57     18.73
  +10% tail size          447.467      +0.41    18.732
  Largest mass impact: +10% wingspan (+5.57)
```

The system is **general**: a trade study is a data spec of
`{geom, tab, field, op, amount}` variants — any combination of geom, parameter,
and operation (`scale` ±%, `delta` +units, `set` absolute) runs through the same
orchestrator with no code change.

---

## 2. The two-tier philosophy

LegacyPilot splits every workflow into two roles:

| Tier | Role | Mechanism | Cost |
|------|------|-----------|------|
| **Discovery** | Learn *what/where* the controls are | Holo computer-use model grounds a screenshot → coordinate | vision, slow (~2-5 s/call) |
| **Execution** | Do the workflow repeatedly | Replay baked coordinates via `cliclick` / AppleScript | instant per click |

**Key insight:** the workflow is always the same, so a control only needs to be
*grounded once* (discovery). After that, the baked coordinate is replayed with
**zero vision** — this is the "record once, replay forever" model.

**The one thing vision cannot be removed from:** *reading result values.* A baked
coordinate tells you **where** the "Total Mass" field is; it cannot tell you it
now reads `453.802`. Reading a changing number is content, not location, so it
requires a vision call every time. This is the fundamental speed floor of a
GUI-only system.

---

## 3. Component architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              user request                    │
                    │  "trade study on mass: +10% span vs +10% tail"│
                    └───────────────────────┬─────────────────────┘
                                             │
                            ┌────────────────▼────────────────┐
                            │   bot/trade_study.py             │  ORCHESTRATION
                            │   baseline → variants → compare  │
                            └───┬───────────┬──────────────┬───┘
                                │           │              │
              ┌─────────────────▼──┐  ┌─────▼──────┐  ┌────▼───────────┐
              │ bot/lifecycle.py   │  │ bot/geom.py│  │ bot/replay.py  │
              │ launch / quit the  │  │ read / set │  │ fire a recorded│
              │ GUI app + model    │  │ geom parms │  │ trajectory     │
              └─────────┬──────────┘  └─────┬──────┘  └────┬───────────┘
                        │                   │              │
                        │            ┌──────▼──────┐  ┌────▼───────────┐
                        │            │bot/windows. │  │bot/trajectory. │
                        │            │py (window   │  │py (record/     │
                        │            │discipline)  │  │replay model)   │
                        │            └──────┬──────┘  └────┬───────────┘
                        │                   │              │
                        └───────────────────▼──────────────▼──────────┐
                                    │   cu/desktop.py                  │  PRIMITIVES
                                    │   screenshot · click · type ·    │
                                    │   menu · Holo locate/ask         │
                                    └──────────────┬───────────────────┘
                                                   │
                          ┌────────────────────────▼─────────────────────┐
                          │  macOS controls          │  Holo (H Company)  │
                          │  screencapture, cliclick, │  holo3-1-35b-a3b   │
                          │  AppleScript/System Events│  vision grounding  │
                          └───────────────────────────┴────────────────────┘
                                                   │
                                          ┌────────▼─────────┐
                                          │   OpenVSP GUI    │
                                          │  (the vsp app)   │
                                          └──────────────────┘
```

### 3.1 Primitive layer — `cu/`

Low-level, no product logic.

- **`cu/desktop.py`** — the desktop control layer for macOS.
  - `screenshot(tag)` → captures the full display (Retina) via `screencapture`.
  - `click / triple_click / type_text / commit_return / cmd_key / key` → actions
    via `cliclick` and AppleScript. **FLTK-specific lessons baked in:** field
    edits need triple-click-to-select then an **AppleScript Return** (key code
    36) to commit — `cliclick`'s Return does *not* register in OpenVSP fields.
  - `click_menu(path)` → menu commands by **name path** via AppleScript (e.g.
    `['Analysis','Aero','VSPAERO...']`) — position-independent, no vision, works
    for nested submenus.
  - `dump_menus()` → the full menu tree (free "API" of every menu command).
  - `locate(image, desc)` → **Holo grounding**: returns the coordinate of a
    described control. Coordinates are normalized 0-1000 (Qwen-VL style), mapped
    to logical points so the Retina factor cancels.
  - `ask(prompt, image)` → **Holo VQA**: general vision read (used to read result
    panels off the screen).
  - `require_frontmost(app)` → **hard safety guard**: raises (issues *no* clicks)
    unless OpenVSP owns the menu bar. Prevents baked coordinates from landing in
    another app.

- **`cu/holo_client.py`** — dependency-light Holo client (OpenAI-compatible,
  `https://api.hcompany.ai/v1`, model `holo3-1-35b-a3b`). `localize()` + `ask()`.

  **Critical fix documented here:** Holo is a *reasoning* model — it emits
  ~200-280 tokens of chain-of-thought *before* the JSON answer. With a small
  token budget the answer gets truncated (empty `content`, `finish_reason=length`
  → "no coords"). Fix: `max_tokens=900` **and** read both `content` and the
  `reasoning` field.

### 3.2 Record / Replay engine — `bot/trajectory.py`, `recorder.py`, `replay.py`

The "teach once, operate forever" machinery.

- **`bot/trajectory.py`** — the shared data model. A *trajectory* is a flat,
  ordered list of steps saved as JSON in `bot/trajectories/`. Step ops:
  `menu`, `click`, `set_field`, `fill_field`, `type`, `key`, `cmd`, `wait`,
  `wait_console`, `read_results`.
  - Coordinate steps store an **offset from a window's top-left corner**
    (`off: [dx,dy]`) plus the window's **size signature** (`anchor: [w,h]`), not
    an absolute screen pixel. This makes replay survive the dialog opening at a
    different position.
  - `{placeholder}` values in `value`/`text` are filled from replay kwargs, so
    one recording parameterizes any value (e.g. any slice count).

- **`bot/recorder.py`** — the **discovery pass**. You author a workflow as
  high-level *intents* (`{"do":"set","find":"the Span field","value":"{span}"}`).
  For each intent that targets a control it screenshots, grounds the control with
  Holo, and **bakes** the window-relative offset. Menu/type/key/wait intents need
  no vision. Starts from a clean baseline (`reset=True`).

- **`bot/replay.py`** — the **execution pass**, zero grounding vision. Loads a
  trajectory and fires each step:
  - `menu` → AppleScript by name (position-independent).
  - coordinate steps → re-read the live window rect (AppleScript), add the baked
    offset, click there.
  - `read_results` → Holo reads a panel off-screen and returns structured JSON
    (this is the *only* vision at replay time).
  - `wait_console` → poll Holo until a completion token appears (for async
    solvers like VSPAERO).
  - **Safety:** re-raises OpenVSP and hard-verifies menu-bar ownership before any
    coordinate action.

### 3.3 Lifecycle — `bot/lifecycle.py`

Repeatable cold-start-to-quit, as required for the demo ("close at the end,
reopen on each demand").

- `launch(model)` → starts `~/Downloads/OpenVSP-3.51.0-MacOS/vsp <model.vsp3>`.
  The GUI binary takes the `.vsp3` as a CLI argument, so **app-open + model-load
  happen in one step** — no File>Open needed. Polls for the windows to appear.
  Cold launch ≈ 40-75 s (Gatekeeper verifies the unsigned 38 MB binary on first
  launch; subsequent launches are faster).
- `quit()` → Cmd-Q, dismiss any "save?" prompt with Escape, `pkill` fallback.
  Leaves nothing running.

### 3.4 Window discipline — `bot/windows.py`

The piece that keeps baked coordinates valid across a live session.

- `list_windows()` / `find_by_size(w,h)` → locate a window by its **size
  signature** (OpenVSP's FLTK dialogs are unnamed, so size is the stable
  identifier). Signatures on this machine: Geom Browser `275×673`, geom editor
  `460×828`, Mass Prop dialog `300×508`, VSPAERO dialog `~1000×828`.
- `to_offset / to_absolute` → convert between screen pixels and window-relative
  offsets.
- `close_by_size` / `raise_by_size` → close or front a specific window. Used to
  **clear analysis dialogs off the Geom Browser tree** and raise the target
  window before clicking, so a baked coordinate can't land on an overlapping
  window.

### 3.5 GUI geometry driver — `bot/geom.py`

The vocabulary a trade study needs, all via the GUI.

- `open_geom(name)` → double-click a geom in the Geom Browser tree (clears
  overlapping dialogs + raises the tree first).
- `goto_tab(tab)` → click a tab in the geom editor (Gen/XForm/Plan/Sect/…).
- `read_field(desc)` → Holo reads one numeric field → float.
- `read_panel(panel, fields)` → Holo reads several labelled values → dict.
- `set_field(desc, value)` → FLTK-safe field edit (triple-click, type, commit).
- Grounded coordinates are cached in `legacypilot/knowledge/gui_anchors.json`
  as window-relative offsets, so repeat runs reuse them with no vision.

**Key discovery:** the horizontal & vertical stabilizers are internally **Wing
geoms**, so they expose the *same* Plan/Span/Area fields as the main wing — one
vocabulary drives wing *and* tail.

### 3.6 Orchestrators — `bot/trade_study.py`, `bot/run_demo.py`

- **`bot/trade_study.py`** — the top-level "compare an output across geometry
  variants" engine.
  - A variant = `{name, geom, tab, field_desc, op, amount, tree_desc?}`.
    `op ∈ {scale, delta, set}`.
  - `study(model, variants)` — the **reliable** path: relaunch a fresh model for
    each variant (guarantees an identical clean window layout). Slow but robust.
  - `study_fast(model, variants)` — the **fast** path: launch once, revert the
    field between variants. Faster, but see §6 (Known limitations).
  - `_report()` prints the comparison table and the largest-impact variant.
  - `VARIANTS_EXAMPLE` = the "+10 % wingspan vs +10 % tail" study.

- **`bot/run_demo.py`** — a single-analysis demo driver (launch → run massprop →
  read → quit).

### 3.7 Trajectories on disk — `bot/trajectories/*.json`

Recorded GUI workflows:
- `massprop.json` / `massprop_fast.json` — Mass Properties (open dialog → set
  slices → Compute → read results). `_fast` trims the conservative waits.
- `vspaero_setup.json` / `vspaero_solve.json` / `vspaero_flow.json` — VSPAERO
  aerodynamic solve trajectories (see §6).

---

## 4. End-to-end process (a trade study run)

```
user: "trade study on mass: +10% wingspan vs +10% tail size"
  │
  ▼
build a variant spec:
  [ {baseline},
    {Wing,  Plan, Span field, scale +0.10},
    {h-stab, Plan, Span field, scale +0.10} ]
  │
  ▼
for the baseline and each variant:
  1. lifecycle.launch(model)                 ← cold, model loaded (relaunch path)
  2. (variant only) geom.open_geom(geom)      ← double-click tree
                    geom.goto_tab("Plan")     ← click tab
                    before = geom.read_field  ← VISION: current value
                    after  = f(before, op, amount)
                    geom.set_field(after)     ← type + commit
                    verify read-back          ← VISION: confirm it stuck
  3. replay("massprop")                        ← menu → set slices → Compute
        └─ read_results                        ← VISION: mass, CG, inertia
  4. record {variant, before, after, mass, cg, ...}
  │
  ▼
_report():  table + "largest mass impact: …"
  │
  ▼
lifecycle.quit()                               ← clean state for next request
```

Every arrow is a GUI action. The three **VISION** points (read current value,
verify set, read results) are the only Holo calls per variant and constitute the
irreducible time cost.

---

## 5. Timing & performance

Measured on this machine (Apple Silicon, macOS 26.3):

| Item | Time | Nature |
|------|------|--------|
| Cold launch (Gatekeeper) | 40-75 s | one-time per session |
| `open_geom` | ~1.5 s | click + settle |
| `goto_tab` | ~2.7 s | click + settle |
| `read_field` (Holo) | ~2-5 s | **vision round-trip** |
| `set_field` | ~2.7 s | clicks + commit |
| Mass Prop compute + read | ~11 s | ~4 s waits + Holo read |
| **One variant (GUI open)** | **~18 s** | after sleep-trimming (was ~31 s) |

**Optimizations applied:** trimmed conservative `wait` sleeps, skip re-focus when
OpenVSP already owns the menu bar, `massprop_fast` trajectory with shorter waits.

**The floor:** each variant needs ~3 vision round-trips (read current, verify
set, read result). At ~2-5 s each over the network, ~15-20 s/variant is the
practical GUI-only floor. You cannot reach "instant" while insisting on
vision-driven GUI operation — the vision calls *are* the cost. The mass
computation itself is < 0.5 s.

---

## 6. Known limitations & honest status

- **`study_fast` (launch-once) is not yet reliable.** Keeping OpenVSP alive and
  editing geometry in-session accumulates **FLTK window-state corruption**:
  after several open/close/edit cycles, cached coordinates drift, field edits
  stop landing, and values get garbled (observed a span field reading `737.0`).
  The **relaunch-per-variant path (`study`) is the trustworthy one** precisely
  because every variant starts from a byte-identical clean window layout. The
  intended fix is a GUI **File → Revert / reload-between-variants** step (fast
  reset, ~2-3 s, without cold-start and without accumulating editor cruft) —
  designed, not yet implemented.

- **VSPAERO (aerodynamics) via GUI is fragile.** It shells out to an external
  `vspaero` solver and uses async, gated *Prepare → Launch → wait-for-Done*
  buttons. Two real gotchas were found and fixed:
  1. The GUI's *Launch Solver* silently fails unless the `vspaero` binary sits
     next to the `vsp` GUI binary — we copy the vendored, de-quarantined solver
     there.
  2. Focus-steal between the gated clicks aborts the run (the safety guard fires).

  For demo reliability, **Mass Properties is preferred** (in-process, single
  button, no async, no external binary). VSPAERO trajectories exist but are
  higher-risk.

- **Slice-field snapping.** Typing into the Mass Prop *Num Slice* slider-field
  snaps to the slider's range (e.g. 30 → 50). Slice count affects mesh
  resolution slightly, not the top-line mass. Record it as slider drags if exact
  slice counts matter.

- **Speed vs the API.** GUI-only will never match the software's own API for
  speed (the API runs the same solver in-process in < 0.5 s). That is accepted:
  the value of LegacyPilot is operating software *without* an API, not matching
  API speed.

---

## 7. Environment & setup facts

- **GUI binary:** `~/Downloads/OpenVSP-3.51.0-MacOS/vsp` (bare executable, not a
  `.app`). Usage: `vsp [model.vsp3]`.
- **Demo model:** `models/boeing777200.vsp3` (a Boeing 737-800; tree = fuselage,
  Wing, engine/fan/PodGeom, horizontal & vertical stabilizer, fixation).
- **Holo:** OpenAI-compatible endpoint `https://api.hcompany.ai/v1`, model
  `holo3-1-35b-a3b`, key in gitignored `.env` as `HCOMPANY_API_KEY`.
- **macOS permissions:** the driving process needs **Screen Recording** (for
  `screencapture`) and **Accessibility** (for `cliclick`/AppleScript control).
- **vspaero solver:** vendored de-quarantined copy in `vendor/openvsp/`; copied
  next to the `vsp` binary so the GUI can find it.

---

## 8. File map

```
bot/
  lifecycle.py      launch/quit the GUI app + model               (89 loc)
  windows.py        window geometry, size-signature, raise/close   (94 loc)
  trajectory.py     record/replay data model                       (78 loc)
  recorder.py       discovery pass — Holo grounds + bakes coords   (151 loc)
  replay.py         execution pass — fire baked coords, read panels (170 loc)
  geom.py           GUI geometry driver (open/tab/read/set)         (150 loc)
  trade_study.py    orchestrator — variants → compare              (193 loc)
  run_demo.py       single-analysis demo driver                    (102 loc)
  trajectories/     recorded GUI workflows (*.json)

cu/
  desktop.py        macOS control + Holo grounding primitives      (308 loc)
  holo_client.py    dependency-light Holo API client               (126 loc)

legacypilot/
  knowledge/
    gui_anchors.json   cached window-relative control coordinates
    menus.json         dumped OpenVSP menu tree
  analyses.py / sim.py  API-based analysis layer — NOT used (violates GUI-only;
                        kept only for out-of-band verification)

models/boeing777200.vsp3   demo aircraft
vendor/openvsp/            vendored, de-quarantined solver binaries
```

---

## 9. Design principles (summary)

1. **GUI-only.** Operate the interface, never the software's API. That is the
   product.
2. **Discover once, replay forever.** Vision grounds a control once; baked
   coordinates replay it with no vision.
3. **Vision is only for reading values.** Locations are baked; changing numbers
   must be read every time — this is the accepted speed floor.
4. **Window-relative coordinates.** Store offsets from a window's corner, not
   absolute pixels, so replay survives dialogs opening anywhere.
5. **Window discipline.** Clear/raise windows before clicking; a baked
   coordinate is only valid against a known layout.
6. **Determinism over speed.** Relaunch guarantees a clean layout; it is slow but
   correct. Fast paths must not trade away correctness silently — verify
   read-backs and fail loudly.
7. **Repeatable lifecycle.** Every request: launch → operate → quit. Nothing left
   running.
