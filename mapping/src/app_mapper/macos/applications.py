from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

import Quartz
from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace

from app_mapper.config import TargetApplication


def inspect_bundle(target: TargetApplication) -> dict[str, Any]:
    plist_path = target.bundle_path / "Contents" / "Info.plist"
    result: dict[str, Any] = {
        "name": target.name,
        "bundle_id": target.bundle_id,
        "bundle_path": str(target.bundle_path),
        "installed": target.bundle_path.is_dir(),
        "info_plist_path": str(plist_path),
    }
    if not plist_path.is_file():
        return result

    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)

    result.update(
        {
            "bundle_id": plist.get("CFBundleIdentifier", target.bundle_id),
            "display_name": plist.get("CFBundleDisplayName", target.name),
            "executable": plist.get("CFBundleExecutable"),
            "version": plist.get("CFBundleShortVersionString"),
            "build_version": plist.get("CFBundleVersion"),
            "minimum_macos_version": plist.get("LSMinimumSystemVersion"),
        }
    )
    return result


def find_processes(target: TargetApplication) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for application in NSWorkspace.sharedWorkspace().runningApplications():
        if application.bundleIdentifier() != target.bundle_id:
            continue

        executable_url = application.executableURL()
        bundle_url = application.bundleURL()
        processes.append(
            {
                "pid": int(application.processIdentifier()),
                "localized_name": str(application.localizedName() or ""),
                "bundle_id": str(application.bundleIdentifier()),
                "executable_path": str(executable_url.path()) if executable_url else None,
                "bundle_path": str(bundle_url.path()) if bundle_url else None,
                "active": bool(application.isActive()),
                "hidden": bool(application.isHidden()),
                "terminated": bool(application.isTerminated()),
            }
        )
    return processes


def activate_application(pid: int) -> bool:
    application = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if application is None or application.isTerminated():
        return False
    return bool(application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))


def _bounds_to_dict(bounds: Any) -> dict[str, float]:
    return {
        "x": float(bounds.get("X", 0.0)),
        "y": float(bounds.get("Y", 0.0)),
        "width": float(bounds.get("Width", 0.0)),
        "height": float(bounds.get("Height", 0.0)),
    }


def list_windows(pid: int) -> list[dict[str, Any]]:
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    raw_windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    windows: list[dict[str, Any]] = []
    for raw in raw_windows:
        if int(raw.get(Quartz.kCGWindowOwnerPID, -1)) != pid:
            continue
        bounds = _bounds_to_dict(raw.get(Quartz.kCGWindowBounds, {}))
        windows.append(
            {
                "window_id": int(raw.get(Quartz.kCGWindowNumber, 0)),
                "owner_pid": pid,
                "owner_name": str(raw.get(Quartz.kCGWindowOwnerName, "")),
                "title": str(raw.get(Quartz.kCGWindowName, "")),
                "layer": int(raw.get(Quartz.kCGWindowLayer, 0)),
                "alpha": float(raw.get(Quartz.kCGWindowAlpha, 1.0)),
                "on_screen": bool(raw.get(Quartz.kCGWindowIsOnscreen, True)),
                "memory_bytes": int(raw.get(Quartz.kCGWindowMemoryUsage, 0)),
                "bounds": bounds,
                "area": bounds["width"] * bounds["height"],
            }
        )
    return windows


def choose_primary_window(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        window
        for window in windows
        if window["on_screen"]
        and window["alpha"] > 0
        and window["layer"] == 0
        and window["area"] > 10_000
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda window: (window["area"], bool(window["title"])))


def runtime_context() -> dict[str, Any]:
    return {
        "observer_pid": os.getpid(),
        "platform": "macOS",
    }
