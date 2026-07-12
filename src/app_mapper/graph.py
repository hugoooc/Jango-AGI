from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ApplicationServices

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.identity import identify_observation
from app_mapper.interpretation import capture_interpretation_observation
from app_mapper.macos.accessibility import (
    application_windows,
    choose_dismiss_action,
    choose_new_window,
    find_menu_item,
    perform_action,
)
from app_mapper.models import (
    EdgeAction,
    EdgeEvidence,
    GraphEdge,
    GraphNodeSummary,
    GraphRecord,
    NodeRecord,
)
from app_mapper.transition import ABOUT_MENU_TITLES, wait_for


class GraphError(RuntimeError):
    pass


class ReplayRefused(GraphError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _edge_id(source: str, destination: str, action_key: str) -> str:
    digest = hashlib.sha256(f"{source}\0{destination}\0{action_key}".encode()).hexdigest()
    return f"edge-{digest[:12]}"


def _capture_identified_state(
    pid: int,
    diagnostics: dict[str, Any],
    destination: Path,
    registry_root: Path,
    max_depth: int,
    max_elements: int,
) -> str:
    destination.mkdir(parents=True, exist_ok=False)
    write_json(
        destination / "manifest.json",
        {
            **diagnostics,
            "checked_at": _now(),
            "graph_capture": True,
            "actions_executed": 0,
        },
    )
    capture_interpretation_observation(pid, destination, max_depth, max_elements)
    decision = identify_observation(destination, registry_root)
    if decision.review_required or decision.node_id is None:
        raise GraphError(
            f"State identity is {decision.status}; graph recording/replay requires an unambiguous node."
        )
    return decision.node_id


def capture_identified_state(
    pid: int,
    diagnostics: dict[str, Any],
    destination: Path,
    registry_root: Path,
    max_depth: int,
    max_elements: int,
) -> str:
    return _capture_identified_state(
        pid,
        diagnostics,
        destination,
        registry_root,
        max_depth,
        max_elements,
    )


def _new_about_dialog(
    before: list[tuple[Any, dict[str, Any]]], pid: int
) -> tuple[Any, dict[str, Any]] | None:
    candidate = choose_new_window(before, application_windows(pid))
    if candidate is None:
        return None
    if (
        candidate[1].get(str(ApplicationServices.kAXSubroleAttribute))
        != ApplicationServices.kAXDialogSubrole
    ):
        return None
    return candidate


def _current_dialog(pid: int) -> tuple[Any, dict[str, Any]] | None:
    return next(
        (
            window
            for window in application_windows(pid)
            if window[1].get(str(ApplicationServices.kAXSubroleAttribute))
            == ApplicationServices.kAXDialogSubrole
        ),
        None,
    )


def _open_about(pid: int, timeout: float) -> tuple[Any, dict[str, Any]]:
    before = application_windows(pid)
    target = find_menu_item(pid, ABOUT_MENU_TITLES)
    if target is None:
        raise GraphError("Exact allowlisted OpenVSP About menu item was not found.")
    perform_action(target[0], ApplicationServices.kAXPressAction)
    dialog = wait_for(lambda: _new_about_dialog(before, pid), timeout=timeout)
    if dialog is None:
        raise GraphError("Timed out waiting for the About dialog.")
    return dialog


def _dismiss_dialog(pid: int, timeout: float) -> None:
    dialog = _current_dialog(pid)
    if dialog is None:
        raise GraphError("No Accessibility dialog is open to dismiss.")
    dismiss = choose_dismiss_action(dialog[0])
    if dismiss is None:
        raise GraphError("The dialog has no allowlisted dismissal action.")
    expected_window_count = sum(
        window[1].get(str(ApplicationServices.kAXSubroleAttribute))
        != ApplicationServices.kAXDialogSubrole
        for window in application_windows(pid)
    )
    perform_action(dismiss[0], dismiss[1])

    def workspace_stable() -> bool | None:
        windows = application_windows(pid)
        has_dialog = any(
            window[1].get(str(ApplicationServices.kAXSubroleAttribute))
            == ApplicationServices.kAXDialogSubrole
            for window in windows
        )
        return True if not has_dialog and len(windows) == expected_window_count else None

    closed = wait_for(
        workspace_stable,
        timeout=timeout,
    )
    if closed is None:
        raise GraphError("Timed out waiting for the dialog to close.")


def _node_summaries(
    registry_root: Path, included_node_ids: set[str]
) -> list[GraphNodeSummary]:
    summaries: list[GraphNodeSummary] = []
    for path in sorted(registry_root.glob("node-*/node.json")):
        node = NodeRecord.model_validate(_read_json(path))
        if node.node_id not in included_node_ids:
            continue
        summaries.append(
            GraphNodeSummary(
                node_id=node.node_id,
                semantic_name=node.semantic_name,
                state_type=node.state_type,
                representative_screenshot=node.representative_screenshot,
            )
        )
    return summaries


def _load_edges(graph_root: Path) -> list[GraphEdge]:
    edge_root = graph_root / "edges"
    if not edge_root.is_dir():
        return []
    return [
        GraphEdge.model_validate(_read_json(path))
        for path in sorted(edge_root.glob("edge-*.json"))
    ]


def _save_edge(graph_root: Path, edge: GraphEdge) -> GraphEdge:
    edge_root = graph_root / "edges"
    edge_root.mkdir(parents=True, exist_ok=True)
    path = edge_root / f"{edge.edge_id}.json"
    for candidate_path in edge_root.glob("edge-*.json"):
        if candidate_path == path:
            continue
        candidate = GraphEdge.model_validate(_read_json(candidate_path))
        if candidate.action.action_key != edge.action.action_key:
            continue
        superseded_root = graph_root / "superseded"
        superseded_root.mkdir(parents=True, exist_ok=True)
        superseded_path = superseded_root / candidate_path.name
        if superseded_path.exists():
            superseded_path = superseded_root / (
                f"{candidate_path.stem}-{hashlib.sha256(str(_now()).encode()).hexdigest()[:8]}.json"
            )
        shutil.move(candidate_path, superseded_path)
    if path.is_file():
        existing = GraphEdge.model_validate(_read_json(path))
        known_runs = {evidence.run_path for evidence in existing.evidence}
        for evidence in edge.evidence:
            if evidence.run_path not in known_runs:
                existing.evidence.append(evidence)
        edge = existing
    write_json(path, edge.model_dump(mode="json"))
    return edge


def persist_edge_pair(
    graph_root: Path,
    registry_root: Path,
    source_node_id: str,
    destination_node_id: str,
    forward_action: EdgeAction,
    reverse_action: EdgeAction,
    evidence: EdgeEvidence,
) -> tuple[GraphEdge, GraphEdge]:
    forward = GraphEdge(
        edge_id=_edge_id(
            source_node_id, destination_node_id, forward_action.action_key
        ),
        source_node_id=source_node_id,
        destination_node_id=destination_node_id,
        action=forward_action,
        evidence=[evidence],
    )
    reverse = GraphEdge(
        edge_id=_edge_id(
            destination_node_id, source_node_id, reverse_action.action_key
        ),
        source_node_id=destination_node_id,
        destination_node_id=source_node_id,
        action=reverse_action,
        evidence=[evidence],
    )
    forward = _save_edge(graph_root, forward)
    reverse = _save_edge(graph_root, reverse)
    export_graph(graph_root, registry_root)
    return forward, reverse


def export_graph(graph_root: Path, registry_root: Path) -> GraphRecord:
    graph_root.mkdir(parents=True, exist_ok=True)
    edges = _load_edges(graph_root)
    included_node_ids = {
        node_id
        for edge in edges
        for node_id in (edge.source_node_id, edge.destination_node_id)
    }
    graph = GraphRecord(
        updated_at=_now(),
        nodes=_node_summaries(registry_root, included_node_ids),
        edges=edges,
    )
    write_json(graph_root / "graph.json", graph.model_dump(mode="json"))

    graphml = ET.Element(
        "graphml", xmlns="http://graphml.graphdrawing.org/xmlns"
    )
    for key_id, target, name, value_type in (
        ("label", "node", "label", "string"),
        ("state_type", "node", "state_type", "string"),
        ("action", "edge", "action", "string"),
        ("risk", "edge", "risk", "string"),
        ("success_rate", "edge", "success_rate", "double"),
    ):
        ET.SubElement(
            graphml,
            "key",
            id=key_id,
            **{"for": target, "attr.name": name, "attr.type": value_type},
        )
    xml_graph = ET.SubElement(graphml, "graph", edgedefault="directed", id="openvsp")
    for node in graph.nodes:
        xml_node = ET.SubElement(xml_graph, "node", id=node.node_id)
        ET.SubElement(xml_node, "data", key="label").text = node.semantic_name
        ET.SubElement(xml_node, "data", key="state_type").text = node.state_type
    for edge in graph.edges:
        xml_edge = ET.SubElement(
            xml_graph,
            "edge",
            id=edge.edge_id,
            source=edge.source_node_id,
            target=edge.destination_node_id,
        )
        ET.SubElement(xml_edge, "data", key="action").text = edge.action.semantic_description
        ET.SubElement(xml_edge, "data", key="risk").text = edge.action.risk
        rate = edge.replay.successes / edge.replay.attempts if edge.replay.attempts else 0.0
        ET.SubElement(xml_edge, "data", key="success_rate").text = f"{rate:.6f}"
    xml = ET.tostring(graphml, encoding="unicode", xml_declaration=True)
    (graph_root / "graph.graphml").write_text(xml + "\n", encoding="utf-8")
    return graph


def _actions() -> dict[str, EdgeAction]:
    return {
        "open_about": EdgeAction(
            action_key="open_about",
            semantic_description="Open the informational About vsp dialog",
            accessibility_locator={
                "role": "AXMenuItem",
                "titles": list(ABOUT_MENU_TITLES),
                "identifier": "showPanel",
                "action": "AXPress",
            },
            preconditions=[
                "OpenVSP is running",
                "current node matches source_node_id",
                "no modal Accessibility dialog is open",
            ],
            expected_postconditions=[
                "one new AXDialog is present",
                "observed node matches destination_node_id",
            ],
            reverse_action_key="dismiss_about",
        ),
        "dismiss_about": EdgeAction(
            action_key="dismiss_about",
            semantic_description="Dismiss the informational About vsp dialog",
            accessibility_locator={
                "window_subrole": "AXDialog",
                "preferred_control_subrole": "AXCloseButton",
                "fallback_titles": ["OK", "Close", "Cancel"],
                "fallback_action": "AXCancel",
            },
            preconditions=[
                "OpenVSP is running",
                "current node matches source_node_id",
                "an AXDialog with an allowlisted dismissal action is open",
            ],
            expected_postconditions=[
                "the AXDialog is absent",
                "observed node matches destination_node_id",
            ],
            reverse_action_key="open_about",
        ),
    }


def record_about_edges(
    pid: int,
    diagnostics: dict[str, Any],
    graph_root: Path,
    registry_root: Path,
    run_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[GraphEdge, GraphEdge, Path]:
    run = create_observation_directory(run_root)
    trace: dict[str, Any] = {"started_at": _now(), "events": [], "success": False}
    write_json(run / "trace.json", trace)

    def record(event: str, **details: Any) -> None:
        trace["events"].append({"event": event, "at": _now(), **details})
        write_json(run / "trace.json", trace)

    source_dir = run / "source"
    destination_dir = run / "destination"
    returned_dir = run / "returned"
    if _current_dialog(pid) is not None:
        raise GraphError(
            "About edge recording requires the normal workspace with no Accessibility dialog open."
        )
    source_id = _capture_identified_state(
        pid, diagnostics, source_dir, registry_root, max_depth, max_elements
    )
    record("source_verified", node_id=source_id, observation=str(source_dir.resolve()))

    dialog_open = False
    try:
        _open_about(pid, timeout)
        dialog_open = True
        record("open_about_performed")
        destination_id = _capture_identified_state(
            pid,
            diagnostics,
            destination_dir,
            registry_root,
            max_depth,
            max_elements,
        )
        record(
            "destination_verified",
            node_id=destination_id,
            observation=str(destination_dir.resolve()),
        )
        if destination_id == source_id:
            raise GraphError("About destination unexpectedly matched the workspace source node.")
        _dismiss_dialog(pid, timeout)
        dialog_open = False
        record("dismiss_about_performed")
    except Exception:
        if dialog_open and _current_dialog(pid) is not None:
            try:
                _dismiss_dialog(pid, timeout)
                record("failure_cleanup_dismissed_dialog")
            except Exception as cleanup_error:
                record("failure_cleanup_failed", error=str(cleanup_error))
        raise

    returned_id = _capture_identified_state(
        pid, diagnostics, returned_dir, registry_root, max_depth, max_elements
    )
    record(
        "source_return_verified",
        node_id=returned_id,
        observation=str(returned_dir.resolve()),
    )
    if returned_id != source_id:
        raise GraphError(
            f"Dismissal returned to {returned_id}, expected original source {source_id}."
        )

    evidence = EdgeEvidence(
        recorded_at=_now(),
        run_path=str(run.resolve()),
        source_observation=str(source_dir.resolve()),
        destination_observation=str(destination_dir.resolve()),
        returned_observation=str(returned_dir.resolve()),
    )
    actions = _actions()
    forward, reverse = persist_edge_pair(
        graph_root,
        registry_root,
        source_id,
        destination_id,
        actions["open_about"],
        actions["dismiss_about"],
        evidence,
    )
    trace["success"] = True
    trace["completed_at"] = _now()
    trace["edge_ids"] = [forward.edge_id, reverse.edge_id]
    write_json(run / "trace.json", trace)
    return forward, reverse, run


def load_edge(graph_root: Path, edge_id: str) -> GraphEdge:
    path = graph_root / "edges" / f"{edge_id}.json"
    if not path.is_file():
        raise GraphError(f"Unknown edge ID: {edge_id}")
    return GraphEdge.model_validate(_read_json(path))


def _update_replay(
    graph_root: Path,
    registry_root: Path,
    edge: GraphEdge,
    *,
    success: bool,
    refused: bool = False,
) -> None:
    edge.replay.attempts += 1
    edge.replay.last_attempt_at = _now()
    if success:
        edge.replay.successes += 1
        edge.replay.last_success_at = edge.replay.last_attempt_at
    else:
        edge.replay.failures += 1
    if refused:
        edge.replay.refused_wrong_source += 1
    write_json(
        graph_root / "edges" / f"{edge.edge_id}.json",
        edge.model_dump(mode="json"),
    )
    export_graph(graph_root, registry_root)


def replay_edge(
    edge_id: str,
    pid: int,
    diagnostics: dict[str, Any],
    graph_root: Path,
    registry_root: Path,
    replay_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[dict[str, Any], Path]:
    edge = load_edge(graph_root, edge_id)
    if edge.action.risk != "safe_navigation" or not edge.action.reversible:
        raise ReplayRefused("Edge is not both safe_navigation and reversible.")
    if edge.action.action_key not in _actions():
        raise ReplayRefused(f"Action {edge.action.action_key!r} is not executable.")

    run = create_observation_directory(replay_root)
    result: dict[str, Any] = {
        "edge_id": edge.edge_id,
        "started_at": _now(),
        "success": False,
        "action_performed": False,
    }
    write_json(run / "replay.json", result)
    try:
        source_id = _capture_identified_state(
            pid,
            diagnostics,
            run / "source",
            registry_root,
            max_depth,
            max_elements,
        )
        result["observed_source_node_id"] = source_id
        result["expected_source_node_id"] = edge.source_node_id
        if source_id != edge.source_node_id:
            result["refused"] = True
            result["error"] = "current state does not match the edge source node"
            _update_replay(
                graph_root, registry_root, edge, success=False, refused=True
            )
            write_json(run / "replay.json", result)
            raise ReplayRefused(
                f"Current node is {source_id}; edge requires {edge.source_node_id}. No action was taken."
            )

        if edge.action.action_key == "open_about":
            _open_about(pid, timeout)
        elif edge.action.action_key == "dismiss_about":
            _dismiss_dialog(pid, timeout)
        result["action_performed"] = True

        destination_id = _capture_identified_state(
            pid,
            diagnostics,
            run / "destination",
            registry_root,
            max_depth,
            max_elements,
        )
        result["observed_destination_node_id"] = destination_id
        result["expected_destination_node_id"] = edge.destination_node_id
        if destination_id != edge.destination_node_id:
            raise GraphError(
                f"Replay reached {destination_id}, expected {edge.destination_node_id}."
            )
        result["success"] = True
        result["completed_at"] = _now()
        _update_replay(graph_root, registry_root, edge, success=True)
        write_json(run / "replay.json", result)
        return result, run
    except ReplayRefused:
        raise
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if result["action_performed"] and edge.action.action_key == "open_about":
            try:
                _dismiss_dialog(pid, timeout)
                result["failure_cleanup"] = "dialog dismissed"
            except Exception as cleanup_error:
                result["failure_cleanup"] = f"failed: {cleanup_error}"
        _update_replay(graph_root, registry_root, edge, success=False)
        write_json(run / "replay.json", result)
        raise


def graph_summary(graph_root: Path, registry_root: Path) -> GraphRecord:
    return export_graph(graph_root, registry_root)
