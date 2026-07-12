from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.macos.accessibility import read_menu_tree
from app_mapper.models import MenuControl, MenuInventory


TARGET_MENUS = {
    "OpenVSP",
    "File",
    "Edit",
    "Window",
    "View",
    "Model",
    "Analysis",
    "Help",
}

# These exact commands are approved only for a later open-observe-close experiment.
# Approval never authorizes buttons or fields inside the resulting window.
SAFE_DIALOG_PATHS = {
    ("OpenVSP", "About vsp"),
    ("File", "Preferences..."),
    ("Window", "Background..."),
    ("Window", "3D Background..."),
    ("View", "Adjust..."),
    ("Model", "Set Editor..."),
    ("Model", "Variable Presets..."),
    ("Model", "Mode Editor..."),
    ("Model", "Link..."),
    ("Model", "Design Variables..."),
    ("Model", "Measure..."),
    ("Model", "Lighting..."),
    ("Model", "Clipping..."),
    ("Model", "Texture..."),
    ("Model", "Adv Link..."),
    ("Model", "User Parms..."),
    ("Model", "Attribute Explorer..."),
    ("Model", "Vehicle Notes..."),
    ("Model", "Fit Model..."),
    ("Model", "Snap To..."),
}

FILE_TITLES = {
    "New",
    "Open...",
    "Save...",
    "Save As...",
    "Save Set...",
    "Insert...",
    "Import...",
    "Export...",
    "Run Script...",
    "Screenshot...",
    "Print Front Window & Titlebar",
}
MODEL_MODIFYING_TITLES = {
    "Undo Parameter Change",
    "Cut",
    "Copy",
    "Paste",
    "Delete",
    "Select All",
    "Toggle Pick Mode",
}
DESTRUCTIVE_TITLES = {"Exit", "Quit vsp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _control_id(path: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\0".join(path).casefold().encode()).hexdigest()[:12]
    return f"control-{digest}"


def safe_screen_action_keys(path: tuple[str, ...]) -> tuple[str, str]:
    label = "_".join(re.sub(r"[^a-z0-9]+", "_", part.casefold()).strip("_") for part in path)
    digest = hashlib.sha256("\0".join(path).casefold().encode()).hexdigest()[:8]
    return f"open_screen_{label}_{digest}", f"dismiss_screen_{label}_{digest}"


def safe_screen_action_paths() -> dict[str, tuple[str, ...]]:
    actions: dict[str, tuple[str, ...]] = {}
    for path in SAFE_DIALOG_PATHS:
        open_key, close_key = safe_screen_action_keys(path)
        actions[open_key] = path
        actions[close_key] = path
    return actions


def classify_control(
    path: tuple[str, ...], *, enabled: bool, has_submenu: bool
) -> tuple[str, str, list[str]]:
    top, title = path[0], path[-1]
    if has_submenu:
        return "submenu", "not_applicable", ["container exposes nested menu items"]
    if not enabled:
        return "unknown", "rejected", ["control is currently disabled"]
    if path in SAFE_DIALOG_PATHS:
        return (
            "safe_dialog",
            "approved",
            [
                "exact human-reviewed command opens a dialog or manager",
                "later execution is limited to open, observe, and close",
            ],
        )
    if title in DESTRUCTIVE_TITLES:
        return "destructive", "rejected", ["may terminate OpenVSP"]
    if title in FILE_TITLES:
        return "file_operation", "rejected", ["may read, write, or execute external files"]
    if top == "Analysis":
        return (
            "analysis_workflow",
            "review_required",
            ["opens an analysis workflow; running analyses remains forbidden"],
        )
    if top == "Help" or top == "OpenVSP":
        return "external_or_system", "rejected", ["may leave OpenVSP or change application state"]
    if title in MODEL_MODIFYING_TITLES:
        return "model_modifying", "rejected", ["may change model or selection state"]
    if top in {"View", "Window"}:
        return (
            "view_state_change",
            "review_required",
            ["changes viewport state rather than opening a proven reversible window"],
        )
    return "unknown", "review_required", ["no exact reviewed policy entry"]


def controls_from_menu_tree(tree: dict[str, Any]) -> list[MenuControl]:
    controls: list[MenuControl] = []

    def walk(node: dict[str, Any], top: str | None, item_path: tuple[str, ...]) -> None:
        attributes = node.get("attributes", {})
        role = attributes.get("AXRole")
        title = str(attributes.get("AXTitle") or "").strip()
        if role == "AXMenuBarItem":
            top = title if title in TARGET_MENUS else None
            item_path = (top,) if top else ()
        elif role == "AXMenuItem" and top and title:
            path = (*item_path, title)
            has_submenu = any(
                child.get("attributes", {}).get("AXRole") == "AXMenu"
                and child.get("children")
                for child in node.get("children", [])
            )
            enabled = attributes.get("AXEnabled", True) is True
            classification, decision, reasons = classify_control(
                path, enabled=enabled, has_submenu=has_submenu
            )
            controls.append(
                MenuControl(
                    control_id=_control_id(path),
                    path=list(path),
                    top_level_menu=top,
                    title=title,
                    enabled=enabled,
                    identifier=attributes.get("AXIdentifier"),
                    has_submenu=has_submenu,
                    classification=classification,
                    policy_decision=decision,
                    policy_reasons=reasons,
                )
            )
            item_path = path
        for child in node.get("children", []):
            walk(child, top, item_path)

    root = tree.get("root")
    if root:
        walk(root, None, ())
    return controls


def _summary(controls: list[MenuControl]) -> dict[str, int]:
    summary = {"total": len(controls)}
    for decision in ("approved", "review_required", "rejected", "not_applicable"):
        summary[decision] = sum(control.policy_decision == decision for control in controls)
    for classification in (
        "safe_dialog",
        "submenu",
        "view_state_change",
        "model_modifying",
        "file_operation",
        "analysis_workflow",
        "external_or_system",
        "destructive",
        "unknown",
    ):
        summary[classification] = sum(
            control.classification == classification for control in controls
        )
    return summary


def capture_menu_inventory(
    pid: int,
    diagnostics: dict[str, Any],
    output_root: Path,
    max_depth: int,
    max_elements: int,
) -> tuple[MenuInventory, Path]:
    run = create_observation_directory(output_root)
    tree = read_menu_tree(pid, max_depth=max_depth, max_elements=max_elements)
    write_json(run / "menu-accessibility.json", tree)
    controls = controls_from_menu_tree(tree)
    inventory = MenuInventory(
        captured_at=_now(),
        app_version=diagnostics.get("target", {}).get("version"),
        source_path=str((run / "menu-accessibility.json").resolve()),
        truncated=bool(tree.get("truncated")),
        controls=controls,
        summary=_summary(controls),
    )
    write_json(run / "inventory.json", inventory.model_dump(mode="json"))
    return inventory, run
