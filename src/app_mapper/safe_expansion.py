from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ApplicationServices

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.graph import capture_identified_state, persist_edge_pair
from app_mapper.macos.accessibility import (
    application_windows,
    choose_dismiss_action,
    choose_new_window,
    find_menu_item_path,
    perform_action,
    window_fingerprint,
    windows_match,
)
from app_mapper.menu_inventory import SAFE_DIALOG_PATHS, safe_screen_action_keys
from app_mapper.models import (
    EdgeAction,
    EdgeEvidence,
    MenuInventory,
    SafeExpansionCandidate,
    SafeExpansionRecord,
    NodeRecord,
)
from app_mapper.transition import wait_for


class SafeExpansionError(RuntimeError):
    def __init__(self, message: str, *, action_performed: bool = False):
        super().__init__(message)
        self.action_performed = action_performed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def latest_inventory(root: Path) -> Path | None:
    paths = sorted(root.glob("*/inventory.json"), reverse=True)
    return paths[0] if paths else None


def _load_inventory(path: Path) -> MenuInventory:
    return MenuInventory.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _mapped_action_keys(graph_root: Path, registry_root: Path) -> set[str]:
    keys: set[str] = set()
    for path in (graph_root / "edges").glob("edge-*.json"):
        try:
            edge = json.loads(path.read_text(encoding="utf-8"))
            action_key = edge["action"]["action_key"]
            if action_key.startswith("open_screen_"):
                node_path = registry_root / edge["destination_node_id"] / "node.json"
                if not node_path.is_file():
                    continue
                node = json.loads(node_path.read_text(encoding="utf-8"))
                if node.get("state_type") not in {"manager", "dialog"}:
                    continue
            keys.add(action_key)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return keys


def open_safe_screen(
    pid: int,
    path: tuple[str, ...],
    timeout: float,
    before: list[tuple[Any, dict[str, Any]]] | None = None,
) -> tuple[Any, dict[str, Any], list[tuple[Any, dict[str, Any]]]]:
    before = before if before is not None else application_windows(pid)
    target = find_menu_item_path(pid, path)
    if target is None:
        raise SafeExpansionError(f"Exact enabled menu path was not found: {' > '.join(path)}")
    perform_action(target[0], ApplicationServices.kAXPressAction)

    before_fingerprints = {window_fingerprint(summary) for _, summary in before}

    def opened() -> list[tuple[Any, dict[str, Any]]] | None:
        added = [
            window
            for window in application_windows(pid)
            if window_fingerprint(window[1]) not in before_fingerprints
        ]
        return added or None

    new_windows = wait_for(opened, timeout=timeout)
    if new_windows is None:
        raise SafeExpansionError(
            f"No new OpenVSP window appeared after {' > '.join(path)}.",
            action_performed=True,
        )
    dismissible = [window for window in new_windows if choose_dismiss_action(window[0]) is not None]
    if len(dismissible) != 1:
        raise SafeExpansionError(
            f"Expected exactly one close-capable parent for {' > '.join(path)}, found {len(dismissible)}.",
            action_performed=True,
        )
    window = dismissible[0]
    return window[0], window[1], before


def close_safe_screen(
    pid: int,
    window: Any,
    before: list[tuple[Any, dict[str, Any]]],
    timeout: float,
) -> None:
    dismiss = choose_dismiss_action(window)
    if dismiss is None:
        raise SafeExpansionError("Opened window no longer has an allowlisted close action.")
    perform_action(dismiss[0], dismiss[1])
    restored = wait_for(
        lambda: True if windows_match(before, application_windows(pid)) else None,
        timeout=timeout,
    )
    if restored is None:
        raise SafeExpansionError("Timed out waiting for the opened window to close.")


