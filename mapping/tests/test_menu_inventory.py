from pathlib import Path

from app_mapper.menu_inventory import (
    capture_menu_inventory,
    classify_control,
    controls_from_menu_tree,
)


def _node(role: str, title: str = "", children: list[dict] | None = None, **attrs):
    return {
        "attributes": {"AXRole": role, "AXTitle": title, **attrs},
        "children": children or [],
    }


def _tree() -> dict:
    model_items = [
        _node("AXMenuItem", "Set Editor...", AXEnabled=True, AXIdentifier="doCallback"),
        _node("AXMenuItem", "Delete", AXEnabled=True),
    ]
    analysis_submenu = _node(
        "AXMenuItem",
        "Aero",
        [_node("AXMenu", children=[_node("AXMenuItem", "VSPAERO...", AXEnabled=True)])],
        AXEnabled=True,
    )
    return {
        "root": _node(
            "AXMenuBar",
            children=[
                _node("AXMenuBarItem", "Apple", [_node("AXMenu", children=[])]),
                _node("AXMenuBarItem", "Model", [_node("AXMenu", children=model_items)]),
                _node(
                    "AXMenuBarItem",
                    "Analysis",
                    [_node("AXMenu", children=[analysis_submenu])],
                ),
            ],
        ),
        "truncated": False,
    }


def test_menu_tree_inventory_excludes_system_menu_and_preserves_nested_paths() -> None:
    controls = controls_from_menu_tree(_tree())

    assert [control.path for control in controls] == [
        ["Model", "Set Editor..."],
        ["Model", "Delete"],
        ["Analysis", "Aero"],
        ["Analysis", "Aero", "VSPAERO..."],
    ]
    assert controls[0].policy_decision == "approved"
    assert controls[1].classification == "model_modifying"
    assert controls[2].classification == "submenu"
    assert controls[3].policy_decision == "review_required"


def test_policy_rejects_file_and_exit_and_requires_review_for_view_changes() -> None:
    assert classify_control(("File", "Save..."), enabled=True, has_submenu=False)[1] == "rejected"
    assert classify_control(("OpenVSP", "Quit vsp"), enabled=True, has_submenu=False)[0] == "destructive"
    assert classify_control(("View", "Top"), enabled=True, has_submenu=False)[1] == "review_required"


def test_capture_is_read_only_and_persists_inventory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app_mapper.menu_inventory.read_menu_tree", lambda *_args, **_kwargs: _tree())

    inventory, run = capture_menu_inventory(
        123,
        {"target": {"version": "3.51.0"}},
        tmp_path / "inventories",
        max_depth=8,
        max_elements=200,
    )

    assert inventory.read_only is True
    assert inventory.actions_executed == 0
    assert inventory.summary["total"] == 4
    assert inventory.summary["approved"] == 1
    assert (run / "inventory.json").is_file()
    assert (run / "menu-accessibility.json").is_file()
