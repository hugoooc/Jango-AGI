import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app_mapper.graph import ReplayRefused, record_about_edges, replay_edge
from app_mapper.models import (
    EdgeAction,
    GraphEdge,
    NodeFingerprints,
    NodeObservation,
    NodeRecord,
)
from app_mapper.menu_inventory import safe_screen_action_keys


def _write_node(registry: Path, node_id: str, state_type: str) -> None:
    node_dir = registry / node_id
    node_dir.mkdir(parents=True)
    observation = NodeObservation(
        path=f"/observations/{node_id}",
        observed_at="2026-07-11T22:00:00+00:00",
        structural_sha256="a" * 64,
        visual_dhash="0" * 16,
        semantic_sha256="b" * 64,
    )
    node = NodeRecord(
        node_id=node_id,
        semantic_name=f"OpenVSP {state_type}",
        semantic_description=f"Test {state_type}",
        state_type=state_type,
        semantic_source="deterministic",
        application={"name": "OpenVSP", "version": "3.51.0"},
        window_summary={"accessibility_window_count": 3, "dialog_count": 0},
        stable_landmarks=[],
        interactive_control_summary={},
        fingerprints=NodeFingerprints(
            structural_sha256="a" * 64,
            visual_dhash="0" * 16,
            semantic_sha256="b" * 64,
            structural_variants=["a" * 64],
            visual_variants=["0" * 16],
            semantic_variants=["b" * 64],
        ),
        representative_screenshot=f"/screenshots/{node_id}.png",
        first_seen=observation.observed_at,
        last_seen=observation.observed_at,
        observation_count=1,
        observations=[observation],
    )
    (node_dir / "node.json").write_text(node.model_dump_json(), encoding="utf-8")


def _capture_sequence(monkeypatch, node_ids: list[str]):
    values = iter(node_ids)

    def capture(_pid, _diagnostics, destination, _registry, _depth, _elements, *_extra):
        destination.mkdir(parents=True, exist_ok=False)
        return next(values)

    monkeypatch.setattr("app_mapper.graph._capture_identified_state", capture)


