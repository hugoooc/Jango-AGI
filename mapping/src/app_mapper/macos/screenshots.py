from __future__ import annotations

import math
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


class ScreenshotError(RuntimeError):
    pass


def capture_window(window_id: int, destination: Path) -> None:
    completed = subprocess.run(
        ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window_id), str(destination)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not destination.is_file():
        detail = completed.stderr.strip() or completed.stdout.strip() or "no image was created"
        raise ScreenshotError(f"Window capture failed: {detail}")


def capture_region(bounds: dict[str, float], destination: Path) -> None:
    x = math.floor(bounds["x"])
    y = math.floor(bounds["y"])
    width = math.ceil(bounds["width"])
    height = math.ceil(bounds["height"])
    if width < 1 or height < 1:
        raise ScreenshotError("Screen capture region has no area.")
    completed = subprocess.run(
        ["/usr/sbin/screencapture", "-x", f"-R{x},{y},{width},{height}", str(destination)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not destination.is_file():
        detail = completed.stderr.strip() or completed.stdout.strip() or "no image was created"
        raise ScreenshotError(f"Region capture failed: {detail}")


def capture_window_composite(
    windows: list[dict[str, Any]], destination: Path
) -> tuple[dict[str, float], list[str]]:
    if not windows:
        raise ScreenshotError("No windows were supplied for composite capture.")
    captured_by_digest: dict[str, tuple[dict[str, Any], Path]] = {}
    for window in windows:
        path = destination.parent / f"window-{window['window_id']}.png"
        capture_window(window["window_id"], path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = captured_by_digest.get(digest)
        if previous is None or window["area"] > previous[0]["area"]:
            captured_by_digest[digest] = (window, path)
    captured = list(captured_by_digest.values())

    left = min(window["bounds"]["x"] for window, _ in captured)
    top = min(window["bounds"]["y"] for window, _ in captured)
    right = max(
        window["bounds"]["x"] + window["bounds"]["width"] for window, _ in captured
    )
    bottom = max(
        window["bounds"]["y"] + window["bounds"]["height"] for window, _ in captured
    )
    bounds = {"x": left, "y": top, "width": right - left, "height": bottom - top}

    with Image.open(captured[0][1]) as first:
        scale_x = first.width / captured[0][0]["bounds"]["width"]
        scale_y = first.height / captured[0][0]["bounds"]["height"]
    canvas = Image.new(
        "RGBA",
        (math.ceil(bounds["width"] * scale_x), math.ceil(bounds["height"] * scale_y)),
        (30, 30, 30, 255),
    )
    for window, path in reversed(captured):
        with Image.open(path) as source:
            image = source.convert("RGBA")
            expected_size = (
                round(window["bounds"]["width"] * scale_x),
                round(window["bounds"]["height"] * scale_y),
            )
            if image.size != expected_size:
                image = image.resize(expected_size, Image.Resampling.LANCZOS)
            offset = (
                round((window["bounds"]["x"] - left) * scale_x),
                round((window["bounds"]["y"] - top) * scale_y),
            )
            canvas.alpha_composite(image, dest=offset)
    canvas.convert("RGB").save(destination, format="PNG")
    return bounds, [path.name for _, path in captured]
