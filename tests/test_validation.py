from pathlib import Path

from app_mapper.models import EdgeAction, GraphEdge, GraphRecord
from app_mapper.validation import (
    classify_failure,
    sample_edges,
    shortest_edge_path,
    validate_graph,
)


def _edge(edge_id: str, source: str, destination: str, action: str, reverse: str) -> GraphEdge:
    return GraphEdge(
        edge_id=edge_id,
        source_node_id=source,
        destination_node_id=destination,
        action=EdgeAction(
            action_key=action,
            semantic_description=action.replace("_", " "),
            accessibility_locator={"title": "File"},
            preconditions=[],
            expected_postconditions=[],
            reverse_action_key=reverse,
        ),
        evidence=[],
    )


def test_sampling_prefers_forward_edges_and_is_reproducible() -> None:
    edges = [
        _edge("edge-open-b", "workspace", "b", "open_menu_b", "dismiss_menu_b"),
        _edge("edge-close-b", "b", "workspace", "dismiss_menu_b", "open_menu_b"),
        _edge("edge-open-a", "workspace", "a", "open_menu_a", "dismiss_menu_a"),
    ]

    first = sample_edges(edges, 1, 8)
    second = sample_edges(edges, 1, 8)

    assert [edge.edge_id for edge in first] == [edge.edge_id for edge in second]
    assert first[0].action.action_key.startswith("open_")


def test_shortest_path_finds_safe_return_route() -> None:
    edges = [
        _edge("one", "a", "b", "open_menu_a", "dismiss_menu_a"),
        _edge("two", "b", "c", "open_menu_b", "dismiss_menu_b"),
    ]

    path = shortest_edge_path(edges, "a", "c")

    assert path is not None
    assert [edge.edge_id for edge in path] == ["one", "two"]
    assert shortest_edge_path(edges, "c", "a") is None


def test_validate_graph_replays_and_verifies_return(monkeypatch, tmp_path: Path) -> None:
    forward = _edge("edge-open", "workspace", "menu", "open_menu_file", "dismiss_menu_file")
    reverse = _edge("edge-close", "menu", "workspace", "dismiss_menu_file", "open_menu_file")
    graph = GraphRecord(updated_at="2026-07-12T00:00:00+00:00", nodes=[], edges=[forward, reverse])
    monkeypatch.setattr("app_mapper.validation.graph_summary", lambda *_args: graph)
    states = iter(["workspace", "workspace"])

    def capture(_pid, _diagnostics, destination, *_args):
        destination.mkdir(parents=True)
        return next(states)

    calls: list[str] = []

    def replay(edge_id, _pid, _diagnostics, _graph, _registry, replay_root, *_args):
        calls.append(edge_id)
        destination = replay_root / f"run-{len(calls)}"
        destination.mkdir(parents=True)
        observed = "menu" if edge_id == "edge-open" else "workspace"
        return {"success": True, "observed_destination_node_id": observed}, destination

    monkeypatch.setattr("app_mapper.validation.capture_identified_state", capture)
    monkeypatch.setattr("app_mapper.validation.replay_edge", replay)

    record, run = validate_graph(
        123,
        {},
        tmp_path / "graph",
        tmp_path / "nodes",
        tmp_path / "validations",
        sample_size=1,
        seed=8,
        edge_ids=None,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert calls == ["edge-open", "edge-close"]
    assert record.success is True
    assert record.summary["reliability"] == 1.0
    assert record.results[0].return_verified is True
    assert (run / "validation.json").is_file()


def test_failure_classification_distinguishes_drift_and_stale_locator() -> None:
    assert classify_failure(RuntimeError("reached node-x, expected node-y")) == "drift"
    assert classify_failure(RuntimeError("menu locator was not found")) == "stale_locator"