def test_record_about_persists_two_edges_and_valid_exports(monkeypatch, tmp_path: Path) -> None:
    graph_root = tmp_path / "graph"
    registry = tmp_path / "nodes"
    run_root = tmp_path / "runs"
    source = "node-workspace"
    destination = "node-dialog"
    _write_node(registry, source, "workspace")
    _write_node(registry, destination, "dialog")
    _capture_sequence(monkeypatch, [source, destination, source])
    actions: list[str] = []
    monkeypatch.setattr("app_mapper.graph._current_dialog", lambda _pid: None)
    monkeypatch.setattr(
        "app_mapper.graph._open_about", lambda _pid, _timeout: actions.append("open")
    )
    monkeypatch.setattr(
        "app_mapper.graph._dismiss_dialog", lambda _pid, _timeout: actions.append("dismiss")
    )

    forward, reverse, run = record_about_edges(
        123,
        {},
        graph_root,
        registry,
        run_root,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert actions == ["open", "dismiss"]
    assert forward.source_node_id == source
    assert forward.destination_node_id == destination
    assert reverse.source_node_id == destination
    assert reverse.destination_node_id == source
    assert forward.action.reverse_action_key == reverse.action.action_key
    graph = json.loads((graph_root / "graph.json").read_text())
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 2
    assert (run / "trace.json").is_file()
    ET.parse(graph_root / "graph.graphml")


def test_replay_refuses_wrong_source_without_action(monkeypatch, tmp_path: Path) -> None:
    graph_root = tmp_path / "graph"
    registry = tmp_path / "nodes"
    run_root = tmp_path / "runs"
    replay_root = tmp_path / "replays"
    source = "node-workspace"
    destination = "node-dialog"
    wrong = "node-wrong"
    for node_id, state_type in (
        (source, "workspace"),
        (destination, "dialog"),
        (wrong, "manager"),
    ):
        _write_node(registry, node_id, state_type)
    _capture_sequence(monkeypatch, [source, destination, source])
    monkeypatch.setattr("app_mapper.graph._current_dialog", lambda _pid: None)
    monkeypatch.setattr("app_mapper.graph._open_about", lambda _pid, _timeout: None)
    monkeypatch.setattr("app_mapper.graph._dismiss_dialog", lambda _pid, _timeout: None)
    forward, _, _ = record_about_edges(
        123, {}, graph_root, registry, run_root, 1, 2, 20
    )

    _capture_sequence(monkeypatch, [wrong])
    calls: list[str] = []
    monkeypatch.setattr(
        "app_mapper.graph._open_about", lambda _pid, _timeout: calls.append("open")
    )

    with pytest.raises(ReplayRefused, match="No action was taken"):
        replay_edge(
            forward.edge_id,
            123,
            {},
            graph_root,
            registry,
            replay_root,
            1,
            2,
            20,
        )

    assert calls == []
    stored = json.loads(
        (graph_root / "edges" / f"{forward.edge_id}.json").read_text()
    )
    assert stored["replay"] == {
        "attempts": 1,
        "failures": 1,
        "last_attempt_at": stored["replay"]["last_attempt_at"],
        "last_success_at": None,
        "refused_wrong_source": 1,
        "successes": 0,
    }


def test_replay_executes_allowlisted_action_and_verifies_destination(
    monkeypatch, tmp_path: Path
) -> None:
    graph_root = tmp_path / "graph"
    registry = tmp_path / "nodes"
    run_root = tmp_path / "runs"
    replay_root = tmp_path / "replays"
    source = "node-workspace"
    destination = "node-dialog"
    _write_node(registry, source, "workspace")
    _write_node(registry, destination, "dialog")
    _capture_sequence(monkeypatch, [source, destination, source])
    monkeypatch.setattr("app_mapper.graph._current_dialog", lambda _pid: None)
    monkeypatch.setattr("app_mapper.graph._open_about", lambda _pid, _timeout: None)
    monkeypatch.setattr("app_mapper.graph._dismiss_dialog", lambda _pid, _timeout: None)
    forward, _, _ = record_about_edges(
        123, {}, graph_root, registry, run_root, 1, 2, 20
    )

    _capture_sequence(monkeypatch, [source, destination])
    calls: list[str] = []
    monkeypatch.setattr(
        "app_mapper.graph._open_about", lambda _pid, _timeout: calls.append("open")
    )
    result, run = replay_edge(
        forward.edge_id,
        123,
        {},
        graph_root,
        registry,
        replay_root,
        1,
        2,
        20,
    )

    assert calls == ["open"]
    assert result["success"] is True
    assert result["observed_destination_node_id"] == destination
    assert (run / "replay.json").is_file()
    stored = json.loads(
        (graph_root / "edges" / f"{forward.edge_id}.json").read_text()
    )
    assert stored["replay"]["attempts"] == 1
    assert stored["replay"]["successes"] == 1


def test_replay_executes_only_trusted_menu_locator(monkeypatch, tmp_path: Path) -> None:
    graph_root = tmp_path / "graph"
    edge_root = graph_root / "edges"
    registry = tmp_path / "nodes"
    replay_root = tmp_path / "replays"
    edge_root.mkdir(parents=True)
    source, destination = "node-workspace", "node-file-menu"
    _write_node(registry, source, "workspace")
    _write_node(registry, destination, "menu")
    edge = GraphEdge(
        edge_id="edge-file",
        source_node_id=source,
        destination_node_id=destination,
        action=EdgeAction(
            action_key="open_menu_file",
            semantic_description="tampered description",
            accessibility_locator={"title": "Model"},
            preconditions=[],
            expected_postconditions=[],
            reverse_action_key="dismiss_menu_file",
        ),
        evidence=[],
    )
    (edge_root / "edge-file.json").write_text(edge.model_dump_json(), encoding="utf-8")
    _capture_sequence(monkeypatch, [source, destination])
    calls: list[str] = []
    monkeypatch.setattr(
        "app_mapper.discovery._open_menu",
        lambda _pid, title, _timeout: calls.append(title),
    )

    result, _ = replay_edge(
        "edge-file", 123, {}, graph_root, registry, replay_root, 1, 2, 20
    )

    assert result["success"] is True
    assert calls == ["File"]


def test_replay_executes_only_trusted_safe_screen_path(monkeypatch, tmp_path: Path) -> None:
    graph_root = tmp_path / "graph"
    edge_root = graph_root / "edges"
    registry = tmp_path / "nodes"
    replay_root = tmp_path / "replays"
    edge_root.mkdir(parents=True)
    source, destination = "node-workspace", "node-preferences"
    _write_node(registry, source, "workspace")
    _write_node(registry, destination, "manager")
    action_key, reverse_key = safe_screen_action_keys(("File", "Preferences..."))
    edge = GraphEdge(
        edge_id="edge-preferences",
        source_node_id=source,
        destination_node_id=destination,
        action=EdgeAction(
            action_key=action_key,
            semantic_description="tampered",
            accessibility_locator={"menu_path": ["File", "Save..."]},
            preconditions=[],
            expected_postconditions=[],
            reverse_action_key=reverse_key,
        ),
        evidence=[],
    )
    (edge_root / "edge-preferences.json").write_text(edge.model_dump_json(), encoding="utf-8")
    _capture_sequence(monkeypatch, [source, destination])
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "app_mapper.safe_expansion.open_safe_screen",
        lambda _pid, path, _timeout: calls.append(path),
    )

    result, _ = replay_edge(
        "edge-preferences", 123, {}, graph_root, registry, replay_root, 1, 2, 20
    )

    assert result["success"] is True
    assert calls == [("File", "Preferences...")]


def test_replay_executes_curated_internal_tab_action(monkeypatch, tmp_path: Path) -> None:
    graph_root = tmp_path / "graph"
    edge_root = graph_root / "edges"
    registry = tmp_path / "nodes"
    replay_root = tmp_path / "replays"
    edge_root.mkdir(parents=True)
    source, destination = "node-apply-tab", "node-group-tab"
    _write_node(registry, source, "manager")
    _write_node(registry, destination, "manager")
    edge = GraphEdge(
        edge_id="edge-group-tab",
        source_node_id=source,
        destination_node_id=destination,
        action=EdgeAction(
            action_key="select_tab_variable_presets_group",
            semantic_description="Select Group",
            mechanism="quartz_coordinate",
            accessibility_locator={},
            visual_locator={"normalized_click_point": [999, 999]},
            preconditions=[],
            expected_postconditions=[],
            reverse_action_key="select_tab_variable_presets_apply_from_group",
        ),
        evidence=[],
    )
    (edge_root / "edge-group-tab.json").write_text(edge.model_dump_json(), encoding="utf-8")
    _capture_sequence(monkeypatch, [source, destination])
    calls: list[str] = []
    monkeypatch.setattr(
        "app_mapper.recursive_exploration.current_variable_presets_window",
        lambda _pid: {"window_id": 42},
    )
    monkeypatch.setattr(
        "app_mapper.recursive_exploration.click_curated_tab",
        lambda _pid, action_key: calls.append(action_key),
    )

    result, _ = replay_edge(
        "edge-group-tab", 123, {}, graph_root, registry, replay_root, 1, 2, 20
    )

    assert result["success"] is True
    assert calls == ["select_tab_variable_presets_group"]
