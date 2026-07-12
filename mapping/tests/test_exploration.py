from pathlib import Path
from types import SimpleNamespace

from app_mapper.exploration import (
    ExplorationError,
    _check_focus,
    create_exploration,
    load_exploration,
    run_exploration,
)
import pytest
from app_mapper.models import ExplorationBounds


def _mock_runtime(monkeypatch, states: list[str]):
    values = iter(states)

    def capture(_pid, _diagnostics, destination, _registry, _depth, _elements):
        destination.mkdir(parents=True, exist_ok=False)
        return next(values)

    actions: list[tuple[str, str]] = []
    edges: list[tuple[str, str]] = []
    monkeypatch.setattr("app_mapper.exploration.capture_identified_state", capture)
    monkeypatch.setattr("app_mapper.exploration._ensure_runtime", lambda state: state.target_pid)
    monkeypatch.setattr("app_mapper.exploration._check_focus", lambda _state: None)
    monkeypatch.setattr(
        "app_mapper.exploration._open",
        lambda task, _pid, _timeout: actions.append(("open", task.target)),
    )
    monkeypatch.setattr(
        "app_mapper.exploration._close",
        lambda task, _pid, _timeout: actions.append(("close", task.target)),
    )

    def persist(_graph, _registry, source, destination, *_args):
        edges.append((source, destination))
        return SimpleNamespace(edge_id=f"edge-open-{destination}"), SimpleNamespace(
            edge_id=f"edge-close-{destination}"
        )

    monkeypatch.setattr("app_mapper.exploration.persist_edge_pair", persist)
    return actions, edges


def test_pause_and_resume_preserve_queue_and_graph_consistency(
    monkeypatch, tmp_path: Path
) -> None:
    source = "node-workspace"
    actions, edges = _mock_runtime(
        monkeypatch,
        [
            source,  # bootstrap
            source,
            "node-openvsp-menu",
            source,
            source,
            "node-file-menu",
            source,
        ],
    )
    state = create_exploration(
        tmp_path / "explorations",
        tmp_path / "graph",
        tmp_path / "nodes",
        123,
        ExplorationBounds(
            max_depth=1,
            max_nodes=10,
            max_actions=4,
            max_seconds=60,
            max_retries=1,
        ),
    )

    paused = run_exploration(
        state,
        {},
        pause_after_actions=2,
        timeout=1,
        ax_max_depth=2,
        max_elements=20,
    )

    assert paused.status == "paused"
    assert paused.actions_executed == 2
    assert paused.tasks_completed == 1
    assert paused.queue[0].status == "succeeded"
    persisted = load_exploration(Path(paused.run_path))
    assert persisted.queue[0].edge_ids == [
        "edge-open-node-openvsp-menu",
        "edge-close-node-openvsp-menu",
    ]

    resumed = run_exploration(
        persisted,
        {},
        pause_after_actions=None,
        timeout=1,
        ax_max_depth=2,
        max_elements=20,
    )

    assert resumed.status == "stopped_bound"
    assert resumed.stop_reason == "max_actions reached"
    assert resumed.actions_executed == 4
    assert resumed.tasks_completed == 2
    assert actions == [
        ("open", "OpenVSP"),
        ("close", "OpenVSP"),
        ("open", "File"),
        ("close", "File"),
    ]
    assert edges == [
        (source, "node-openvsp-menu"),
        (source, "node-file-menu"),
    ]


def test_node_bound_stops_before_creating_an_extra_state(
    monkeypatch, tmp_path: Path
) -> None:
    source = "node-workspace"
    actions, _ = _mock_runtime(
        monkeypatch,
        [source, source, "node-openvsp-menu", source],
    )
    state = create_exploration(
        tmp_path / "explorations",
        tmp_path / "graph",
        tmp_path / "nodes",
        123,
        ExplorationBounds(
            max_depth=1,
            max_nodes=2,
            max_actions=18,
            max_seconds=60,
            max_retries=0,
        ),
    )

    result = run_exploration(
        state,
        {},
        pause_after_actions=None,
        timeout=1,
        ax_max_depth=2,
        max_elements=20,
    )

    assert result.status == "stopped_bound"
    assert result.stop_reason == "max_nodes reached"
    assert len(result.discovered_node_ids) == 2
    assert actions == [("open", "OpenVSP"), ("close", "OpenVSP")]


def test_focus_change_is_rejected(monkeypatch, tmp_path: Path) -> None:
    state = create_exploration(
        tmp_path / "explorations",
        tmp_path / "graph",
        tmp_path / "nodes",
        123,
        ExplorationBounds(
            max_depth=1,
            max_nodes=2,
            max_actions=2,
            max_seconds=10,
            max_retries=0,
        ),
    )
    state.focus_guard_bundle_id = "com.example.expected"
    monkeypatch.setattr(
        "app_mapper.exploration._frontmost_bundle_id",
        lambda: "com.example.unexpected",
    )

    with pytest.raises(ExplorationError, match="Frontmost application changed"):
        _check_focus(state)
