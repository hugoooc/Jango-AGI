from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.config import (
    DEFAULT_ARTIFACT_ROOT,
    DEFAULT_AX_MAX_DEPTH,
    DEFAULT_AX_MAX_ELEMENTS,
    DEFAULT_INTERPRETATION_ARTIFACT_ROOT,
    DEFAULT_TRANSITION_ARTIFACT_ROOT,
    OPENVSP,
)
from app_mapper.holo import (
    HoloConfigurationError,
    HoloSettings,
    build_request,
    request_interpretation,
)
from app_mapper.interpretation import capture_interpretation_observation, validate_and_filter
from app_mapper.macos.accessibility import read_application_tree
from app_mapper.macos.applications import (
    choose_primary_window,
    find_processes,
    inspect_bundle,
    list_windows,
    runtime_context,
)
from app_mapper.macos.permissions import permission_status
from app_mapper.macos.screenshots import ScreenshotError, capture_window
from app_mapper.transition import capture_phase, exercise_about


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_diagnostics() -> dict[str, Any]:
    bundle = inspect_bundle(OPENVSP)
    processes = find_processes(OPENVSP) if bundle["installed"] else []
    permissions = permission_status()
    return {
        "checked_at": _now(),
        "target": bundle,
        "processes": processes,
        "running": bool(processes),
        "permissions": permissions,
        "runtime": {
            **runtime_context(),
            "python_version": platform.python_version(),
            "macos_version": platform.mac_ver()[0],
            "machine": platform.machine(),
        },
        "read_only": True,
    }


def _print_doctor_report(report: dict[str, Any]) -> None:
    target = report["target"]
    permissions = report["permissions"]
    print("OpenVSP Navigation Mapper — read-only diagnostics")
    print(f"Installed:        {'yes' if target['installed'] else 'no'}")
    print(f"Bundle path:      {target['bundle_path']}")
    print(f"Version:          {target.get('version') or 'unknown'}")
    print(f"Running:          {'yes' if report['running'] else 'no'}")
    print(
        "Screen Recording: "
        + ("granted" if permissions["screen_recording"]["granted"] else "not granted")
    )
    print(
        "Accessibility:    "
        + ("granted" if permissions["accessibility"]["granted"] else "not granted")
    )
    print("Mode:             read-only (no launch, focus, mouse, or keyboard actions)")

    if not permissions["screen_recording"]["granted"]:
        print("\nTo capture OpenVSP, grant this terminal Screen & System Audio Recording access:")
        print(f"  {permissions['screen_recording']['settings_location']}")
    if not permissions["accessibility"]["granted"]:
        print("\nTo inspect UI elements, grant this terminal Accessibility access:")
        print(f"  {permissions['accessibility']['settings_location']}")
    if not report["running"]:
        print("\nOpenVSP is not running. Start it manually before using `observe`.")


def doctor(as_json: bool) -> int:
    report = collect_diagnostics()
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_doctor_report(report)
    return 0


def observe(artifact_root: Path, max_depth: int, max_elements: int) -> int:
    destination = create_observation_directory(artifact_root)
    diagnostics = collect_diagnostics()
    write_json(destination / "manifest.json", diagnostics)

    if not diagnostics["target"]["installed"]:
        print(f"Observation stopped: OpenVSP is not installed. Partial artifacts: {destination}")
        return 2
    if not diagnostics["running"]:
        print(f"Observation stopped: OpenVSP is not running. Partial artifacts: {destination}")
        return 2

    pid = int(diagnostics["processes"][0]["pid"])
    windows = list_windows(pid)
    selected_window = choose_primary_window(windows)
    write_json(destination / "windows.json", {"pid": pid, "windows": windows, "selected": selected_window})

    errors: list[str] = []
    if selected_window is None:
        errors.append("No eligible on-screen OpenVSP window was found.")
    elif not diagnostics["permissions"]["screen_recording"]["granted"]:
        errors.append("Screen Recording permission is not granted; screenshot skipped.")
    else:
        try:
            capture_window(selected_window["window_id"], destination / "screenshot.png")
        except ScreenshotError as exc:
            errors.append(str(exc))

    if not diagnostics["permissions"]["accessibility"]["granted"]:
        errors.append("Accessibility permission is not granted; accessibility tree skipped.")
    else:
        try:
            tree = read_application_tree(pid, max_depth=max_depth, max_elements=max_elements)
            write_json(destination / "accessibility.json", tree)
        except Exception as exc:
            errors.append(f"Accessibility inspection failed: {type(exc).__name__}: {exc}")

    summary = {
        "observed_at": _now(),
        "pid": pid,
        "window_count": len(windows),
        "selected_window_id": selected_window["window_id"] if selected_window else None,
        "files": sorted(path.name for path in destination.iterdir()),
        "errors": errors,
        "complete": not errors,
        "read_only": True,
    }
    write_json(destination / "summary.json", summary)

    print(f"Observation artifacts: {destination}")
    print(f"OpenVSP windows found: {len(windows)}")
    print(f"Status: {'complete' if not errors else 'partial'}")
    for error in errors:
        print(f"- {error}")
    return 0 if not errors else 3


