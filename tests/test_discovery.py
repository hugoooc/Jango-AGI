import json
from pathlib import Path

from app_mapper.discovery import build_candidates, discover_one_hop


def _interpretation(path: Path) -> Path:
    payload = {
        "navigation_targets": [
            {
                "label": "POD dropdown",
                "confidence": 0.9,
                "bounding_box": [10, 10, 20, 20],
                "accessibility_match": None,
            }
        ],
        "rejected_targets": [
            {
                "reason": "policy term=add",
                "target": {
                    "label": "Add",
                    "confidence": 0.8,
                    "bounding_box": [30, 10, 40, 20],
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_holo_candidates_are_recorded_but_only_exact_human_allowlist_is_approved(
    tmp_path: Path,
) -> None:
    candidates = build_candidates(_interpretation(tmp_path / "interpretation.json"))

    holo = [candidate for candidate in candidates if candidate.origin == "holo"]
    approved = [candidate for candidate in candidates if candidate.policy_decision == "approved"]
    assert all(candidate.status == "rejected" for candidate in holo)
    assert [candidate.candidate_id for candidate in approved] == [
        "menu-file",
        "menu-view",
        "menu-model",
    ]
    assert all(candidate.classification == "menu" for candidate in approved)


def test_plan_mode_never_executes_actions(tmp_path: Path) -> None:
    record, run = discover_one_hop(
        0,
        {},
        tmp_path / "runs",
        tmp_path / "graph",
        tmp_path / "nodes",
        _interpretation(tmp_path / "interpretation.json"),
        execute=False,
        max_candidates=2,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert record.mode == "plan"
    assert record.actions_executed == 0
    assert record.success is True
    assert record.summary["proposed"] == 2
    assert record.summary["skipped"] == 1
    assert (run / "discovery.json").is_file()


def test_execute_explores_one_at_a_time_and_restores_source(
    monkeypatch, tmp_path: Path
) -> None:
    source = "node-workspace"
    states = iter(
        [
            source,
            "node-file-menu",
            source,
            source,
            "node-view-menu",
            source,
        ]
    )

    def capture(_pid, _diagnostics, destination, _registry, _depth, _elements):
        destination.mkdir(parents=True, exist_ok=False)
        return next(states)

    actions: list[tuple[str, str]] = []
    edges: list[tuple[str, str]] = []
    monkeypatch.setattr("app_mapper.discovery.capture_identified_state", capture)
    monkeypatch.setattr(
        "app_mapper.discovery._open_menu",
        lambda _pid, title, _timeout: actions.append(("open", title)),
    )
    monkeypatch.setattr(
        "app_mapper.discovery._close_menu",
        lambda _pid, title, _timeout: actions.append(("close", title)),
    )
    monkeypatch.setattr(
        "app_mapper.discovery.persist_edge_pair",
        lambda _graph, _registry, source_id, destination_id, *_args: edges.append(
            (source_id, destination_id)
        ),
    )

    record, run = discover_one_hop(
        123,
        {},
        tmp_path / "runs",
        tmp_path / "graph",
        tmp_path / "nodes",
        None,
        execute=True,
        max_candidates=2,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert record.success is True
    assert record.source_node_id == source
    assert record.actions_executed == 4
    assert actions == [
        ("open", "File"),
        ("close", "File"),
        ("open", "View"),
        ("close", "View"),
    ]
    assert edges == [(source, "node-file-menu"), (source, "node-view-menu")]
    assert record.summary["succeeded"] == 2
    assert record.summary["skipped"] == 1
    for candidate in record.candidates:
        if candidate.status == "succeeded":
            assert candidate.return_verified is True
            assert Path(candidate.trace_path).is_file()
    assert (run / "discovery.json").is_file()
