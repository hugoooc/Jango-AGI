from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ApplicationServices

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.graph import capture_identified_state, persist_edge_pair
from app_mapper.macos.accessibility import attribute_value, element_summary, perform_action
from app_mapper.models import (
    DiscoveryCandidate,
    DiscoveryRecord,
    EdgeAction,
    EdgeEvidence,
)
from app_mapper.transition import wait_for


MENU_ALLOWLIST = (
    {"candidate_id": "menu-file", "title": "File", "description": "Open the File choices menu"},
    {"candidate_id": "menu-view", "title": "View", "description": "Open the View choices menu"},
    {"candidate_id": "menu-model", "title": "Model", "description": "Open the Model choices menu"},
)


class DiscoveryError(RuntimeError):
    def __init__(self, message: str, *, action_performed: bool = False):
        super().__init__(message)
        self.action_performed = action_performed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return hashlib.sha256(value.casefold().encode()).hexdigest()[:10]


def latest_interpretation(root: Path) -> Path | None:
    matches = sorted(root.glob("*/interpretation.json"), reverse=True)
    return matches[0] if matches else None


def build_candidates(interpretation_path: Path | None) -> list[DiscoveryCandidate]:
    candidates: list[DiscoveryCandidate] = []
    if interpretation_path is not None and interpretation_path.is_file():
        interpretation = json.loads(interpretation_path.read_text(encoding="utf-8"))
        for target in interpretation.get("navigation_targets", []):
            candidates.append(
                DiscoveryCandidate(
                    candidate_id=f"holo-{_slug(target['label'])}",
                    label=target["label"],
                    origin="holo",
                    classification="unknown",
                    confidence=target["confidence"],
                    locator={
                        "bounding_box": target["bounding_box"],
                        "accessibility_match": target.get("accessibility_match"),
                    },
                    policy_decision="rejected",
                    policy_reasons=[
                        "Holo proposal has no exact executable human-allowlist entry"
                    ],
                    status="rejected",
                )
            )
        for rejected in interpretation.get("rejected_targets", []):
            target = rejected["target"]
            candidates.append(
                DiscoveryCandidate(
                    candidate_id=f"holo-{_slug(target['label'])}",
                    label=target["label"],
                    origin="holo",
                    classification="excluded",
                    confidence=target["confidence"],
                    locator={"bounding_box": target["bounding_box"]},
                    policy_decision="rejected",
                    policy_reasons=[f"Milestone 3 rejection: {rejected['reason']}"],
                    status="rejected",
                )
            )
    for target in MENU_ALLOWLIST:
        candidates.append(
            DiscoveryCandidate(
                candidate_id=target["candidate_id"],
                label=f"{target['title']} menu",
                origin="human_allowlist",
                classification="menu",
                confidence=1.0,
                locator={
                    "role": "AXMenuBarItem",
                    "title": target["title"],
                    "open_action": "AXPress",
                    "close_action": "AXCancel",
                },
                policy_decision="approved",
                policy_reasons=[
                    "top-level menu only exposes choices",
                    "exact AXMenuBarItem locator",
                    "AXCancel provides a non-selecting reverse action",
                ],
                status="proposed",
            )
        )
    return candidates


def _menu_item(pid: int, title: str) -> Any | None:
    application = ApplicationServices.AXUIElementCreateApplication(pid)
    menu_bar = attribute_value(application, ApplicationServices.kAXMenuBarAttribute)
    if menu_bar is None:
        return None
    for item in attribute_value(menu_bar, ApplicationServices.kAXChildrenAttribute) or []:
        summary = element_summary(item)
        if (
            summary.get(str(ApplicationServices.kAXRoleAttribute))
            == ApplicationServices.kAXMenuBarItemRole
            and summary.get(str(ApplicationServices.kAXTitleAttribute)) == title
            and summary.get(str(ApplicationServices.kAXEnabledAttribute), True) is True
        ):
            return item
    return None


def _menu_active(item: Any) -> bool:
    return (
        attribute_value(item, ApplicationServices.kAXExpandedAttribute) is True
        or attribute_value(item, ApplicationServices.kAXSelectedAttribute) is True
    )


