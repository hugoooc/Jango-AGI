from pathlib import Path
from types import SimpleNamespace

from app_mapper.menu_inventory import safe_screen_action_keys
from app_mapper.models import MenuControl, MenuInventory
from app_mapper.safe_expansion import expand_safe_frontier


def _inventory(path: Path) -> None:
    controls = []
    for index, parts in enumerate(
        [
            ["OpenVSP", "About vsp"],
            ["File", "Preferences..."],
            ["Model", "Geometry..."],
        ]
    ):
        controls.append(
            MenuControl(
                control_id=f"control-{index}",
                path=parts,
                top_level_menu=parts[0],
                title=parts[-1],
                enabled=True,
                classification="safe_dialog",
                policy_decision="approved",
                policy_reasons=["test"],
            )
        )
    inventory = MenuInventory(
        captured_at="2026-07-12T00:00:00+00:00",
        app_version="3.51.0",
        source_path="/menu.json",
        truncated=False,
        controls=controls,
        summary={"total": 3},
    )
    path.write_text(inventory.model_dump_json(), encoding="utf-8")


def test_safe_expansion_captures_round_trip_and_persists_edges(
    monkeypatch, tmp_path: Path
) -> None:
    inventory_path = tmp_path / "inventory.json"
    _inventory(inventory_path)
    states = iter(["node-workspace", "node-preferences", "node-workspace"])

    def capture(_pid, _diagnostics, destination, *_args):
        destination.mkdir(parents=True)
        return next(states)

    actions: list[str] = []
    monkeypatch.setattr("app_mapper.safe_expansion.capture_identified_state", capture)
    monkeypatch.setattr(
        "app_mapper.safe_expansion.application_windows", lambda _pid: [("workspace", {})]
    )
    monkeypatch.setattr(
        "app_mapper.safe_expansion.open_safe_screen",
        lambda *_args: ("preferences", {"AXTitle": "Preferences"}, [("workspace", {})]),
    )
    monkeypatch.setattr(
        "app_mapper.safe_expansion.close_safe_screen",
        lambda *_args: actions.append("closed"),
    )

    def persist(_graph, _registry, source, destination, forward, reverse, _evidence):
        assert source == "node-workspace"
        assert destination == "node-preferences"
        return SimpleNamespace(edge_id=forward.action_key), SimpleNamespace(edge_id=reverse.action_key)

    monkeypatch.setattr("app_mapper.safe_expansion.persist_edge_pair", persist)

    record, run = expand_safe_frontier(
        123,
        {},
        inventory_path,
        tmp_path / "runs",
        tmp_path / "graph",
        tmp_path / "nodes",
        execute=True,
        max_candidates=1,
        timeout=1,
        max_depth=2,
        max_elements=20,
    )

    assert actions == ["closed"]
    assert record.success is True
    assert record.actions_executed == 2
    assert record.summary["already_mapped"] == 1
    assert record.summary["succeeded"] == 1
    succeeded = next(candidate for candidate in record.candidates if candidate.status == "succeeded")
    assert succeeded.destination_node_id == "node-preferences"
    assert succeeded.return_verified is True
    assert (run / "expansion.json").is_file()


def test_safe_screen_action_keys_are_stable_and_directional() -> None:
    first = safe_screen_action_keys(("Model", "Geometry..."))
    second = safe_screen_action_keys(("Model", "Geometry..."))

    assert first == second
    assert first[0].startswith("open_screen_model_geometry_")
    assert first[1].startswith("dismiss_screen_model_geometry_")