def dismiss_safe_screen_to_node(
    pid: int,
    registry_root: Path,
    destination_node_id: str,
    timeout: float,
) -> None:
    """Dismiss the one window present beyond a recorded destination workspace."""
    node_path = registry_root / destination_node_id / "node.json"
    if not node_path.is_file():
        raise SafeExpansionError(f"Missing destination node metadata: {destination_node_id}")
    node = NodeRecord.model_validate(json.loads(node_path.read_text(encoding="utf-8")))

    def window_key(summary: dict[str, Any]) -> tuple[str, int, int]:
        size = summary.get("AXSize") or summary.get("bounds") or {}
        title = str(summary.get("AXTitle") or summary.get("title") or "").casefold()
        if title.startswith("openvsp "):
            title = "openvsp-main"
        return (
            title,
            round(float(size.get("width", 0))),
            round(float(size.get("height", 0))),
        )

    expected = {
        window_key({"bounds": item.get("bounds", {}), "title": item.get("title", "")})
        for item in node.window_summary.get("representative_geometry", [])
    }
    extras: list[tuple[Any, dict[str, Any]]] = []
    for element, summary in application_windows(pid):
        if window_key(summary) not in expected:
            extras.append((element, summary))
    dismissible = [
        action
        for element, _summary in extras
        if (action := choose_dismiss_action(element)) is not None
    ]
    if len(dismissible) != 1:
        raise SafeExpansionError(
            f"Expected exactly one close-capable extra manager/dialog, found {len(dismissible)}; no action taken."
        )
    dismiss = dismissible[0]
    perform_action(dismiss[0], dismiss[1])

    def destination_restored() -> bool | None:
        actual = {window_key(summary) for _, summary in application_windows(pid)}
        return True if actual == expected else None

    if wait_for(destination_restored, timeout=timeout) is None:
        raise SafeExpansionError("Timed out restoring the recorded workspace window set.")


def _actions(path: tuple[str, ...], window_summary: dict[str, Any]) -> tuple[EdgeAction, EdgeAction]:
    open_key, close_key = safe_screen_action_keys(path)
    label = " > ".join(path)
    return (
        EdgeAction(
            action_key=open_key,
            semantic_description=f"Open {label}",
            accessibility_locator={
                "role": "AXMenuItem",
                "menu_path": list(path),
                "action": "AXPress",
            },
            preconditions=[
                "verified workspace source",
                "exact menu path is enabled",
                "no unrecognized dialog or manager is open",
            ],
            expected_postconditions=[
                "one new OpenVSP window is present",
                "new window has an allowlisted dismissal action",
                "destination node is verified",
            ],
            reverse_action_key=close_key,
        ),
        EdgeAction(
            action_key=close_key,
            semantic_description=f"Close {label} without editing",
            accessibility_locator={
                "window_role": window_summary.get("AXRole"),
                "window_subrole": window_summary.get("AXSubrole"),
                "window_title": window_summary.get("AXTitle"),
                "action": "allowlisted dismissal",
                "menu_path": list(path),
            },
            preconditions=["verified manager/dialog source", "allowlisted close action exists"],
            expected_postconditions=["workspace node is restored"],
            reverse_action_key=open_key,
        ),
    )


def _summary(candidates: list[SafeExpansionCandidate]) -> dict[str, int]:
    return {
        status: sum(candidate.status == status for candidate in candidates)
        for status in ("proposed", "already_mapped", "succeeded", "failed", "skipped")
    }