def _open_menu(pid: int, title: str, timeout: float) -> None:
    item = _menu_item(pid, title)
    if item is None:
        raise DiscoveryError(f"Exact enabled {title!r} menu bar item was not found.")
    if _menu_active(item):
        raise DiscoveryError(f"{title!r} menu is already expanded.")
    perform_action(item, ApplicationServices.kAXPressAction)
    opened = wait_for(lambda: True if _menu_active(item) else None, timeout=timeout)
    if opened is None:
        raise DiscoveryError(
            f"Timed out waiting for {title!r} menu to become selected.",
            action_performed=True,
        )


def _close_menu(pid: int, title: str, timeout: float) -> None:
    item = _menu_item(pid, title)
    if item is None:
        raise DiscoveryError(f"Exact {title!r} menu bar item disappeared.")
    perform_action(item, ApplicationServices.kAXCancelAction)
    closed = wait_for(lambda: True if not _menu_active(item) else None, timeout=timeout)
    if closed is None:
        raise DiscoveryError(f"Timed out waiting for {title!r} menu to close.")


def _summary(candidates: list[DiscoveryCandidate]) -> dict[str, int]:
    return {
        status: sum(candidate.status == status for candidate in candidates)
        for status in ("proposed", "rejected", "succeeded", "failed", "skipped")
    }


