# Holo Computer-Use — De-Risk Findings

**Date:** 2026-07-11 · **Key:** promo (`hk-…`, in gitignored `.env`)

**Verdict: Holo works and grounds real OpenVSP GUI screenshots. Coordinate
convention and calling quirks are pinned down. One real caveat: single-shot
grounding on dense stacked-slider panels is ~1-row imprecise — mitigate with
crop-and-zoom.**

## The API

- OpenAI-compatible, base `https://api.hcompany.ai/v1`, `Authorization: Bearer <key>`.
- Model available on the promo key: **`holo3-1-35b-a3b`** (35B / 3B active, vision+text,
  reasoning+tools). The bigger `holo3-122b-a10b` returns **HTTP 402 Payment Required** — not
  on this key.
- No pip SDK; use `openai` client or raw `POST /v1/chat/completions`. `cu/holo_client.py` is
  a dependency-light client (`localize()` + `ask()`).

## Coordinate convention (VERIFIED)

- Output coords are **normalized 0–1000** (Qwen-VL style), NOT absolute pixels.
  Convert: `x_px = x/1000 * width`, `y_px = y/1000 * height`.
- Origin top-left.

## Calling quirks (each cost time — don't repeat)

1. **It's a reasoning model.** With a plain text prompt it burns tokens "thinking"; the
   answer is in `message.content` (or `message.reasoning` if `content` is null). Give it
   ≥300 `max_tokens` or `finish_reason` = `length` and content = null.
2. **Do NOT send `chat_template_kwargs.enable_thinking=False`** — the server hangs (45s+
   timeout). (Some H Company integrations use it; this endpoint doesn't accept it.)
3. **Do NOT nest `structured_outputs` under `extra_body`** — also hangs. Just ask for JSON in
   the prompt and regex/parse it out. Works, ~3–4s.
4. **Endpoint is intermittently slow / drops connections** → retry with backoff (built into
   `_post`, 3 tries).

## Grounding accuracy (OpenVSP `GUI_Sect.png`, 400×708)

| Target | Predicted px | Verdict |
|--------|-------------|---------|
| "Sect" tab | (245,61) | ✅ dead-on |
| Root Chord value | (360,398) | ✅ dead-on |
| Span value (63.63) | (351,337) | ⚠️ ~1 row low |
| Sweep value (45) | (346,497) | ⚠️ ~1 row low |

- **Column is always correct** (finds the value-field strip on the right).
- **Row can be off by ~30px on tightly stacked sliders.** Tabs/buttons/isolated fields are
  reliable; dense parameter rows are not, single-shot.
- **Recommended mitigation (standard Holo technique):** two-stage crop-and-zoom — localize the
  section (e.g. "Section Planform" header) coarsely, crop to it, re-localize the field in the
  crop, then map coords back. Higher effective resolution → row-accurate.

## Blocker for the live loop

- **Terminal lacks macOS Screen Recording permission** → `screencapture` fails
  ("could not create image from display"). The desktop app (or whatever process drives the CU
  loop) must be granted Screen Recording in System Settings → Privacy before it can screenshot
  OpenVSP live. For de-risk we used the bundled example PNGs instead.

## Implication for architecture

- The 3-tier plan holds: Holo **can** read the OpenVSP GUI and locate controls. For the demo's
  reliability, prefer **crop-and-zoom grounding** on parameter panels, and/or have the
  compiler map discovered parm *names* → the Python API `SetParmVal` calls (which we already
  proved) so execution doesn't depend on pixel-perfect clicks. CU's job = discover *what/where*;
  the compiled recipe = robust execution.

## Files
- `cu/holo_client.py` — `localize(image, desc) -> (x_px, y_px, size)`, `ask(prompt, image)`
- `cu/test_grounding.py` — accuracy harness that overlays predictions on the panel