def run_about_transition(
    artifact_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> int:
    destination = create_observation_directory(artifact_root)
    diagnostics = collect_diagnostics()
    manifest = {**diagnostics, "read_only": False, "interaction": "about-dialog-only"}
    write_json(destination / "manifest.json", manifest)

    errors: list[str] = []
    if not diagnostics["target"]["installed"]:
        errors.append("OpenVSP is not installed.")
    if not diagnostics["running"]:
        errors.append("OpenVSP is not running.")
    if not diagnostics["permissions"]["screen_recording"]["granted"]:
        errors.append("Screen Recording permission is required.")
    if not diagnostics["permissions"]["accessibility"]["granted"]:
        errors.append("Accessibility permission is required.")

    if errors:
        write_json(destination / "failure.json", {"errors": errors, "action_performed": False})
        print(f"Transition stopped before interaction. Artifacts: {destination}")
        for error in errors:
            print(f"- {error}")
        return 2

    pid = int(diagnostics["processes"][0]["pid"])
    try:
        exercise_about(pid, destination, timeout, max_depth, max_elements)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        trace_path = destination / "trace.json"
        action_performed = False
        if trace_path.is_file():
            try:
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
                action_performed = any(
                    event.get("event") == "open_action_performed"
                    for event in trace.get("events", [])
                )
            except (OSError, json.JSONDecodeError):
                pass
        try:
            failure_state = capture_phase(
                destination, "failure", pid, max_depth, max_elements
            )
        except Exception as capture_exc:
            failure_state = {"errors": [f"Failure-state capture failed: {capture_exc}"]}
        write_json(
            destination / "failure.json",
            {"errors": [error], "action_performed": action_performed, "state": failure_state},
        )
        print(f"Transition failed safely. Artifacts: {destination}")
        print(f"- {error}")
        return 3

    print(f"Transition succeeded. Artifacts: {destination}")
    print("Verified: main state -> About OpenVSP -> main state")
    return 0


def run_holo_interpretation(
    artifact_root: Path,
    model: str | None,
    base_url: str | None,
    timeout: float,
    min_confidence: float,
    max_depth: int,
    max_elements: int,
) -> int:
    destination = create_observation_directory(artifact_root)
    diagnostics = collect_diagnostics()
    try:
        settings = HoloSettings.from_environment(
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
    except HoloConfigurationError as exc:
        write_json(
            destination / "failure.json",
            {"errors": [str(exc)], "external_request_sent": False, "actions_executed": 0},
        )
        print(f"Interpretation stopped before capture. Artifacts: {destination}")
        print(f"- {exc}")
        return 2

    manifest = {
        **diagnostics,
        "provider": settings.public_dict(),
        "read_only": True,
        "actions_executed": 0,
    }
    write_json(destination / "manifest.json", manifest)

    errors: list[str] = []
    if not diagnostics["target"]["installed"]:
        errors.append("OpenVSP is not installed.")
    if not diagnostics["running"]:
        errors.append("OpenVSP is not running.")
    if not diagnostics["permissions"]["screen_recording"]["granted"]:
        errors.append("Screen Recording permission is required.")
    if not diagnostics["permissions"]["accessibility"]["granted"]:
        errors.append("Accessibility permission is required.")
    if errors:
        write_json(
            destination / "failure.json",
            {"errors": errors, "external_request_sent": False, "actions_executed": 0},
        )
        print(f"Interpretation stopped before capture. Artifacts: {destination}")
        for error in errors:
            print(f"- {error}")
        return 2

    pid = int(diagnostics["processes"][0]["pid"])
    request_sent = False
    try:
        screenshot, context = capture_interpretation_observation(
            pid,
            destination,
            max_depth,
            max_elements,
        )
        _, _, request_record = build_request(screenshot, context, settings)
        write_json(destination / "request.json", request_record)
        request_sent = True
        raw, content, _ = request_interpretation(
            settings, screenshot, context
        )
        write_json(destination / "raw-response.json", raw)
        write_json(destination / "raw-content.json", {"content": content})
        validated = validate_and_filter(content, min_confidence=min_confidence)
        write_json(destination / "interpretation.json", validated.model_dump(mode="json"))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        write_json(
            destination / "failure.json",
            {
                "errors": [error],
                "external_request_sent": request_sent,
                "actions_executed": 0,
            },
        )
        print(f"Interpretation failed. Artifacts: {destination}")
        print(f"- {error}")
        return 3

    print(f"Holo interpretation artifacts: {destination}")
    print(f"State: {validated.state.title} ({validated.state.type})")
    print(f"Accepted navigation targets: {len(validated.navigation_targets)}")
    print(f"Rejected by local policy: {len(validated.rejected_targets)}")
    print("Actions executed: 0 (read-only)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app_mapper",
        description="Guarded OpenVSP observation and navigation tools for macOS.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check installation, runtime, and permissions.")
    doctor_parser.add_argument("--json", action="store_true", help="Print diagnostics as JSON.")

    observe_parser = subparsers.add_parser("observe", help="Capture a read-only OpenVSP observation.")
    observe_parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help=f"Observation output root (default: {DEFAULT_ARTIFACT_ROOT}).",
    )
    observe_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    observe_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)

    transition_parser = subparsers.add_parser(
        "exercise-about",
        help="Open and dismiss About OpenVSP, then verify the original state returned.",
    )
    transition_parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_TRANSITION_ARTIFACT_ROOT,
        help=f"Transition output root (default: {DEFAULT_TRANSITION_ARTIFACT_ROOT}).",
    )
    transition_parser.add_argument("--timeout", type=float, default=8.0)
    transition_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    transition_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)

    interpret_parser = subparsers.add_parser(
        "interpret",
        help="Capture OpenVSP and ask Holo for a read-only structured interpretation.",
    )
    interpret_parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_INTERPRETATION_ARTIFACT_ROOT,
        help=f"Interpretation output root (default: {DEFAULT_INTERPRETATION_ARTIFACT_ROOT}).",
    )
    interpret_parser.add_argument("--model", help="Holo model ID (default: HOLO_MODEL or free-tier model).")
    interpret_parser.add_argument("--base-url", help="Models API base URL (default: HOLO_BASE_URL).")
    interpret_parser.add_argument("--timeout", type=float, default=60.0)
    interpret_parser.add_argument("--min-confidence", type=float, default=0.5)
    interpret_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    interpret_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor(as_json=args.json)
    if args.command == "observe":
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return observe(args.artifact_root, args.max_depth, args.max_elements)
    if args.command == "exercise-about":
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return run_about_transition(
            args.artifact_root,
            args.timeout,
            args.max_depth,
            args.max_elements,
        )
    if args.command == "interpret":
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        if not 0 <= args.min_confidence <= 1:
            parser.error("--min-confidence must be in [0, 1]")
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return run_holo_interpretation(
            args.artifact_root,
            args.model,
            args.base_url,
            args.timeout,
            args.min_confidence,
            args.max_depth,
            args.max_elements,
        )
    parser.error(f"Unknown command: {args.command}")
    return 2
