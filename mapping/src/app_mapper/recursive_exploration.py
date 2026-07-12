from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import Quartz

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.graph import capture_identified_state, persist_edge_pair
from app_mapper.macos.applications import list_windows
from app_mapper.models import (
    EdgeAction,
    EdgeEvidence,
    RecursiveCandidate,
    RecursiveExplorationRecord,
)
from app_mapper.safe_expansion import close_safe_screen, open_safe_screen


PARENT_PATH = ("Model", "Variable Presets...")
BASE_HINT = {
    "name": "Model > Variable Presets... manager",
    "description": "OpenVSP screen opened through Model > Variable Presets...",
    "state_type": "manager",
}
TABS = (
    {
        "id": "variable-presets-group-tab",
        "label": "Group",
        "point": (194, 85),
        "bbox": (129, 68, 259, 101),
        "forward": "select_tab_variable_presets_group",
        "reverse": "select_tab_variable_presets_apply_from_group",
    },
    {
        "id": "variable-presets-settings-tab",
        "label": "Settings",
        "point": (344, 85),
        "bbox": (263, 68, 426, 101),
        "forward": "select_tab_variable_presets_settings",
        "reverse": "select_tab_variable_presets_apply_from_settings",
    },
)
APPLY_POINT = (63, 85)


class RecursiveExplorationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tab_hint(label: str) -> dict[str, str]:
    return {
        "name": f"Model > Variable Presets... > {label} tab",
        "description": f"{label} navigation tab in the Variable Presets manager",
        "state_type": "manager",
    }


