import json
from pathlib import Path

from PIL import Image, ImageDraw

from app_mapper.identity import (
    AMBIGUOUS_MATCH_THRESHOLD,
    AUTO_MATCH_THRESHOLD,
    identify_observation,
    normalize_accessibility,
    visual_dhash,
    visual_similarity,
)


def _node(
    role: str,
    *,
    subrole: str | None = None,
    title: str | None = None,
    position: dict | None = None,
    children: list[dict] | None = None,
) -> dict:
    attributes = {"AXRole": role, "AXEnabled": True}
    if subrole:
        attributes["AXSubrole"] = subrole
    if title is not None:
        attributes["AXTitle"] = title
    if position:
        attributes["AXPosition"] = position
    return {"attributes": attributes, "children": children or []}


def _accessibility(
    *,
    dialog: bool = False,
    position_x: int = 10,
    button_title: str = "Help",
    expanded_menu: str | None = None,
) -> dict:
    windows = [
        _node(
            "AXWindow",
            subrole="AXStandardWindow",
            title="OpenVSP 3.51.0 - 06/29/26 Unnamed.vsp3",
            position={"x": position_x, "y": 33},
            children=[_node("AXButton", title=button_title)],
        )
    ]
    if dialog:
        windows.append(
            _node(
                "AXWindow",
                subrole="AXDialog",
                title="About vsp",
                children=[_node("AXButton", subrole="AXCloseButton")],
            )
        )
    menu_children = []
    if expanded_menu:
        menu_item = _node("AXMenuBarItem", title=expanded_menu)
        # FLTK exposes an open macOS menu as selected rather than expanded.
        menu_item["attributes"]["AXSelected"] = True
        menu_children.append(menu_item)
    return {
        "application": {"root": _node("AXApplication", children=windows)},
        "menu": {"root": _node("AXMenuBar", children=menu_children)},
    }


def _write_observation(
    root: Path,
    name: str,
    *,
    dialog: bool = False,
    position_x: int = 10,
    visual_mark: bool = False,
    button_title: str = "Help",
    expanded_menu: str | None = None,
) -> Path:
    destination = root / name
    destination.mkdir()
    image = Image.new("RGB", (160, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 154, 94), outline="black", width=2)
    if visual_mark:
        draw.rectangle((140, 10, 145, 15), fill="gray")
    image.save(destination / "screenshot.png")
    (destination / "accessibility.json").write_text(
        json.dumps(
            _accessibility(
                dialog=dialog,
                position_x=position_x,
                button_title=button_title,
                expanded_menu=expanded_menu,
            )
        ),
        encoding="utf-8",
    )
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "checked_at": f"2026-07-11T22:00:0{name[-1]}+00:00",
                "target": {
                    "name": "OpenVSP",
                    "bundle_id": "org.openvsp.OpenVSP",
                    "version": "3.51.0",
                },
            }
        ),
        encoding="utf-8",
    )
    (destination / "windows.json").write_text(
        json.dumps({"windows": []}), encoding="utf-8"
    )
    return destination


def test_normalization_ignores_geometry_and_volatile_title_parts() -> None:
    first = normalize_accessibility(_accessibility(position_x=10))
    second = normalize_accessibility(_accessibility(position_x=900))

    assert first == second
    serialized = json.dumps(first)
    assert "AXPosition" not in serialized
    assert "<version>" in serialized
    assert "<date>" in serialized
    assert "<document>.vsp3" in serialized


def test_visual_hash_tolerates_a_small_transient_change(tmp_path: Path) -> None:
    first = _write_observation(tmp_path, "state1") / "screenshot.png"
    second = _write_observation(tmp_path, "state2", visual_mark=True) / "screenshot.png"

    assert visual_similarity(visual_dhash(first), visual_dhash(second)) > 0.9