def expand_safe_frontier(
    pid: int,
    diagnostics: dict[str, Any],
    inventory_path: Path,
    run_root: Path,
    graph_root: Path,
    registry_root: Path,
    *,
    execute: bool,
    max_candidates: int,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[SafeExpansionRecord, Path]:
    inventory = _load_inventory(inventory_path)
    mapped = _mapped_action_keys(graph_root, registry_root)
    candidates: list[SafeExpansionCandidate] = []
    for control in inventory.controls:
        if control.policy_decision != "approved" or tuple(control.path) not in SAFE_DIALOG_PATHS:
            continue
        path = tuple(control.path)
        open_key, _ = safe_screen_action_keys(path)
        already_mapped = open_key in mapped or path == ("OpenVSP", "About vsp")
        candidates.append(
            SafeExpansionCandidate(
                control_id=control.control_id,
                path=control.path,
                status="already_mapped" if already_mapped else "proposed",
            )
        )
    actionable = [candidate for candidate in candidates if candidate.status == "proposed"]
    for candidate in actionable[max_candidates:]:
        candidate.status = "skipped"

    run = create_observation_directory(run_root)
    record = SafeExpansionRecord(
        started_at=_now(),
        mode="execute" if execute else "plan",
        inventory_path=str(inventory_path.resolve()),
        max_candidates=max_candidates,
        candidates=candidates,
        summary=_summary(candidates),
    )
    write_json(run / "expansion.json", record.model_dump(mode="json"))
    if not execute:
        record.completed_at = _now()
        record.success = True
        write_json(run / "expansion.json", record.model_dump(mode="json"))
        return record, run

    source_node_id: str | None = None
    for candidate in actionable[:max_candidates]:
        candidate_root = run / "candidates" / candidate.control_id
        trace: dict[str, Any] = {"events": [], "success": False, "path": candidate.path}

        def event(name: str, **details: Any) -> None:
            trace["events"].append({"event": name, "at": _now(), **details})
            candidate_root.mkdir(parents=True, exist_ok=True)
            write_json(candidate_root / "trace.json", trace)

        opened_window: Any | None = None
        before_windows: list[tuple[Any, dict[str, Any]]] = []
        action_counted = False
        path = tuple(candidate.path)
        try:
            before_id = capture_identified_state(
                pid,
                diagnostics,
                candidate_root / "before",
                registry_root,
                max_depth,
                max_elements,
            )
            if source_node_id is None:
                source_node_id = before_id
            elif before_id != source_node_id:
                raise SafeExpansionError(
                    f"Candidate source {before_id} does not match workspace {source_node_id}."
                )
            candidate.source_node_id = source_node_id
            event("source_verified", node_id=before_id)

            before_windows = application_windows(pid)
            opened_window, window_summary, before_windows = open_safe_screen(
                pid, path, timeout, before_windows
            )
            record.actions_executed += 1
            action_counted = True
            event("screen_opened", window=window_summary)
            destination_id = capture_identified_state(
                pid,
                diagnostics,
                candidate_root / "destination",
                registry_root,
                max_depth,
                max_elements,
                {
                    "name": f"{' > '.join(path)} manager",
                    "description": f"OpenVSP screen opened through {' > '.join(path)}",
                    "state_type": "manager",
                },
            )
            if destination_id == source_node_id:
                raise SafeExpansionError("Opened screen matched the workspace node.")
            candidate.destination_node_id = destination_id
            event("destination_verified", node_id=destination_id)

            close_safe_screen(pid, opened_window, before_windows, timeout)
            opened_window = None
            record.actions_executed += 1
            event("screen_closed")
            returned_id = capture_identified_state(
                pid,
                diagnostics,
                candidate_root / "returned",
                registry_root,
                max_depth,
                max_elements,
            )
            if returned_id != source_node_id:
                raise SafeExpansionError(
                    f"Return reached {returned_id}, expected workspace {source_node_id}."
                )
            candidate.return_verified = True
            forward, reverse = _actions(path, window_summary)
            evidence = EdgeEvidence(
                recorded_at=_now(),
                run_path=str(candidate_root.resolve()),
                source_observation=str((candidate_root / "before").resolve()),
                destination_observation=str((candidate_root / "destination").resolve()),
                returned_observation=str((candidate_root / "returned").resolve()),
            )
            forward_edge, reverse_edge = persist_edge_pair(
                graph_root,
                registry_root,
                source_node_id,
                destination_id,
                forward,
                reverse,
                evidence,
            )
            candidate.edge_ids = [forward_edge.edge_id, reverse_edge.edge_id]
            candidate.status = "succeeded"
            trace["success"] = True
            event("return_verified", node_id=returned_id)
        except Exception as exc:
            if isinstance(exc, SafeExpansionError) and exc.action_performed and not action_counted:
                record.actions_executed += 1
                action_counted = True
            candidate.status = "failed"
            candidate.error = f"{type(exc).__name__}: {exc}"
            event("candidate_failed", error=candidate.error)
            if opened_window is None and before_windows:
                opened_window = choose_new_window(before_windows, application_windows(pid))
            if opened_window is not None:
                try:
                    close_safe_screen(pid, opened_window, before_windows, timeout)
                    record.actions_executed += 1
                    event("recovery_closed_window")
                except Exception as recovery_error:
                    event("recovery_failed", error=str(recovery_error))
                    candidate.trace_path = str((candidate_root / "trace.json").resolve())
                    break
            try:
                recovered_id = capture_identified_state(
                    pid,
                    diagnostics,
                    candidate_root / "recovered",
                    registry_root,
                    max_depth,
                    max_elements,
                )
                if source_node_id is not None and recovered_id != source_node_id:
                    event("recovery_wrong_state", node_id=recovered_id)
                    break
                event("recovery_verified", node_id=recovered_id)
            except Exception as recovery_error:
                event("recovery_verification_failed", error=str(recovery_error))
                break
        candidate.trace_path = str((candidate_root / "trace.json").resolve())
        record.summary = _summary(candidates)
        write_json(run / "expansion.json", record.model_dump(mode="json"))

    record.completed_at = _now()
    record.summary = _summary(candidates)
    record.success = record.summary["failed"] == 0
    write_json(run / "expansion.json", record.model_dump(mode="json"))
    return record, run
