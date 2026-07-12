from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from AppKit import NSRunningApplication, NSWorkspace

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.config import OPENVSP
from app_mapper.discovery import _close_menu, _open_menu
from app_mapper.graph import (
    capture_identified_state,
    dismiss_dialog,
    open_about,
    persist_edge_pair,
)
from app_mapper.macos.applications import find_processes, list_windows
from app_mapper.models import (
    EdgeAction,
    EdgeEvidence,
    ExplorationBounds,
    ExplorationState,
    ExplorationTask,
)


AUTONOMOUS_MENU_TITLES = (
    "OpenVSP",
    "File",
    "Edit",
    "Window",
    "View",
    "Model",
    "Analysis",
    "Help",
)


class ExplorationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frontmost_bundle_id() -> str | None:
    application = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(application.bundleIdentifier()) if application and application.bundleIdentifier() else None


def _tasks() -> list[ExplorationTask]:
    tasks = [
        ExplorationTask(
            task_id=f"menu-{title.casefold()}",
            depth=1,
            action_type="menu",
            target=title,
        )
        for title in AUTONOMOUS_MENU_TITLES
    ]
    tasks.append(
        ExplorationTask(
            task_id="dialog-about",
            depth=1,
            action_type="about",
            target="About vsp",
        )
    )
    return tasks


def _state_path(state: ExplorationState) -> Path:
    return Path(state.run_path) / "state.json"


def _persist(state: ExplorationState) -> None:
    state.updated_at = _now()
    write_json(_state_path(state), state.model_dump(mode="json"))


def _read_state(run: Path) -> ExplorationState:
    import json

    return ExplorationState.model_validate(
        json.loads((run / "state.json").read_text(encoding="utf-8"))
    )


