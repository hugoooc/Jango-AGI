from pathlib import Path
from types import SimpleNamespace

from app_mapper.recursive_exploration import (
    TABS,
    curated_tab_actions,
    explore_recursive_tabs,
)


def test_recursive_plan_exposes_only_curated_navigation_tabs(tmp_path: Path) -> None:
    record, run = explore_recursive_tabs(
        0,
        {},
        tmp_path / "runs",
        tmp_path / "graph",
        tmp_path / "nodes",
        execute=False,
        max_actions=6,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert record.success is True
    assert record.actions_executed == 0
    assert [candidate.label for candidate in record.candidates] == ["Group", "Settings"]
    assert all(candidate.depth == 2 for candidate in record.candidates)
    assert (run / "recursive.json").is_file()


def test_recursive_execution_records_depth_two_round_trips(monkeypatch, tmp_path: Path) -> None:
    states = iter(
        [
            "workspace",
            "variable-presets",
            "group-tab",
            "variable-presets",
            "settings-tab",
            "variable-presets",
            "workspace",
        ]
    )

    def capture(_pid, _diagnostics, destination, *_args):
        destination.mkdir(parents=True)
        return next(states)

    windows = iter(
        [
            [{"window_id": 1, "on_screen": True, "title": "workspace", "area": 10}],
            [
                {"window_id": 1, "on_screen": True, "title": "workspace", "area": 10},
                {
                    "window_id": 2,
                    "on_screen": True,
                    "title": "Variable Presets",
                    "area": 20,
                    "bounds": {"x": 0, "y": 0, "width": 400, "height": 780},
                },
            ],
        ]
    )
    clicks: list[tuple[int, int]] = []
    edges: list[tuple[str, str]] = []
    monkeypatch.setattr("app_mapper.recursive_exploration.capture_identified_state", capture)
    monkeypatch.setattr("app_mapper.recursive_exploration.list_windows", lambda _pid: next(windows))
    monkeypatch.setattr(
        "app_mapper.recursive_exploration.open_safe_screen",
        lambda *_args: ("manager", {}, [("workspace", {})]),
    )
    monkeypatch.setattr("app_mapper.recursive_exploration.close_safe_screen", lambda *_args: None)
    monkeypatch.setattr(
        "app_mapper.recursive_exploration._click_normalized",
        lambda _window, point: clicks.append(point),
    )

    def persist(_graph, _registry, source, destination, forward, reverse, _evidence):
        edges.append((source, destination))
        return SimpleNamespace(edge_id=forward.action_key), SimpleNamespace(edge_id=reverse.action_key)

    monkeypatch.setattr("app_mapper.recursive_exploration.persist_edge_pair", persist)

    record, _ = explore_recursive_tabs(
        123,
        {},
        tmp_path / "runs",
        tmp_path / "graph",
        tmp_path / "nodes",
        execute=True,
        max_actions=6,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert record.success is True
    assert record.actions_executed == 6
    assert record.summary["succeeded"] == 2
    assert edges == [("variable-presets", "group-tab"), ("variable-presets", "settings-tab")]
    assert clicks == [TABS[0]["point"], (63, 85), TABS[1]["point"], (63, 85)]


def test_curated_tab_registry_has_source_and_destination_hints() -> None:
    actions = curated_tab_actions()
    assert set(actions) == {
        "select_tab_variable_presets_group",
        "select_tab_variable_presets_apply_from_group",
        "select_tab_variable_presets_settings",
        "select_tab_variable_presets_apply_from_settings",
    }
    assert actions["select_tab_variable_presets_group"]["destination_hint"]["name"].endswith(
        "Group tab"
    )