def test_repeated_moved_and_returned_states_deduplicate_but_dialog_is_distinct(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "observations"
    registry = tmp_path / "nodes"
    observations.mkdir()
    main_first = _write_observation(observations, "state1")
    main_moved = _write_observation(observations, "state2", position_x=700, visual_mark=True)
    dialog = _write_observation(observations, "state3", dialog=True)
    main_returned = _write_observation(observations, "state4")

    first = identify_observation(main_first, registry)
    moved = identify_observation(main_moved, registry)
    opened = identify_observation(dialog, registry)
    returned = identify_observation(main_returned, registry)

    assert first.status == "new"
    assert moved.status == "matched"
    assert moved.node_id == first.node_id
    assert opened.status == "new"
    assert opened.node_id != first.node_id
    assert returned.status == "matched"
    assert returned.node_id == first.node_id
    index = json.loads((registry / "index.json").read_text())
    assert index["node_count"] == 2
    main_node = json.loads((registry / first.node_id / "node.json").read_text())
    assert main_node["observation_count"] == 3


def test_identifying_same_artifact_twice_is_idempotent(tmp_path: Path) -> None:
    observation = _write_observation(tmp_path, "state1")
    registry = tmp_path / "nodes"

    first = identify_observation(observation, registry)
    second = identify_observation(observation, registry)

    assert second.node_id == first.node_id
    node = json.loads((registry / first.node_id / "node.json").read_text())
    assert node["observation_count"] == 1
    assert AMBIGUOUS_MATCH_THRESHOLD < AUTO_MATCH_THRESHOLD


def test_uncertain_similarity_is_surfaced_without_merging(tmp_path: Path) -> None:
    observations = tmp_path / "observations"
    registry = tmp_path / "nodes"
    observations.mkdir()
    baseline = _write_observation(observations, "state1", button_title="Help")
    uncertain = _write_observation(observations, "state2", button_title="Unknown Panel")

    first = identify_observation(baseline, registry)
    decision = identify_observation(uncertain, registry)

    assert decision.status == "ambiguous"
    assert decision.node_id is None
    assert decision.review_required is True
    assert AMBIGUOUS_MATCH_THRESHOLD <= decision.candidates[0].score < AUTO_MATCH_THRESHOLD
    index = json.loads((registry / "index.json").read_text())
    assert index["node_count"] == 1


def test_expanded_menu_is_distinct_and_cancel_returns_to_workspace(tmp_path: Path) -> None:
    observations = tmp_path / "observations"
    registry = tmp_path / "nodes"
    observations.mkdir()
    workspace = _write_observation(observations, "state1")
    file_menu = _write_observation(observations, "state2", expanded_menu="File")
    view_menu = _write_observation(observations, "state3", expanded_menu="View")
    returned = _write_observation(observations, "state4")

    workspace_decision = identify_observation(workspace, registry)
    file_decision = identify_observation(file_menu, registry)
    view_decision = identify_observation(view_menu, registry)
    returned_decision = identify_observation(returned, registry)

    assert file_decision.status == "new"
    assert file_decision.node_id != workspace_decision.node_id
    assert view_decision.status == "new"
    assert view_decision.node_id not in {workspace_decision.node_id, file_decision.node_id}
    file_node = json.loads((registry / file_decision.node_id / "node.json").read_text())
    view_node = json.loads((registry / view_decision.node_id / "node.json").read_text())
    assert file_node["state_type"] == "menu"
    assert file_node["semantic_name"] == "file menu"
    assert view_node["semantic_name"] == "view menu"
    assert returned_decision.status == "matched"
    assert returned_decision.node_id == workspace_decision.node_id


def test_trusted_manager_paths_keep_structurally_similar_screens_distinct(
    tmp_path: Path,
) -> None:
    first = _write_observation(tmp_path, "state1")
    second = _write_observation(tmp_path, "state2")
    for observation, name in (
        (first, "Window > Background... manager"),
        (second, "View > Adjust... manager"),
    ):
        (observation / "semantic-hint.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "description": f"Trusted {name}",
                    "state_type": "manager",
                }
            ),
            encoding="utf-8",
        )

    first_decision = identify_observation(first, tmp_path / "nodes")
    second_decision = identify_observation(second, tmp_path / "nodes")

    assert first_decision.status == "new"
    assert second_decision.status == "new"
    assert second_decision.node_id != first_decision.node_id
    assert "different trusted manager navigation path" in second_decision.candidates[0].reasons
