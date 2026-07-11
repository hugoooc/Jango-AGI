from __future__ import annotations

import subprocess
from pathlib import Path


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

