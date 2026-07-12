from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import ApplicationServices

from app_mapper.artifacts import write_json
from app_mapper.macos.accessibility import (
    application_windows,
    choose_dismiss_action,
    choose_new_window,
    find_menu_item,
    perform_action,
    read_application_tree,
    windows_match,
)
from app_mapper.macos.applications import list_windows
from app_mapper.macos.screenshots import ScreenshotError, capture_window


# OpenVSP 3.51.0 exposes its application name as "vsp" to macOS Accessibility.
# Keep both exact product spellings allowlisted; no fuzzy menu matching is permitted.
ABOUT_MENU_TITLES = ("About OpenVSP", "About vsp")


class TransitionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait_for(
    probe: Callable[[], Any | None], timeout: float, interval: float = 0.2
) -> Any | None:
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def capture_phase(
    destination: Path,
    phase: str,
    pid: int,
    max_depth: int,
    max_elements: int,
) -> dict[str, Any]:
    cg_windows = list_windows(pid)
    ax_tree = read_application_tree(pid, max_depth=max_depth, max_elements=max_elements)
    write_json(destination / f"{phase}-windows.json", {"pid": pid, "windows": cg_windows})
    write_json(destination / f"{phase}-accessibility.json", ax_tree)

    screenshots: list[str] = []
    errors: list[str] = []
    eligible = [
        window
        for window in cg_windows
        if window["on_screen"] and window["alpha"] > 0 and window["layer"] == 0 and window["area"] > 2_500
    ]
    for window in eligible:
        filename = f"{phase}-window-{window['window_id']}.png"
        try:
            capture_window(window["window_id"], destination / filename)
            screenshots.append(filename)
        except ScreenshotError as exc:
            errors.append(str(exc))
    return {
        "phase": phase,
        "window_count": len(cg_windows),
        "visible_window_ids": sorted(window["window_id"] for window in eligible),
        "screenshots": screenshots,
        "errors": errors,
        "complete": bool(screenshots),
    }


def exercise_about(
    pid: int,
    destination: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "transition": "OpenVSP main state -> About OpenVSP -> OpenVSP main state",
        "pid": pid,
        "allowed_open_targets": list(ABOUT_MENU_TITLES),
        "started_at": _now(),
        "events": [],
        "success": False,
    }
    write_json(destination / "trace.json", trace)

    def record(event: str, **details: Any) -> None:
        trace["events"].append({"event": event, "at": _now(), **details})
        write_json(destination / "trace.json", trace)

    source_windows = application_windows(pid)
    if not source_windows:
        raise TransitionError("OpenVSP exposes no Accessibility windows.")
    before_capture = capture_phase(destination, "before", pid, max_depth, max_elements)
    record("source_captured", **before_capture)
    if not before_capture["complete"]:
        raise TransitionError("The source window could not be captured; no action was taken.")

    target = find_menu_item(pid, ABOUT_MENU_TITLES)
    if target is None:
        raise TransitionError("No exact enabled allowlisted OpenVSP About menu item was found; no action was taken.")
    record("target_selected", element=target[1], action="AXPress")
    perform_action(target[0], "AXPress")
    record("open_action_performed")

    def find_new_dialog() -> tuple[Any, dict[str, Any]] | None:
        candidate = choose_new_window(source_windows, application_windows(pid))
        if candidate is None:
            return None
        if candidate[1].get(str(ApplicationServices.kAXSubroleAttribute)) != ApplicationServices.kAXDialogSubrole:
            return None
        return candidate

    dialog = wait_for(find_new_dialog, timeout=timeout)
    if dialog is None:
        record("destination_timeout", timeout_seconds=timeout)
        raise TransitionError("Timed out waiting for a new informational window.")

    destination_capture = capture_phase(destination, "destination", pid, max_depth, max_elements)
    record(
        "destination_detected",
        window=dialog[1],
        **destination_capture,
    )

    dismiss = choose_dismiss_action(dialog[0])
    if dismiss is None:
        raise TransitionError("The new window has no allowlisted close, OK, or Cancel control; it was not acted on.")
    record("dismiss_target_selected", element=dismiss[2], action=dismiss[1])
    perform_action(dismiss[0], dismiss[1])
    record("dismiss_action_performed")

    def source_restored() -> bool | None:
        if not windows_match(source_windows, application_windows(pid)):
            return None
        current_ids = sorted(
            window["window_id"]
            for window in list_windows(pid)
            if window["on_screen"]
            and window["alpha"] > 0
            and window["layer"] == 0
            and window["area"] > 2_500
        )
        return True if current_ids == before_capture["visible_window_ids"] else None

    returned = wait_for(source_restored, timeout=timeout)
    if returned is None:
        record("return_timeout", timeout_seconds=timeout)
        raise TransitionError("The dialog was dismissed, but the original Accessibility window state did not return.")

    after_capture = capture_phase(destination, "after", pid, max_depth, max_elements)
    record("source_restored", **after_capture)
    if not destination_capture["complete"] or not after_capture["complete"]:
        raise TransitionError("The transition returned safely, but one or more required screenshots are missing.")
    trace["success"] = True
    trace["completed_at"] = _now()
    write_json(destination / "trace.json", trace)
    return trace