def curated_tab_actions() -> dict[str, dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for tab in TABS:
        actions[tab["forward"]] = {
            "point": tab["point"],
            "source_hint": BASE_HINT,
            "destination_hint": tab_hint(tab["label"]),
        }
        actions[tab["reverse"]] = {
            "point": APPLY_POINT,
            "source_hint": tab_hint(tab["label"]),
            "destination_hint": BASE_HINT,
        }
    return actions


def _click_normalized(window: dict[str, Any], point: tuple[int, int]) -> None:
    bounds = window["bounds"]
    x = bounds["x"] + bounds["width"] * point[0] / 1_000
    y = bounds["y"] + bounds["height"] * point[1] / 1_000
    for event_type in (
        Quartz.kCGEventMouseMoved,
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventLeftMouseUp,
    ):
        event = Quartz.CGEventCreateMouseEvent(
            None, event_type, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.04)


def _manager_window(pid: int, before_ids: set[int]) -> dict[str, Any]:
    candidates = [
        window
        for window in list_windows(pid)
        if window["window_id"] not in before_ids
        and window["on_screen"]
        and "sub gl" not in window["title"].casefold()
    ]
    if len(candidates) != 1:
        raise RecursiveExplorationError(
            f"Expected one Variable Presets window, found {len(candidates)}."
        )
    return candidates[0]


def current_variable_presets_window(pid: int) -> dict[str, Any]:
    candidates = [
        window
        for window in list_windows(pid)
        if window["on_screen"]
        and window["layer"] != 0
        and "sub gl" not in window["title"].casefold()
        and window["area"] > 20_000
    ]
    if len(candidates) != 1:
        raise RecursiveExplorationError(
            f"Expected one visible Variable Presets manager, found {len(candidates)}."
        )
    return candidates[0]


def click_curated_tab(pid: int, action_key: str) -> dict[str, Any]:
    action = curated_tab_actions().get(action_key)
    if action is None:
        raise RecursiveExplorationError(f"Unknown curated tab action: {action_key}")
    window = current_variable_presets_window(pid)
    _click_normalized(window, action["point"])
    return window


def _action_pair(tab: dict[str, Any]) -> tuple[EdgeAction, EdgeAction]:
    label = tab["label"]
    return (
        EdgeAction(
            action_key=tab["forward"],
            semantic_description=f"Select the {label} tab in Variable Presets",
            mechanism="quartz_coordinate",
            accessibility_locator={},
            visual_locator={
                "window": "Model > Variable Presets...",
                "normalized_bounding_box": list(tab["bbox"]),
                "normalized_click_point": list(tab["point"]),
                "label": label,
            },
            preconditions=["verified Variable Presets Apply-tab source"],
            expected_postconditions=[f"verified Variable Presets {label}-tab destination"],
            reverse_action_key=tab["reverse"],
        ),
        EdgeAction(
            action_key=tab["reverse"],
            semantic_description=f"Return from {label} to the Apply tab in Variable Presets",
            mechanism="quartz_coordinate",
            accessibility_locator={},
            visual_locator={
                "window": "Model > Variable Presets...",
                "normalized_click_point": list(APPLY_POINT),
                "label": "Apply tab",
            },
            preconditions=[f"verified Variable Presets {label}-tab source"],
            expected_postconditions=["verified Variable Presets Apply-tab destination"],
            reverse_action_key=tab["forward"],
        ),
    )


def curated_edge_actions() -> dict[str, EdgeAction]:
    actions: dict[str, EdgeAction] = {}
    for tab in TABS:
        forward, reverse = _action_pair(tab)
        actions[forward.action_key] = forward
        actions[reverse.action_key] = reverse
    return actions


def _mapped_keys(graph_root: Path) -> set[str]:
    keys: set[str] = set()
    for path in (graph_root / "edges").glob("edge-*.json"):
        try:
            keys.add(json.loads(path.read_text(encoding="utf-8"))["action"]["action_key"])
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return keys


def _summary(candidates: list[RecursiveCandidate]) -> dict[str, int]:
    return {
        status: sum(candidate.status == status for candidate in candidates)
        for status in ("proposed", "already_mapped", "succeeded", "failed", "skipped")
    }


def explore_recursive_tabs(
    pid: int,
    diagnostics: dict[str, Any],
    run_root: Path,
    graph_root: Path,
    registry_root: Path,
    *,
    execute: bool,
    max_actions: int,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[RecursiveExplorationRecord, Path]:
    mapped = _mapped_keys(graph_root)
    candidates = [
        RecursiveCandidate(
            candidate_id=tab["id"],
            parent_path=list(PARENT_PATH),
            label=tab["label"],
            depth=2,
            status="already_mapped" if tab["forward"] in mapped else "proposed",
        )
        for tab in TABS
    ]
    actionable = [candidate for candidate in candidates if candidate.status == "proposed"]
    limit = max_actions // 2
    for candidate in actionable[limit:]:
        candidate.status = "skipped"
    run = create_observation_directory(run_root)
    record = RecursiveExplorationRecord(
        started_at=_now(),
        mode="execute" if execute else "plan",
        candidates=candidates,
        summary=_summary(candidates),
    )
    write_json(run / "recursive.json", record.model_dump(mode="json"))
    if not execute:
        record.completed_at = _now()
        record.success = True
        write_json(run / "recursive.json", record.model_dump(mode="json"))
        return record, run

    workspace_before_id = capture_identified_state(
        pid,
        diagnostics,
        run / "workspace-source",
        registry_root,
        max_depth,
        max_elements,
    )
    before_ids = {window["window_id"] for window in list_windows(pid)}
    opened, _summary_window, before_ax = open_safe_screen(pid, PARENT_PATH, timeout)
    record.actions_executed += 1
    manager = _manager_window(pid, before_ids)
    try:
        base_id = capture_identified_state(
            pid,
            diagnostics,
            run / "parent-source",
            registry_root,
            max_depth,
            max_elements,
            BASE_HINT,
            manager["window_id"],
        )
        for tab, candidate in zip(TABS, candidates):
            if candidate.status != "proposed" or record.actions_executed + 2 > max_actions + 1:
                continue
            candidate.source_node_id = base_id
            candidate_root = run / "candidates" / candidate.candidate_id
            try:
                _click_normalized(manager, tab["point"])
                record.actions_executed += 1
                destination_id = capture_identified_state(
                    pid,
                    diagnostics,
                    candidate_root / "destination",
                    registry_root,
                    max_depth,
                    max_elements,
                    tab_hint(tab["label"]),
                    manager["window_id"],
                )
                candidate.destination_node_id = destination_id
                _click_normalized(manager, APPLY_POINT)
                record.actions_executed += 1
                returned_id = capture_identified_state(
                    pid,
                    diagnostics,
                    candidate_root / "returned",
                    registry_root,
                    max_depth,
                    max_elements,
                    BASE_HINT,
                    manager["window_id"],
                )
                if returned_id != base_id:
                    raise RecursiveExplorationError(
                        f"Tab return reached {returned_id}, expected {base_id}."
                    )
                candidate.return_verified = True
                forward, reverse = _action_pair(tab)
                evidence = EdgeEvidence(
                    recorded_at=_now(),
                    run_path=str(candidate_root.resolve()),
                    source_observation=str((run / "parent-source").resolve()),
                    destination_observation=str((candidate_root / "destination").resolve()),
                    returned_observation=str((candidate_root / "returned").resolve()),
                )
                forward_edge, reverse_edge = persist_edge_pair(
                    graph_root,
                    registry_root,
                    base_id,
                    destination_id,
                    forward,
                    reverse,
                    evidence,
                )
                candidate.edge_ids = [forward_edge.edge_id, reverse_edge.edge_id]
                candidate.status = "succeeded"
            except Exception as exc:
                candidate.status = "failed"
                candidate.error = f"{type(exc).__name__}: {exc}"
                _click_normalized(manager, APPLY_POINT)
                record.actions_executed += 1
            record.summary = _summary(candidates)
            write_json(run / "recursive.json", record.model_dump(mode="json"))
    finally:
        close_safe_screen(pid, opened, before_ax, timeout)
        record.actions_executed += 1

    workspace_id = capture_identified_state(
        pid,
        diagnostics,
        run / "workspace-returned",
        registry_root,
        max_depth,
        max_elements,
    )
    if workspace_id != workspace_before_id:
        raise RecursiveExplorationError(
            f"Workspace return reached {workspace_id}, expected {workspace_before_id}."
        )
    record.completed_at = _now()
    record.summary = _summary(candidates)
    record.success = record.summary["failed"] == 0
    write_json(run / "recursive.json", record.model_dump(mode="json"))
    return record, run