def discover_one_hop(
    pid: int,
    diagnostics: dict[str, Any],
    run_root: Path,
    graph_root: Path,
    registry_root: Path,
    interpretation_path: Path | None,
    *,
    execute: bool,
    max_candidates: int,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[DiscoveryRecord, Path]:
    run = create_observation_directory(run_root)
    candidates = build_candidates(interpretation_path)
    record = DiscoveryRecord(
        started_at=_now(),
        mode="execute" if execute else "plan",
        interpretation_path=(
            str(interpretation_path.resolve()) if interpretation_path is not None else None
        ),
        max_candidates=max_candidates,
        candidates=candidates,
        summary=_summary(candidates),
    )
    write_json(run / "discovery.json", record.model_dump(mode="json"))
    approved = [candidate for candidate in candidates if candidate.policy_decision == "approved"]
    for candidate in approved[max_candidates:]:
        candidate.status = "skipped"
        candidate.policy_reasons.append("outside configured max_candidates bound")
    if not execute:
        record.completed_at = _now()
        record.summary = _summary(candidates)
        record.success = True
        write_json(run / "discovery.json", record.model_dump(mode="json"))
        return record, run

    source_node_id: str | None = None
    for candidate in approved[:max_candidates]:
        candidate_root = run / "candidates" / candidate.candidate_id
        trace: dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "started_at": _now(),
            "events": [],
            "success": False,
        }

        def event(name: str, **details: Any) -> None:
            trace["events"].append({"event": name, "at": _now(), **details})
            candidate_root.mkdir(parents=True, exist_ok=True)
            write_json(candidate_root / "trace.json", trace)

        menu_title = str(candidate.locator["title"])
        menu_open = False
        open_action_counted = False
        before_dir = candidate_root / "before"
        destination_dir = candidate_root / "destination"
        returned_dir = candidate_root / "returned"
        try:
            before_id = capture_identified_state(
                pid,
                diagnostics,
                before_dir,
                registry_root,
                max_depth,
                max_elements,
            )
            event("source_verified", node_id=before_id)
            if source_node_id is None:
                source_node_id = before_id
                record.source_node_id = before_id
            elif before_id != source_node_id:
                raise DiscoveryError(
                    f"Candidate started at {before_id}, expected source {source_node_id}."
                )

            _open_menu(pid, menu_title, timeout)
            menu_open = True
            record.actions_executed += 1
            open_action_counted = True
            event("menu_opened", title=menu_title)
            destination_id = capture_identified_state(
                pid,
                diagnostics,
                destination_dir,
                registry_root,
                max_depth,
                max_elements,
            )
            event("destination_verified", node_id=destination_id)
            if destination_id == before_id:
                raise DiscoveryError("Expanded menu did not produce a distinct node.")

            _close_menu(pid, menu_title, timeout)
            menu_open = False
            record.actions_executed += 1
            event("menu_closed", title=menu_title)
            returned_id = capture_identified_state(
                pid,
                diagnostics,
                returned_dir,
                registry_root,
                max_depth,
                max_elements,
            )
            if returned_id != source_node_id:
                raise DiscoveryError(
                    f"Return reached {returned_id}, expected source {source_node_id}."
                )
            event("return_verified", node_id=returned_id)

            evidence = EdgeEvidence(
                recorded_at=_now(),
                run_path=str(candidate_root.resolve()),
                source_observation=str(before_dir.resolve()),
                destination_observation=str(destination_dir.resolve()),
                returned_observation=str(returned_dir.resolve()),
            )
            open_key = f"open_menu_{menu_title.casefold()}"
            close_key = f"dismiss_menu_{menu_title.casefold()}"
            persist_edge_pair(
                graph_root,
                registry_root,
                source_node_id,
                destination_id,
                EdgeAction(
                    action_key=open_key,
                    semantic_description=f"Open the {menu_title} choices menu",
                    accessibility_locator=candidate.locator,
                    preconditions=[
                        "current node matches source_node_id",
                        "no top-level menu is expanded",
                    ],
                    expected_postconditions=[
                        f"{menu_title} AXMenuBarItem is expanded",
                        "observed node matches destination_node_id",
                    ],
                    reverse_action_key=close_key,
                ),
                EdgeAction(
                    action_key=close_key,
                    semantic_description=f"Dismiss the {menu_title} choices menu without selection",
                    accessibility_locator={
                        "role": "AXMenuBarItem",
                        "title": menu_title,
                        "action": "AXCancel",
                    },
                    preconditions=[
                        "current node matches source_node_id",
                        f"{menu_title} menu is expanded",
                    ],
                    expected_postconditions=[
                        "no top-level menu is expanded",
                        "observed node matches destination_node_id",
                    ],
                    reverse_action_key=open_key,
                ),
                evidence,
            )
            candidate.status = "succeeded"
            candidate.destination_node_id = destination_id
            candidate.return_verified = True
            trace["success"] = True
            trace["completed_at"] = _now()
            write_json(candidate_root / "trace.json", trace)
        except Exception as exc:
            if (
                isinstance(exc, DiscoveryError)
                and exc.action_performed
                and not open_action_counted
            ):
                record.actions_executed += 1
                open_action_counted = True
            candidate.status = "failed"
            candidate.error = f"{type(exc).__name__}: {exc}"
            event("candidate_failed", error=candidate.error)
            current_item = _menu_item(pid, menu_title)
            if menu_open or (current_item is not None and _menu_active(current_item)):
                try:
                    _close_menu(pid, menu_title, timeout)
                    record.actions_executed += 1
                    event("recovery_menu_closed")
                    menu_open = False
                except Exception as recovery_error:
                    event("recovery_failed", error=str(recovery_error))
                    candidate.trace_path = str((candidate_root / "trace.json").resolve())
                    break
            try:
                recovered_id = capture_identified_state(
                    pid,
                    diagnostics,
                    returned_dir,
                    registry_root,
                    max_depth,
                    max_elements,
                )
                candidate.return_verified = recovered_id == source_node_id
                event("recovery_state_captured", node_id=recovered_id)
            except Exception as recovery_capture_error:
                event("recovery_capture_failed", error=str(recovery_capture_error))
                break
        finally:
            candidate.trace_path = str((candidate_root / "trace.json").resolve())
            record.summary = _summary(candidates)
            write_json(run / "discovery.json", record.model_dump(mode="json"))

    record.completed_at = _now()
    record.summary = _summary(candidates)
    attempted = [candidate for candidate in candidates if candidate.origin == "human_allowlist"]
    record.success = bool(attempted) and all(
        candidate.status in {"succeeded", "skipped"} for candidate in attempted
    )
    write_json(run / "discovery.json", record.model_dump(mode="json"))
    return record, run
