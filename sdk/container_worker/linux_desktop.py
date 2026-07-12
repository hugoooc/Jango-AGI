"""Linux/X11 desktop primitives used inside an isolated worker container.

This module intentionally treats OpenVSP as an opaque GUI.  It only captures
pixels and emits mouse/keyboard events; it never imports or calls OpenVSP's API.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


DISPLAY = os.environ.get("DISPLAY", ":99")
HOLO_BASE = os.environ.get("HCOMPANY_BASE_URL", "https://api.hcompany.ai/v1")
HOLO_MODEL = os.environ.get("HCOMPANY_MODEL", "holo3-1-35b-a3b")
_COORD_RE = re.compile(r'"x"\s*:\s*(-?\d+).*?"y"\s*:\s*(-?\d+)', re.S)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DISPLAY": DISPLAY}
    return subprocess.run(args, check=check, capture_output=True, text=True, env=env)


def screen_size() -> tuple[int, int]:
    out = _run("xdotool", "getdisplaygeometry").stdout.strip()
    width, height = out.split()
    return int(width), int(height)


def screenshot() -> bytes:
    """Return a PNG of the worker's complete virtual desktop."""
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        _run("scrot", "--overwrite", tmp.name)
        return Path(tmp.name).read_bytes()


def click(x: int, y: int, *, double: bool = False) -> None:
    count = "2" if double else "1"
    _run("xdotool", "mousemove", "--sync", str(x), str(y), "click", "--repeat", count, "1")


def type_text(value: str) -> None:
    _run("xdotool", "type", "--clearmodifiers", "--delay", "20", "--", value)


def key(value: str) -> None:
    _run("xdotool", "key", "--clearmodifiers", value)


def focus_openvsp() -> None:
    result = _run("xdotool", "search", "--onlyvisible", "--class", "vsp", check=False)
    ids = [line for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        result = _run("xdotool", "search", "--onlyvisible", "--name", "OpenVSP", check=False)
        ids = [line for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        raise RuntimeError("OpenVSP window is not visible")
    _run("xdotool", "windowactivate", "--sync", ids[-1])


def _api_key() -> str:
    key_value = os.environ.get("HCOMPANY_API_KEY") or os.environ.get("HAI_API_KEY")
    if not key_value:
        raise RuntimeError("HCOMPANY_API_KEY (or HAI_API_KEY) is not configured")
    return key_value


def locate(description: str) -> tuple[int, int]:
    """Ground a textual target against the current screenshot with Holo."""
    png = screenshot()
    image_url = "data:image/png;base64," + base64.b64encode(png).decode()
    prompt = (
        "You are a precise UI localizer for an OpenVSP Linux screenshot. "
        f"Find: {description}. Return ONLY JSON as "
        '{"x": <integer>, "y": <integer>} with coordinates normalized from 0 to 1000.'
    )
    payload = {
        "model": HOLO_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
        "temperature": 0.0,
        "max_tokens": 900,
    }
    request = urllib.request.Request(
        f"{HOLO_BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Holo returned HTTP {exc.code}") from exc
    message = result["choices"][0]["message"]
    blob = (message.get("content") or "") + "\n" + (message.get("reasoning") or "")
    match = _COORD_RE.search(blob)
    if not match:
        raise RuntimeError(f"Holo did not return coordinates: {blob!r}")
    normalized_x, normalized_y = int(match.group(1)), int(match.group(2))
    width, height = screen_size()
    return round(normalized_x / 1000 * width), round(normalized_y / 1000 * height)


def execute(action: dict) -> dict:
    """Execute one JSON action and return observable metadata."""
    kind = action.get("type")
    if kind == "focus":
        focus_openvsp()
        return {"type": kind}
    if kind in {"click", "double_click"}:
        click(int(action["x"]), int(action["y"]), double=kind == "double_click")
        return {"type": kind, "x": int(action["x"]), "y": int(action["y"])}
    if kind == "vision_click":
        x, y = locate(str(action["target"]))
        click(x, y, double=bool(action.get("double")))
        return {"type": kind, "target": action["target"], "x": x, "y": y}
    if kind == "type":
        type_text(str(action.get("text", "")))
        return {"type": kind, "length": len(str(action.get("text", "")))}
    if kind == "key":
        key(str(action["key"]))
        return {"type": kind, "key": action["key"]}
    if kind == "sleep":
        import time
        seconds = min(float(action.get("seconds", 0.5)), 30.0)
        time.sleep(seconds)
        return {"type": kind, "seconds": seconds}
    raise ValueError(f"unsupported action type: {kind!r}")
