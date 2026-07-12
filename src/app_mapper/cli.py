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
    DEFAULT_DISCOVERY_ROOT,
    DEFAULT_GRAPH_ROOT,
    DEFAULT_GRAPH_RUN_ROOT,
    DEFAULT_EXPLORATION_ROOT,
    DEFAULT_INTERPRETATION_ARTIFACT_ROOT,
    DEFAULT_NODE_OBSERVATION_ROOT,
    DEFAULT_NODE_REGISTRY_ROOT,
    DEFAULT_REPLAY_ROOT,
    DEFAULT_TRANSITION_ARTIFACT_ROOT,
    OPENVSP,
)
from app_mapper.exploration import create_exploration, load_exploration, run_exploration
from app_mapper.discovery import discover_one_hop, latest_interpretation
from app_mapper.graph import (
    ReplayRefused,
    graph_summary,
    record_about_edges,
    replay_edge,
)
from app_mapper.identity import identify_observation
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
from app_mapper.models import ExplorationBounds
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
    registry_root: Path,
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
        identity = identify_observation(destination, registry_root)
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
    print(f"Node identity: {identity.status} ({identity.node_id or 'review required'})")
    print("Actions executed: 0 (read-only)")
    return 4 if identity.review_required else 0


def _identity_preflight(diagnostics: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not diagnostics["target"]["installed"]:
        errors.append("OpenVSP is not installed.")
    if not diagnostics["running"]:
        errors.append("OpenVSP is not running.")
    if not diagnostics["permissions"]["screen_recording"]["granted"]:
        errors.append("Screen Recording permission is required.")
    if not diagnostics["permissions"]["accessibility"]["granted"]:
        errors.append("Accessibility permission is required.")
    return errors


def run_capture_node(
    artifact_root: Path,
    registry_root: Path,
    max_depth: int,
    max_elements: int,
) -> int:
    destination = create_observation_directory(artifact_root)
    diagnostics = collect_diagnostics()
    write_json(
        destination / "manifest.json",
        {**diagnostics, "identity_capture": True, "actions_executed": 0},
    )
    errors = _identity_preflight(diagnostics)
    if errors:
        write_json(destination / "failure.json", {"errors": errors, "actions_executed": 0})
        print(f"Node capture stopped. Artifacts: {destination}")
        for error in errors:
            print(f"- {error}")
        return 2
    pid = int(diagnostics["processes"][0]["pid"])
    try:
        capture_interpretation_observation(
            pid, destination, max_depth, max_elements
        )
        decision = identify_observation(destination, registry_root)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        write_json(destination / "failure.json", {"errors": [error], "actions_executed": 0})
        print(f"Node capture failed. Artifacts: {destination}")
        print(f"- {error}")
        return 3
    print(f"Node observation: {destination}")
    print(f"Identity decision: {decision.status}")
    print(f"Node ID: {decision.node_id or 'unresolved — inspect identity.json'}")
    if decision.candidates:
        print(f"Best candidate score: {decision.candidates[0].score:.3f}")
    print("Actions executed: 0 (read-only)")
    return 4 if decision.review_required else 0


def run_identify(observation_dir: Path, registry_root: Path) -> int:
    try:
        decision = identify_observation(observation_dir, registry_root)
    except Exception as exc:
        print(f"Identity failed: {type(exc).__name__}: {exc}")
        return 3
    print(f"Identity decision: {decision.status}")
    print(f"Node ID: {decision.node_id or 'unresolved — inspect identity.json'}")
    if decision.candidates:
        print(f"Best candidate score: {decision.candidates[0].score:.3f}")
    return 4 if decision.review_required else 0


def run_graph_record_about(
    graph_root: Path,
    registry_root: Path,
    run_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> int:
    diagnostics = collect_diagnostics()
    errors = _identity_preflight(diagnostics)
    if errors:
        print("Graph recording stopped before interaction.")
        for error in errors:
            print(f"- {error}")
        return 2
    pid = int(diagnostics["processes"][0]["pid"])
    try:
        forward, reverse, run = record_about_edges(
            pid,
            diagnostics,
            graph_root,
            registry_root,
            run_root,
            timeout,
            max_depth,
            max_elements,
        )
    except Exception as exc:
        print(f"Graph recording failed: {type(exc).__name__}: {exc}")
        return 3
    print(f"Graph run artifacts: {run}")
    print(f"Recorded: {forward.edge_id} — {forward.action.semantic_description}")
    print(f"Recorded: {reverse.edge_id} — {reverse.action.semantic_description}")
    print(f"JSON export: {graph_root / 'graph.json'}")
    print(f"GraphML export: {graph_root / 'graph.graphml'}")
    return 0


def run_graph_show(graph_root: Path, registry_root: Path, as_json: bool) -> int:
    graph = graph_summary(graph_root, registry_root)
    if as_json:
        print(graph.model_dump_json(indent=2))
        return 0
    print(f"OpenVSP navigation graph — {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    for node in graph.nodes:
        print(f"- {node.node_id}: {node.semantic_name} [{node.state_type}]")
    for edge in graph.edges:
        attempts = edge.replay.attempts
        rate = edge.replay.successes / attempts if attempts else 0.0
        print(
            f"- {edge.edge_id}: {edge.source_node_id} -> {edge.destination_node_id}"
            f" | {edge.action.semantic_description} | replay {edge.replay.successes}/{attempts}"
            f" ({rate:.0%})"
        )
    return 0


def run_replay(
    edge_id: str,
    graph_root: Path,
    registry_root: Path,
    replay_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> int:
    diagnostics = collect_diagnostics()
    errors = _identity_preflight(diagnostics)
    if errors:
        print("Replay stopped before interaction.")
        for error in errors:
            print(f"- {error}")
        return 2
    pid = int(diagnostics["processes"][0]["pid"])
    try:
        result, run = replay_edge(
            edge_id,
            pid,
            diagnostics,
            graph_root,
            registry_root,
            replay_root,
            timeout,
            max_depth,
            max_elements,
        )
    except ReplayRefused as exc:
        print(f"Replay refused safely: {exc}")
        return 5
    except Exception as exc:
        print(f"Replay failed: {type(exc).__name__}: {exc}")
        return 3
    print(f"Replay artifacts: {run}")
    print(f"Verified destination node: {result['observed_destination_node_id']}")
    return 0


def run_discovery(
    execute: bool,
    interpretation_path: Path | None,
    interpretation_root: Path,
    run_root: Path,
    graph_root: Path,
    registry_root: Path,
    max_candidates: int,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> int:
    selected_interpretation = interpretation_path or latest_interpretation(
        interpretation_root
    )
    diagnostics = collect_diagnostics()
    if execute:
        errors = _identity_preflight(diagnostics)
        if diagnostics["running"] and not list_windows(
            int(diagnostics["processes"][0]["pid"])
        ):
            errors.append(
                "OpenVSP has no visible windows. Reopen its blank workspace before discovery."
            )
        if errors:
            print("Discovery stopped before interaction.")
            for error in errors:
                print(f"- {error}")
            return 2
        pid = int(diagnostics["processes"][0]["pid"])
    else:
        pid = 0
    try:
        discovery, run = discover_one_hop(
            pid,
            diagnostics,
            run_root,
            graph_root,
            registry_root,
            selected_interpretation,
            execute=execute,
            max_candidates=max_candidates,
            timeout=timeout,
            max_depth=max_depth,
            max_elements=max_elements,
        )
    except Exception as exc:
        print(f"Discovery failed: {type(exc).__name__}: {exc}")
        return 3
    print(f"Discovery artifacts: {run}")
    print(f"Mode: {discovery.mode}")
    for candidate in discovery.candidates:
        print(
            f"- {candidate.candidate_id}: {candidate.label}"
            f" | {candidate.policy_decision} | {candidate.status}"
        )
    print(
        "Summary: "
        + ", ".join(f"{key}={value}" for key, value in discovery.summary.items())
    )
    print(f"Actions executed: {discovery.actions_executed}")
    if not execute:
        print("Plan only: review discovery.json, then rerun with --execute.")
    return 0 if discovery.success else 3


def run_bounded_exploration(
    resume: Path | None,
    exploration_root: Path,
    graph_root: Path,
    registry_root: Path,
    max_depth: int,
    max_nodes: int,
    max_actions: int,
    max_seconds: float,
    max_retries: int,
    allow_relaunch: bool,
    pause_after_actions: int | None,
    timeout: float,
    ax_max_depth: int,
    max_elements: int,
) -> int:
    diagnostics = collect_diagnostics()
    errors: list[str] = []
    if not diagnostics["target"]["installed"]:
        errors.append("OpenVSP is not installed.")
    if not diagnostics["permissions"]["screen_recording"]["granted"]:
        errors.append("Screen Recording permission is required.")
    if not diagnostics["permissions"]["accessibility"]["granted"]:
        errors.append("Accessibility permission is required.")

    if resume is not None:
        try:
            state = load_exploration(resume)
        except Exception as exc:
            print(f"Cannot resume exploration: {type(exc).__name__}: {exc}")
            return 3
        if not diagnostics["running"] and not state.bounds.allow_relaunch:
            errors.append("OpenVSP is not running and this run does not allow relaunch.")
    else:
        if not diagnostics["running"]:
            errors.append("OpenVSP is not running.")
        elif not list_windows(int(diagnostics["processes"][0]["pid"])):
            errors.append("OpenVSP has no visible windows.")
        if not errors:
            bounds = ExplorationBounds(
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_actions=max_actions,
                max_seconds=max_seconds,
                max_retries=max_retries,
                allow_relaunch=allow_relaunch,
            )
            state = create_exploration(
                exploration_root,
                graph_root,
                registry_root,
                int(diagnostics["processes"][0]["pid"]),
                bounds,
            )
    if errors:
        print("Exploration stopped before interaction.")
        for error in errors:
            print(f"- {error}")
        return 2

    try:
        state = run_exploration(
            state,
            diagnostics,
            pause_after_actions=pause_after_actions,
            timeout=timeout,
            ax_max_depth=ax_max_depth,
            max_elements=max_elements,
        )
    except Exception as exc:
        print(f"Exploration failed: {type(exc).__name__}: {exc}")
        return 3
    print(f"Exploration run: {state.run_path}")
    print(f"Status: {state.status}")
    print(f"Stop reason: {state.stop_reason}")
    print(
        f"Progress: tasks={state.tasks_completed}/{len(state.queue)}, "
        f"nodes={len(state.discovered_node_ids)}/{state.bounds.max_nodes}, "
        f"actions={state.actions_executed}/{state.bounds.max_actions}, "
        f"elapsed={state.elapsed_seconds:.1f}/{state.bounds.max_seconds:.1f}s"
    )
    print(f"State file: {Path(state.run_path) / 'state.json'}")
    if state.status == "paused":
        print(f"Resume with: uv run python -m app_mapper explore --resume {state.run_path}")
        return 6
    return 3 if state.status == "failed" else 0


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
    interpret_parser.add_argument(
        "--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT
    )

    capture_node_parser = subparsers.add_parser(
        "capture-node",
        help="Capture a read-only state and assign or match its stable node identity.",
    )
    capture_node_parser.add_argument(
        "--artifact-root", type=Path, default=DEFAULT_NODE_OBSERVATION_ROOT
    )
    capture_node_parser.add_argument(
        "--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT
    )
    capture_node_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    capture_node_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)

    identify_parser = subparsers.add_parser(
        "identify", help="Assign node identity to an existing observation directory."
    )
    identify_parser.add_argument("observation_dir", type=Path)
    identify_parser.add_argument(
        "--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT
    )

    graph_parser = subparsers.add_parser("graph", help="Record, inspect, and export graph edges.")
    graph_subparsers = graph_parser.add_subparsers(dest="graph_command", required=True)
    graph_record = graph_subparsers.add_parser(
        "record-about", help="Record workspace -> About -> workspace as two verified edges."
    )
    graph_record.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    graph_record.add_argument("--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT)
    graph_record.add_argument("--run-root", type=Path, default=DEFAULT_GRAPH_RUN_ROOT)
    graph_record.add_argument("--timeout", type=float, default=8.0)
    graph_record.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    graph_record.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)
    graph_show = graph_subparsers.add_parser("show", help="Show and refresh graph exports.")
    graph_show.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    graph_show.add_argument("--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT)
    graph_show.add_argument("--json", action="store_true")

    replay_parser = subparsers.add_parser("replay", help="Replay one recorded safe graph edge.")
    replay_parser.add_argument("edge_id")
    replay_parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    replay_parser.add_argument("--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT)
    replay_parser.add_argument("--replay-root", type=Path, default=DEFAULT_REPLAY_ROOT)
    replay_parser.add_argument("--timeout", type=float, default=8.0)
    replay_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    replay_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)

    discovery_parser = subparsers.add_parser(
        "discover-one-hop",
        help="Plan or execute bounded whitelisted discovery from the workspace.",
    )
    discovery_parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute approved candidates; without this flag the command only writes a review plan.",
    )
    discovery_parser.add_argument("--interpretation", type=Path)
    discovery_parser.add_argument(
        "--interpretation-root", type=Path, default=DEFAULT_INTERPRETATION_ARTIFACT_ROOT
    )
    discovery_parser.add_argument("--run-root", type=Path, default=DEFAULT_DISCOVERY_ROOT)
    discovery_parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    discovery_parser.add_argument(
        "--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT
    )
    discovery_parser.add_argument("--max-candidates", type=int, default=3)
    discovery_parser.add_argument("--timeout", type=float, default=8.0)
    discovery_parser.add_argument("--max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    discovery_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)

    explore_parser = subparsers.add_parser(
        "explore", help="Run or resume bounded safe-navigation exploration."
    )
    explore_parser.add_argument("--app", choices=["OpenVSP"], default="OpenVSP")
    explore_parser.add_argument("--resume", type=Path)
    explore_parser.add_argument("--exploration-root", type=Path, default=DEFAULT_EXPLORATION_ROOT)
    explore_parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    explore_parser.add_argument("--registry-root", type=Path, default=DEFAULT_NODE_REGISTRY_ROOT)
    explore_parser.add_argument("--max-depth", type=int, default=1)
    explore_parser.add_argument("--max-nodes", type=int, default=10)
    explore_parser.add_argument("--max-actions", type=int, default=18)
    explore_parser.add_argument("--max-seconds", type=float, default=180.0)
    explore_parser.add_argument("--max-retries", type=int, default=1)
    explore_parser.add_argument("--risk", choices=["safe_navigation"], default="safe_navigation")
    explore_parser.add_argument("--allow-relaunch", action="store_true")
    explore_parser.add_argument("--pause-after-actions", type=int)
    explore_parser.add_argument("--timeout", type=float, default=8.0)
    explore_parser.add_argument("--ax-max-depth", type=int, default=DEFAULT_AX_MAX_DEPTH)
    explore_parser.add_argument("--max-elements", type=int, default=DEFAULT_AX_MAX_ELEMENTS)
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
            args.registry_root,
        )
    if args.command == "capture-node":
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return run_capture_node(
            args.artifact_root,
            args.registry_root,
            args.max_depth,
            args.max_elements,
        )
    if args.command == "identify":
        return run_identify(args.observation_dir, args.registry_root)
    if args.command == "graph":
        if args.graph_command == "show":
            return run_graph_show(args.graph_root, args.registry_root, args.json)
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        if args.graph_command == "record-about":
            return run_graph_record_about(
                args.graph_root,
                args.registry_root,
                args.run_root,
                args.timeout,
                args.max_depth,
                args.max_elements,
            )
    if args.command == "replay":
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return run_replay(
            args.edge_id,
            args.graph_root,
            args.registry_root,
            args.replay_root,
            args.timeout,
            args.max_depth,
            args.max_elements,
        )
    if args.command == "discover-one-hop":
        if not 1 <= args.max_candidates <= 3:
            parser.error("--max-candidates must be in [1, 3]")
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        if args.max_depth < 0 or args.max_elements < 1:
            parser.error("--max-depth must be non-negative and --max-elements must be positive")
        return run_discovery(
            args.execute,
            args.interpretation,
            args.interpretation_root,
            args.run_root,
            args.graph_root,
            args.registry_root,
            args.max_candidates,
            args.timeout,
            args.max_depth,
            args.max_elements,
        )
    if args.command == "explore":
        if not 1 <= args.max_depth <= 3:
            parser.error("--max-depth must be in [1, 3]")
        if args.max_nodes < 2 or args.max_actions < 1 or args.max_seconds <= 0:
            parser.error("node, action, and time bounds must be positive")
        if not 0 <= args.max_retries <= 5:
            parser.error("--max-retries must be in [0, 5]")
        if args.pause_after_actions is not None and args.pause_after_actions < 1:
            parser.error("--pause-after-actions must be positive")
        if args.timeout <= 0 or args.ax_max_depth < 0 or args.max_elements < 1:
            parser.error("timeout/elements must be positive and AX depth non-negative")
        return run_bounded_exploration(
            args.resume,
            args.exploration_root,
            args.graph_root,
            args.registry_root,
            args.max_depth,
            args.max_nodes,
            args.max_actions,
            args.max_seconds,
            args.max_retries,
            args.allow_relaunch,
            args.pause_after_actions,
            args.timeout,
            args.ax_max_depth,
            args.max_elements,
        )
    parser.error(f"Unknown command: {args.command}")
    return 2
