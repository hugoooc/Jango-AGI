"""Render a mission results figure as a styled SVG. Zero dependencies (stdlib only).

Usage:
  python tools/plot_results.py --in evidence.json --out results.svg

Input JSON schema:
{
  "title": "MASS SENSITIVITY - DEMO AIRCRAFT",
  "mission_id": "m-123",
  "metric": "total_mass", "unit": "kg", "direction": "report|min|max",
  "baseline": {"name": "baseline", "value": 447.057},
  "variants": [{"name": "wingspan_p10", "value": 452.63},
               {"name": "tail_p10", "value": 447.467}],
  "series": {"label": "objective over evaluations",          # optional
             "points": [[1, 452.9], [2, 452.71], [3, 452.63]]}
}

The figure presents evidence; it never invents it. Every number must come from
mission events. If a value is missing, omit it - never interpolate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Palette matched to the Jango control room (near-black, acid, cyan).
BG = "#0b0f11"
PANEL = "#10161a"
GRID = "#1d262b"
INK = "#e8f0ef"
MUTED = "#8391 97".replace(" ", "")
ACID = "#c7ff42"
CYAN = "#5ce1e6"
MAGENTA = "#ff5ca8"
FONT = "Menlo, Consolas, monospace"

W, H = 960, 540
PAD = 56


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v: float) -> str:
    return f"{v:,.3f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.3e}"


def render(data: dict) -> str:
    title = data.get("title", "MISSION RESULTS")
    metric = data.get("metric", "metric")
    unit = data.get("unit", "")
    baseline = data.get("baseline") or {}
    variants = list(data.get("variants") or [])
    series = data.get("series") or None
    mission_id = data.get("mission_id", "")

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}">'
    )
    parts.append(
        '<defs><filter id="glow"><feGaussianBlur stdDeviation="3.2" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
    )
    parts.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    # Header
    parts.append(f'<text x="{PAD}" y="40" fill="{ACID}" font-size="17" letter-spacing="3">'
                 f'{_esc(title)}</text>')
    sub = f"{metric}{f' [{unit}]' if unit else ''}"
    if mission_id:
        sub += f"  ::  mission {mission_id}"
    parts.append(f'<text x="{PAD}" y="60" fill="{MUTED}" font-size="11" letter-spacing="2">'
                 f'{_esc(sub.upper())}</text>')

    have_series = bool(series and series.get("points"))
    bars_x0 = PAD
    bars_w = (W // 2 - PAD - 20) if have_series else (W - 2 * PAD)
    top, bottom = 96, H - PAD

    # --- Panel 1: baseline + variant bars (delta vs baseline) ---
    base_v = baseline.get("value")
    rows = ([{"name": baseline.get("name", "baseline"), "value": base_v}] if base_v is not None else []) + variants
    if rows:
        parts.append(f'<rect x="{bars_x0 - 12}" y="{top - 26}" width="{bars_w + 24}" '
                     f'height="{bottom - top + 44}" fill="{PANEL}" stroke="{GRID}"/>')
        parts.append(f'<text x="{bars_x0}" y="{top - 6}" fill="{CYAN}" font-size="10" '
                     f'letter-spacing="2">VARIANTS // DELTA VS BASELINE</text>')
        vmax = max(abs(r["value"]) for r in rows if r.get("value") is not None) or 1.0
        row_h = min(64, (bottom - top - 8) // max(1, len(rows)))
        for i, r in enumerate(rows):
            if r.get("value") is None:
                continue
            y = top + 14 + i * row_h
            frac = max(0.04, abs(r["value"]) / vmax)
            length = int((bars_w - 190) * frac)
            color = MUTED if (base_v is not None and i == 0) else ACID
            parts.append(f'<text x="{bars_x0}" y="{y + 13}" fill="{INK}" font-size="11">'
                         f'{_esc(r["name"])}</text>')
            parts.append(f'<rect x="{bars_x0 + 150}" y="{y}" width="{length}" height="18" '
                         f'fill="{color}" opacity="0.92" filter="url(#glow)"/>')
            label = _fmt(r["value"])
            if base_v is not None and i > 0:
                d = r["value"] - base_v
                label += f'  ({"+" if d >= 0 else ""}{_fmt(d)})'
            parts.append(f'<text x="{bars_x0 + 156 + length}" y="{y + 13}" fill="{MUTED}" '
                         f'font-size="10">{_esc(label)}</text>')

    # --- Panel 2: convergence / evaluation trace ---
    if have_series:
        x0 = W // 2 + 20
        x1, y0, y1 = W - PAD, top, bottom
        parts.append(f'<rect x="{x0 - 12}" y="{y0 - 26}" width="{x1 - x0 + 24}" '
                     f'height="{y1 - y0 + 44}" fill="{PANEL}" stroke="{GRID}"/>')
        parts.append(f'<text x="{x0}" y="{y0 - 6}" fill="{CYAN}" font-size="10" '
                     f'letter-spacing="2">{_esc(series.get("label", "TRACE").upper())}</text>')
        pts = [(float(a), float(b)) for a, b in series["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        xa, xb = min(xs), max(xs) or 1
        ya, yb = min(ys), max(ys)
        if ya == yb:
            ya, yb = ya - 1, yb + 1
        for g in range(5):  # horizontal grid
            gy = y0 + 14 + (y1 - y0 - 28) * g / 4
            parts.append(f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" '
                         f'stroke="{GRID}" stroke-width="1"/>')
        def sx(v): return x0 + (x1 - x0 - 10) * (v - xa) / (xb - xa or 1)
        def sy(v): return y1 - 14 - (y1 - y0 - 28) * (v - ya) / (yb - ya)
        path = " ".join(f'{"M" if i == 0 else "L"}{sx(a):.1f},{sy(b):.1f}'
                        for i, (a, b) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{CYAN}" stroke-width="2.4" '
                     f'filter="url(#glow)"/>')
        for a, b in pts:
            parts.append(f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="3.2" fill="{MAGENTA}"/>')
        parts.append(f'<text x="{x0}" y="{y1 + 16}" fill="{MUTED}" font-size="9">'
                     f'{_esc(_fmt(ya))} — {_esc(_fmt(yb))}</text>')

    parts.append(f'<text x="{W - PAD}" y="{H - 18}" fill="{MUTED}" font-size="9" '
                 f'text-anchor="end" letter-spacing="2">JANGO // EVIDENCE-LINKED RESULTS</text>')
    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    args = ap.parse_args()
    data = json.loads(Path(args.inp).read_text())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render(data))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
