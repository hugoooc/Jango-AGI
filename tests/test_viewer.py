from pathlib import Path

from app_mapper.graph import persist_edge_pair
from app_mapper.models import (
    EdgeAction,
    EdgeEvidence,
    NodeFingerprints,
    NodeObservation,
    NodeRecord,
)
from app_mapper.viewer import build_viewer_data, generate_viewer


def _node(registry: Path, node_id: str, state_type: str, screenshot: Path) -> None:
    directory = registry / node_id
    directory.mkdir(parents=True)
    observed_at = "2026-07-12T00:00:00+00:00"
    observation = NodeObservation(
        path="/observation",
        observed_at=observed_at,
        structural_sha256=("a" if state_type == "workspace" else "c") * 64,
        visual_dhash="0" * 16,
        semantic_sha256=("b" if state_type == "workspace" else "d") * 64,
    )
    record = NodeRecord(
        node_id=node_id,
        semantic_name=f"OpenVSP {state_type}",
        semantic_description=f"A {state_type} state",
        state_type=state_type,
        semantic_source="deterministic",
        application={"name": "OpenVSP"},
        window_summary={},
        stable_landmarks=[],
        interactive_control_summary={"axbutton": 1},
        fingerprints=NodeFingerprints(
            structural_sha256=observation.structural_sha256,
            visual_dhash=observation.visual_dhash,
            semantic_sha256=observation.semantic_sha256,
            structural_variants=[observation.structural_sha256],
            visual_variants=[observation.visual_dhash],
            semantic_variants=[observation.semantic_sha256],
        ),
        representative_screenshot=str(screenshot),
        first_seen=observed_at,
        last_seen=observed_at,
        observation_count=1,
        observations=[observation],
    )
    (directory / "node.json").write_text(record.model_dump_json(), encoding="utf-8")


def test_viewer_embeds_graph_metadata_and_screenshots(tmp_path: Path) -> None:
    graph_root, registry = tmp_path / "graph", tmp_path / "nodes"
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    _node(registry, "node-workspace", "workspace", screenshot)
    _node(registry, "node-menu", "menu", screenshot)
    open_action = EdgeAction(
        action_key="open_menu_file",
        semantic_description="Open File menu",
        accessibility_locator={"role": "AXMenuBarItem", "title": "File"},
        preconditions=["workspace"],
        expected_postconditions=["menu"],
        reverse_action_key="dismiss_menu_file",
    )
    close_action = EdgeAction(
        action_key="dismiss_menu_file",
        semantic_description="Dismiss File menu",
        accessibility_locator={"role": "AXMenuBarItem", "title": "File"},
        preconditions=["menu"],
        expected_postconditions=["workspace"],
        reverse_action_key="open_menu_file",
    )
    evidence = EdgeEvidence(
        recorded_at="2026-07-12T00:00:00+00:00",
        run_path="/run",
        source_observation="/source",
        destination_observation="/destination",
        returned_observation="/returned",
    )
    persist_edge_pair(
        graph_root,
        registry,
        "node-workspace",
        "node-menu",
        open_action,
        close_action,
        evidence,
    )

    data = build_viewer_data(
        graph_root,
        registry,
        tmp_path / "discovery",
        tmp_path / "explorations",
        tmp_path / "validations",
    )
    destination = generate_viewer(
        tmp_path / "viewer",
        graph_root,
        registry,
        tmp_path / "discovery",
        tmp_path / "explorations",
        tmp_path / "validations",
    )

    assert data["metrics"]["mapped_nodes"] == 2
    assert data["metrics"]["directed_edges"] == 2
    assert data["nodes"][0]["screenshot_data_uri"].startswith("data:image/png;base64,")
    html = destination.read_text(encoding="utf-8")
    assert "OpenVSP Navigation Graph" in html
    assert "data:image/png;base64," in html
    assert "preconditions.join('\\n')" in html
    assert "preconditions.join('\n" not in html
    assert (destination.parent / "viewer-data.json").is_file()