def _ensure_runtime(state: ExplorationState) -> int:
    processes = find_processes(OPENVSP)
    current = next((process for process in processes if process["pid"] == state.target_pid), None)
    if current is not None and list_windows(state.target_pid):
        return state.target_pid
    if not state.bounds.allow_relaunch:
        raise ExplorationError(
            "OpenVSP process identity or visible windows changed; relaunch is disabled."
        )

    for process in processes:
        application = NSRunningApplication.runningApplicationWithProcessIdentifier_(process["pid"])
        if application is not None:
            application.terminate()
    deadline = time.monotonic() + 10
    while find_processes(OPENVSP) and time.monotonic() < deadline:
        time.sleep(0.2)
    subprocess.run(["/usr/bin/open", "-a", str(OPENVSP.bundle_path)], check=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        processes = find_processes(OPENVSP)
        visible = next(
            (process for process in processes if list_windows(process["pid"])), None
        )
        if visible is not None:
            state.target_pid = int(visible["pid"])
            state.focus_guard_bundle_id = _frontmost_bundle_id()
            _persist(state)
            return state.target_pid
        time.sleep(0.2)
    raise ExplorationError("Opt-in OpenVSP relaunch did not restore a visible workspace.")


def _check_focus(state: ExplorationState) -> None:
    current = _frontmost_bundle_id()
    allowed = {state.focus_guard_bundle_id, OPENVSP.bundle_id}
    if current not in allowed:
        raise ExplorationError(
            f"Frontmost application changed from the guarded context to {current!r}."
        )


def create_exploration(
    exploration_root: Path,
    graph_root: Path,
    registry_root: Path,
    pid: int,
    bounds: ExplorationBounds,
) -> ExplorationState:
    run = create_observation_directory(exploration_root).resolve()
    state = ExplorationState(
        run_id=run.name,
        run_path=str(run),
        status="paused",
        started_at=_now(),
        updated_at=_now(),
        bounds=bounds,
        graph_root=str(graph_root.resolve()),
        registry_root=str(registry_root.resolve()),
        target_pid=pid,
        focus_guard_bundle_id=_frontmost_bundle_id(),
        queue=_tasks(),
    )
    _persist(state)
    return state


def load_exploration(run: Path) -> ExplorationState:
    return _read_state(run.resolve())


def _action_metadata(task: ExplorationTask) -> tuple[EdgeAction, EdgeAction]:
    if task.action_type == "menu":
        slug = task.target.casefold()
        return (
            EdgeAction(
                action_key=f"open_menu_{slug}",
                semantic_description=f"Open the {task.target} choices menu",
                accessibility_locator={
                    "role": "AXMenuBarItem",
                    "title": task.target,
                    "action": "AXPress",
                },
                preconditions=["verified workspace source", "no menu or dialog open"],
                expected_postconditions=[f"{task.target} menu selected", "destination node verified"],
                reverse_action_key=f"dismiss_menu_{slug}",
            ),
            EdgeAction(
                action_key=f"dismiss_menu_{slug}",
                semantic_description=f"Dismiss the {task.target} choices menu without selection",
                accessibility_locator={
                    "role": "AXMenuBarItem",
                    "title": task.target,
                    "action": "AXCancel",
                },
                preconditions=[f"{task.target} menu selected"],
                expected_postconditions=["workspace source node restored"],
                reverse_action_key=f"open_menu_{slug}",
            ),
        )
    return (
        EdgeAction(
            action_key="open_about",
            semantic_description="Open the informational About vsp dialog",
            accessibility_locator={"role": "AXMenuItem", "titles": ["About OpenVSP", "About vsp"]},
            preconditions=["verified workspace source", "no menu or dialog open"],
            expected_postconditions=["About dialog node verified"],
            reverse_action_key="dismiss_about",
        ),
        EdgeAction(
            action_key="dismiss_about",
            semantic_description="Dismiss the informational About vsp dialog",
            accessibility_locator={"window_subrole": "AXDialog", "action": "allowlisted dismissal"},
            preconditions=["verified About dialog source"],
            expected_postconditions=["workspace source node restored"],
            reverse_action_key="open_about",
        ),
    )


def _open(task: ExplorationTask, pid: int, timeout: float) -> None:
    if task.action_type == "menu":
        _open_menu(pid, task.target, timeout)
    else:
        open_about(pid, timeout)


def _close(task: ExplorationTask, pid: int, timeout: float) -> None:
    if task.action_type == "menu":
        _close_menu(pid, task.target, timeout)
    else:
        dismiss_dialog(pid, timeout)


def run_exploration(
    state: ExplorationState,
    diagnostics: dict[str, Any],
    *,
    pause_after_actions: int | None,
    timeout: float,
    ax_max_depth: int,
    max_elements: int,
) -> ExplorationState:
    invocation_started = time.monotonic()
    starting_actions = state.actions_executed
    state.status = "running"
    state.stop_reason = None
    _persist(state)
    try:
        pid = _ensure_runtime(state)
    except Exception as exc:
        state.status = "failed"
        state.stop_reason = f"runtime guard: {type(exc).__name__}: {exc}"
        state.elapsed_seconds += time.monotonic() - invocation_started
        state.completed_at = _now()
        _persist(state)
        return state

    if state.source_node_id is None:
        source = Path(state.run_path) / "bootstrap"
        try:
            state.source_node_id = capture_identified_state(
                pid,
                diagnostics,
                source,
                Path(state.registry_root),
                ax_max_depth,
                max_elements,
            )
        except Exception as exc:
            state.status = "failed"
            state.stop_reason = f"source bootstrap: {type(exc).__name__}: {exc}"
            state.elapsed_seconds += time.monotonic() - invocation_started
            state.completed_at = _now()
            _persist(state)
            return state
        state.discovered_node_ids = [state.source_node_id]
        state.expanded_node_ids = [state.source_node_id]
        for task in state.queue:
            task.source_node_id = state.source_node_id
        _persist(state)

    while True:
        elapsed_this_invocation = time.monotonic() - invocation_started
        total_elapsed = state.elapsed_seconds + elapsed_this_invocation
        queued = next((task for task in state.queue if task.status == "queued"), None)
        if queued is None:
            state.status = "completed"
            state.stop_reason = "queue exhausted"
            break
        if queued.depth > state.bounds.max_depth:
            queued.status = "skipped"
            queued.error = "depth bound"
            _persist(state)
            continue
        if len(state.discovered_node_ids) >= state.bounds.max_nodes:
            state.status = "stopped_bound"
            state.stop_reason = "max_nodes reached"
            break
        if state.actions_executed + 2 > state.bounds.max_actions:
            state.status = "stopped_bound"
            state.stop_reason = "max_actions reached"
            break
        if total_elapsed >= state.bounds.max_seconds:
            state.status = "stopped_bound"
            state.stop_reason = "max_seconds reached"
            break
        if (
            pause_after_actions is not None
            and state.actions_executed - starting_actions >= pause_after_actions
        ):
            state.status = "paused"
            state.stop_reason = "pause_after_actions reached"
            break

        try:
            _check_focus(state)
            pid = _ensure_runtime(state)
        except Exception as exc:
            state.status = "failed"
            state.stop_reason = f"safety guard: {type(exc).__name__}: {exc}"
            break
        task = queued
        task.status = "running"
        task.attempts += 1
        task.error = None
        attempt_root = (
            Path(state.run_path)
            / "tasks"
            / task.task_id
            / f"attempt-{task.attempts}"
        )
        trace: dict[str, Any] = {"events": [], "success": False}

        def event(name: str, **details: Any) -> None:
            trace["events"].append({"event": name, "at": _now(), **details})
            attempt_root.mkdir(parents=True, exist_ok=True)
            write_json(attempt_root / "trace.json", trace)

        opened = False
        try:
            before_id = capture_identified_state(
                pid,
                diagnostics,
                attempt_root / "before",
                Path(state.registry_root),
                ax_max_depth,
                max_elements,
            )
            if before_id != state.source_node_id:
                raise ExplorationError(
                    f"task source {before_id} does not match {state.source_node_id}"
                )
            task.phase = "source_verified"
            event("source_verified", node_id=before_id)
            _persist(state)

            _open(task, pid, timeout)
            opened = True
            state.actions_executed += 1
            task.phase = "opened"
            event("action_opened", target=task.target)
            _persist(state)

            destination_id = capture_identified_state(
                pid,
                diagnostics,
                attempt_root / "destination",
                Path(state.registry_root),
                ax_max_depth,
                max_elements,
            )
            task.destination_node_id = destination_id
            task.phase = "destination_verified"
            task.loop_detected = destination_id in state.discovered_node_ids
            if not task.loop_detected:
                state.discovered_node_ids.append(destination_id)
            event("destination_verified", node_id=destination_id, loop=task.loop_detected)
            _persist(state)

            _close(task, pid, timeout)
            opened = False
            state.actions_executed += 1
            task.phase = "closed"
            event("reverse_action_performed")
            _persist(state)

            returned_id = capture_identified_state(
                pid,
                diagnostics,
                attempt_root / "returned",
                Path(state.registry_root),
                ax_max_depth,
                max_elements,
            )
            if returned_id != state.source_node_id:
                raise ExplorationError(
                    f"return reached {returned_id}, expected {state.source_node_id}"
                )
            task.return_verified = True
            forward, reverse = _action_metadata(task)
            evidence = EdgeEvidence(
                recorded_at=_now(),
                run_path=str(attempt_root.resolve()),
                source_observation=str((attempt_root / "before").resolve()),
                destination_observation=str((attempt_root / "destination").resolve()),
                returned_observation=str((attempt_root / "returned").resolve()),
            )
            forward_edge, reverse_edge = persist_edge_pair(
                Path(state.graph_root),
                Path(state.registry_root),
                state.source_node_id,
                destination_id,
                forward,
                reverse,
                evidence,
            )
            task.edge_ids = [forward_edge.edge_id, reverse_edge.edge_id]
            task.status = "succeeded"
            task.phase = "complete"
            task.trace_path = str((attempt_root / "trace.json").resolve())
            state.tasks_completed += 1
            trace["success"] = True
            event("return_verified", node_id=returned_id)
            _persist(state)
        except KeyboardInterrupt:
            if opened:
                try:
                    _close(task, pid, timeout)
                    state.actions_executed += 1
                    event("interrupt_recovery_performed")
                except Exception as recovery_error:
                    event("interrupt_recovery_failed", error=str(recovery_error))
            task.status = "queued"
            task.phase = "queued"
            state.status = "paused"
            state.stop_reason = "keyboard interrupt"
            _persist(state)
            break
        except Exception as exc:
            task.error = f"{type(exc).__name__}: {exc}"
            event("task_failed", error=task.error)
            recovered = not opened
            if opened:
                try:
                    _close(task, pid, timeout)
                    state.actions_executed += 1
                    recovered = True
                    event("failure_recovery_performed")
                except Exception as recovery_error:
                    event("failure_recovery_failed", error=str(recovery_error))
            if recovered:
                try:
                    recovered_id = capture_identified_state(
                        pid,
                        diagnostics,
                        attempt_root / "recovered",
                        Path(state.registry_root),
                        ax_max_depth,
                        max_elements,
                    )
                    recovered = recovered_id == state.source_node_id
                    event("recovery_verified", node_id=recovered_id)
                except Exception as recovery_capture_error:
                    recovered = False
                    event("recovery_verification_failed", error=str(recovery_capture_error))
            if not recovered:
                task.status = "failed"
                state.status = "failed"
                state.stop_reason = f"unrecoverable task {task.task_id}"
                _persist(state)
                break
            if task.attempts <= state.bounds.max_retries:
                task.status = "queued"
                task.phase = "queued"
                event("retry_queued", attempt=task.attempts)
            else:
                task.status = "failed"
                state.tasks_completed += 1
                event("retry_limit_reached")
            _persist(state)

    state.elapsed_seconds += time.monotonic() - invocation_started
    if state.status in {"completed", "failed", "stopped_bound"}:
        state.completed_at = _now()
    _persist(state)
    return state
