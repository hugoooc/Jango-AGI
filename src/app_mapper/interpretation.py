from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app_mapper.artifacts import write_json
from app_mapper.macos.accessibility import read_application_tree, read_menu_tree
from app_mapper.macos.applications import list_windows
from app_mapper.macos.screenshots import capture_window_composite
from app_mapper.models import HoloInterpretation, RejectedTarget, ValidatedInterpretation


UNSAFE_LABEL_TERMS = {
    "add",
    "copy",
    "cut",
    "delete",
    "exit",
    "export",
    "import",
    "insert",
    "new",
    "open",
    "paste",
    "quit",
    "run script",
    "save",
    "select",
    "show",
    "show only",
    "noshow",
}


def _eligible_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        window
        for window in windows
        if window["on_screen"]
        and window["alpha"] > 0
        and window["layer"] == 0
        and window["area"] > 2_500
    ]


def compact_accessibility(
    application_tree: dict[str, Any],
    menu_tree: dict[str, Any],
    max_landmarks: int = 350,
) -> dict[str, Any]:
    landmarks: list[dict[str, Any]] = []
    useful_roles = {
        "AXWindow",
        "AXButton",
        "AXMenuBarItem",
        "AXMenuItem",
        "AXTabGroup",
        "AXCheckBox",
    }

    def visit(node: dict[str, Any] | None, source: str) -> None:
        if node is None or len(landmarks) >= max_landmarks:
            return
        attributes = node.get("attributes", {})
        role = attributes.get("AXRole")
        title = attributes.get("AXTitle")
        description = attributes.get("AXDescription")
        identifier = attributes.get("AXIdentifier")
        if source == "menu" and role == "AXMenuBarItem" and title == "Apple":
            return
        if role in useful_roles or title or description or identifier:
            landmark = {
                key: attributes[key]
                for key in (
                    "AXRole",
                    "AXSubrole",
                    "AXTitle",
                    "AXDescription",
                    "AXHelp",
                    "AXIdentifier",
                    "AXEnabled",
                    "AXPosition",
                    "AXSize",
                )
                if key in attributes
            }
            landmark["source"] = source
            landmarks.append(landmark)
        for child in node.get("children", []):
            visit(child, source)

    visit(menu_tree.get("root"), "menu")
    visit(application_tree.get("root"), "application")
    return {
        "landmarks": landmarks,
        "landmark_count": len(landmarks),
        "truncated": (
            len(landmarks) >= max_landmarks
            or bool(application_tree.get("truncated"))
            or bool(menu_tree.get("truncated"))
        ),
    }


def add_normalized_boxes(
    context: dict[str, Any], capture_bounds: dict[str, float]
) -> dict[str, Any]:
    for landmark in context["landmarks"]:
        position = landmark.get("AXPosition")
        size = landmark.get("AXSize")
        if not isinstance(position, dict) or not isinstance(size, dict):
            continue
        if not {"x", "y"} <= position.keys() or not {"width", "height"} <= size.keys():
            continue
        left = (position["x"] - capture_bounds["x"]) / capture_bounds["width"] * 1_000
        top = (position["y"] - capture_bounds["y"]) / capture_bounds["height"] * 1_000
        right = left + size["width"] / capture_bounds["width"] * 1_000
        bottom = top + size["height"] / capture_bounds["height"] * 1_000
        if right <= 0 or bottom <= 0 or left >= 1_000 or top >= 1_000:
            continue
        normalized = [
            max(0, min(1_000, round(left))),
            max(0, min(1_000, round(top))),
            max(0, min(1_000, round(right))),
            max(0, min(1_000, round(bottom))),
        ]
        if normalized[0] < normalized[2] and normalized[1] < normalized[3]:
            landmark["normalized_bounding_box"] = normalized
    context["coordinate_space"] = {
        "bounding_boxes": "[left, top, right, bottom] normalized to screenshot in [0, 1000]",
        "capture_bounds_screen_points": capture_bounds,
    }
    return context


def capture_interpretation_observation(
    pid: int,
    destination: Path,
    max_depth: int,
    max_elements: int,
) -> tuple[Path, dict[str, Any]]:
    windows = list_windows(pid)
    eligible = _eligible_windows(windows)
    screenshot = destination / "screenshot.png"
    bounds, component_images = capture_window_composite(eligible, screenshot)
    application_tree = read_application_tree(pid, max_depth, max_elements)
    menu_tree = read_menu_tree(pid, max_depth, max_elements)
    context = add_normalized_boxes(compact_accessibility(application_tree, menu_tree), bounds)
    write_json(
        destination / "windows.json",
        {
            "pid": pid,
            "windows": windows,
            "capture_bounds": bounds,
            "component_images": component_images,
        },
    )
    write_json(
        destination / "accessibility.json",
        {"application": application_tree, "menu": menu_tree, "compact_context": context},
    )
    return screenshot, context


def validate_and_filter(content: str, min_confidence: float) -> ValidatedInterpretation:
    parsed = HoloInterpretation.model_validate_json(content)
    accepted = []
    rejected: list[RejectedTarget] = []
    for target in parsed.navigation_targets:
        normalized_label = re.sub(r"[^a-z]+", " ", target.label.casefold()).strip()
        unsafe_term = next(
            (
                term
                for term in UNSAFE_LABEL_TERMS
                if normalized_label == term or normalized_label.startswith(term + " ")
            ),
            None,
        )
        if target.risk != "safe_navigation":
            rejected.append(RejectedTarget(target=target, reason=f"risk={target.risk}"))
        elif unsafe_term is not None:
            rejected.append(RejectedTarget(target=target, reason=f"policy term={unsafe_term}"))
        elif target.confidence < min_confidence:
            rejected.append(
                RejectedTarget(
                    target=target,
                    reason=f"confidence={target.confidence} below {min_confidence}",
                )
            )
        else:
            accepted.append(target)
    return ValidatedInterpretation(
        state=parsed.state,
        navigation_targets=accepted,
        rejected_targets=rejected,
    )
