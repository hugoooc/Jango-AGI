# LegacyPilot — Pitch & Presentation

**Teach once. Operate forever.**
*A computer-use agent that operates the software engineers can't automate — governed for the industries that can't take risks.*

- **Track:** Track 1 — Computer Use (uses H Company's Holo computer-use model)
- **Side challenge:** NVIDIA — H Company models governed through a NemoClaw-pattern layer
- **Repo:** `github.com/RiadEdd/h-exa-2026` (branch `nemoclaw`)

---

## 1. The one-liner

> LegacyPilot is a computer-use agent that **learns to operate specialized desktop
> software by watching its GUI** — no API, no integration, no scripts — and then
> runs real engineering studies on it. We prove it on **OpenVSP**, a NASA-origin
> aircraft design tool, and we wrap every action in **NVIDIA NemoClaw-style
> governance** so it's deployable in aerospace & defense.

---

## 2. The problem (the "why now")

Trillions of dollars of the world's most important engineering — aircraft, reactors,
turbines, medical devices — happens inside **specialized desktop applications that have
no usable API**. Aerospace, defense, and energy run on tools like OpenVSP, CATIA, NASTRAN,
Ansys. They are powerful, decades-deep, and effectively **un-automatable**:

- No REST API, no clean integration, no headless mode you can trust.
- Engineers spend a huge fraction of every project **clicking** — setting parameters,
  running the same analysis, reading numbers off panels, repeating.
- That manual bottleneck caps how many design iterations a team can run. Fewer
  iterations = worse designs, slower time-to-market.

Traditional automation is brittle: hard-coded coordinates, fixed macros, per-app
connectors that break on the next release. **Nobody has cracked "operate any desktop
tool the way an engineer does."**

That's exactly what H Company's computer-use models make possible — and what LegacyPilot
turns into a product.

---

## 3. The solution

LegacyPilot has two tiers, and the split is the whole idea:

**① Discovery — the agent *learns* the GUI (H Company Holo).**
The agent looks at a screenshot and H Company's **Holo** computer-use model grounds every
control — "the Span field," "the Compute button," "the Wing in the tree." It reads the
interface the way an engineer does, with no prior knowledge of the app.

**② Execution — the agent *operates* the GUI, forever.**
Because a workflow is always the same, each control is grounded **once** and baked into a
reusable trajectory. Replays fire with zero vision — fast, deterministic clicks. Reading
result *values* still uses Holo vision (a changing number isn't at a fixed place).

On top of that we built the thing engineers actually want: **trade studies**.
Ask a question in plain terms — *"what's the impact on center of gravity if we increase
the wingspan by 12%?"* — and LegacyPilot drives OpenVSP through its GUI: opens the wing
editor, reads the current span, computes +12%, sets it, runs Mass Properties, reads the
result off the screen, and reports the answer. **All by operating the interface. No API.**

**③ Governance — NemoClaw (the NVIDIA challenge).**
Aerospace and defense can't run an autonomous agent that clicks around unsupervised. So
every action the agent takes passes through a **NemoClaw-pattern governance gate**:
policy enforcement, operator approval for risky mutations, network egress control, and a
complete append-only audit trail. This is what turns a cool demo into something a
regulated engineering org could actually deploy.

---

## 4. Live demo script (1:30)

> **Pre-stage before you present** (do NOT do this on the clock): OpenVSP already
> launched with `boeing777200.vsp3` loaded; terminal ready; `HAI_API_KEY` exported and
> a quick Holo call already warmed. See §8 checklist.

**Beat 1 (0:00–0:20) — the ask.**
"Here's a Boeing 737 in OpenVSP — a real aerospace tool with no API. Watch me ask it an
engineering question in plain language." Type/say:
> *"Trade study on mass & CG: baseline vs +10% wingspan vs +10% tail."*

**Beat 2 (0:20–1:00) — the agent operates the GUI, live.**
Run the governed study. Narrate as it happens:
- "It's opening the wing editor — that's **Holo** finding the control, not a script."
- "Reading the current span off the screen… setting +10%… running Mass Properties…"
- "It's **reading the result off the panel with vision** — no API call."
- When the mutation hits: "Notice it **paused for operator approval** — that's NemoClaw
  governance gating a geometry change." (approve it live)

**Beat 3 (1:00–1:30) — the answer + the trail.**
Show the comparison table:
```
variant                    mass     d mass      X_Cg
baseline                447.057      +0.00     18.68
+10% wingspan            452.63      +5.57     18.73
+10% tail size          447.467      +0.41    18.732
Largest mass impact: +10% wingspan (+5.57)
```
Then: `python -m governance.cli audit --tail` →
"…and every single action is in a signed audit trail. **This is deployable in defense.**"

**Fallback:** if the live run stalls, cut to a 20-second screen recording of the exact
same run (record it beforehand). Never let a flaky GUI kill the story.

---

## 5. Why we win — mapped to the 5 judging criteria (100 pts)

| Criterion (20 ea) | Our claim |
|---|---|
| **Technicality** | Two-tier discover/replay over a live macOS GUI; window-relative coordinate anchoring that survives dialogs moving; FLTK-safe field commits; Holo reasoning-model truncation fix; full policy/approval/egress/audit governance engine. Deep, non-trivial systems work. |
| **Creativity** | Nobody demos computer-use on *aerospace CAD*. "Teach once, operate forever" + a governance layer is a genuinely novel framing — an agent product, not a chatbot. |
| **Usefulness** | Solves a real, expensive bottleneck (engineers clicking through API-less tools). 10× more design iterations with the same headcount. Target market pays for this. |
| **Demo** | A live, plain-language question → the agent visibly operates real software → a real engineering answer + an audit trail. Concrete and legible. |
| **Track / sponsor alignment** | Track 1 to the core: H Company Holo *is* the perception+action engine. Plus the NVIDIA side challenge: H Company models governed through a NemoClaw layer. Two sponsors, one coherent story. |

---

## 6. The NVIDIA challenge — our angle (be precise here)

The challenge: *"Run the H Company models through NemoClaw."* Our honest, defensible
interpretation and what we built:

- **What NemoClaw is:** NVIDIA's stack for running AI agents *more securely* — sandbox
  hardening on OpenShell, network egress policy with operator-approval flow, routed
  inference, and audit logging.
- **The real constraint we hit (and address head-on):** NemoClaw's core is kernel-level
  isolation in an **OpenShell Linux container**. LegacyPilot's GUI driver must run on the
  **macOS host** — it screenshots the display and moves the real mouse to operate
  OpenVSP.app. You *cannot* put the GUI loop inside an OpenShell sandbox: the sandbox is
  exactly what would cut off its access to the desktop.
- **So we adopted NemoClaw's governance model around the agent's decisions and its H
  Company model traffic:** every Holo call is **egress-governed and audited**; every
  geometry mutation is **policy-gated and requires operator approval**; posture profiles
  (`strict`/`standard`/`permissive`) mirror NemoClaw's risk framework; and the full run is
  captured in an append-only audit log. This is the "safer, governed deployment" NemoClaw
  is built for — applied to a computer-use agent that governance-shy autonomy would
  otherwise make unshippable in aerospace/defense.
- **The forward path (say this if asked):** the natural next step is routing Holo
  inference through NemoClaw's **managed inference** and running the orchestration/planner
  tier inside a real OpenShell sandbox while the thin GUI actuator stays on the host — a
  clean split of "governed brain, host-side hands."

We lead with the *judgment* — knowing which part can and cannot be sandboxed, and building
the governance that actually matters — not a checkbox integration.

---

## 7. Architecture (the slide diagram)

```
   plain-language request
            │
   ┌────────▼─────────┐        ┌───────────────────────────┐
   │  Orchestrator    │──────► │  NemoClaw-pattern GOVERNOR │
   │  (trade study)   │ every  │  policy · approval ·       │
   └────────┬─────────┘ action │  egress · audit trail      │
            │           gated  └───────────────────────────┘
   ┌────────▼─────────┐
   │  GUI driver      │   read/set params, run analysis, read results
   │  (bot/, cu/)     │
   └────┬─────────┬───┘
        │         │ every vision call = governed egress
        │         ▼
        │   ┌──────────────────┐
        │   │  H Company HOLO  │  ← perception + grounding (Track 1)
        │   │  holo3-1-35b-a3b │
        │   └──────────────────┘
        ▼
   ┌──────────────────┐
   │  OpenVSP (GUI)   │  ← real aerospace tool, no API used
   └──────────────────┘
```

**Discover once (Holo vision) → replay forever (baked coordinates) → govern everything
(NemoClaw pattern).**

---

## 8. Pre-demo checklist (do this or the demo dies)

- [ ] `export HAI_API_KEY="hk-..."` — **note:** the hackathon SDK uses `HAI_API_KEY`;
      our code reads `HCOMPANY_API_KEY`. Set **both**, or update `.env`. (Highest-risk
      footgun.)
- [ ] OpenVSP pre-launched with `boeing777200.vsp3` loaded (launch is ~40s cold — never
      pay that on the clock).
- [ ] `vspaero` binary present next to the `vsp` GUI binary (only if showing aero; Mass
      Prop doesn't need it).
- [ ] Screen Recording + Accessibility permissions granted to the terminal.
- [ ] One warm-up Holo call done (first call is slower).
- [ ] 20-sec screen recording of a successful run saved as a fallback.
- [ ] Wi-Fi: `Alchemist` / `123456789`; Holo endpoint reachable.
- [ ] Use the **reliable relaunch path** for the demo, or pre-run and show the audit
      trail — do **not** demo the experimental fast path (known FLTK state issue).

---

## 9. Short project description (for submission)

> **LegacyPilot** is a computer-use agent that operates specialized desktop engineering
> software — tools with no API — by learning their GUI with H Company's Holo model and
> replaying the workflow reliably. It runs real engineering trade studies on OpenVSP (a
> NASA-origin aircraft design tool): ask in plain language ("impact on center of gravity
> of a 12% larger wingspan?") and it drives the interface to the answer. Every action is
> wrapped in a NemoClaw-pattern governance layer — policy, operator approval, egress
> control, and a full audit trail — making it deployable in regulated aerospace and
> defense settings. Track 1 (Computer Use) + NVIDIA NemoClaw challenge.

---

## 10. Q&A prep (what judges will probe)

**"Is this just hard-coded coordinates?"**
No. Holo *discovers* each control by vision on first sight; we cache the grounded
coordinate as a **window-relative offset** so it survives the dialog opening anywhere.
Discovery is genuine computer-use; replay is the optimization.

**"Why not just use OpenVSP's Python API?"**
That's the point — the *product* is for the thousands of tools that have **no** API. We
deliberately never use OpenVSP's API as the execution path; we operate the GUI, which is
what generalizes to CATIA, NASTRAN, legacy MES/SCADA, etc. (We keep the API only as an
out-of-band way to *verify* a GUI-produced number.)

**"Is the NemoClaw part real or a wrapper?"**
It's a working governance engine — policy verdicts, operator-approval gate, fail-closed
egress allowlist, append-only audit log — verified live (we can show the audit trail of a
real run). We're explicit that it's NemoClaw's *governance model*, not the OpenShell
container, and we explain exactly why the GUI actuator can't be sandboxed and what the
sandboxed-brain next step looks like. Honesty about the architecture is a feature.

**"What's the hardest technical part?"**
Making GUI operation *reliable*: window-relative anchoring, FLTK-safe field commits (the
app ignores normal Return — needs an AppleScript key event), the Holo reasoning-model
truncation fix (read the `reasoning` field + raise max_tokens), and window discipline so
overlapping dialogs don't misdirect a click.

**"Where does it break / what's next?"**
Honest: reading values needs a vision round-trip (~2–5s) — that's the GUI-only speed
floor. Keeping the app alive across many edits can corrupt FLTK state; our reliable path
relaunches for a clean layout. Next: GUI `File → Revert` for fast clean resets, a
natural-language → trade-study-spec planner, and routing Holo through NemoClaw managed
inference.

---

## 11. Team notes / rules compliance

- H Company models used (Holo `holo3-1-35b-a3b`) — **Track 1 requirement met.**
- Built during the event; open-source libs credited; sponsor APIs used per ToS.
- Uses partner tech for both the core track (H Company) and a side challenge (NVIDIA
  NemoClaw). Gradium voice is an available stretch (see below).

**Stretch if time (Voice / Gradium side challenge):** add STT so an engineer *speaks* the
trade study ("what happens to CG if the wing grows 12%?") and TTS so LegacyPilot *reads
back* the result. That makes the demo a hands-free, spoken engineering assistant — a
second side prize on the same core.
