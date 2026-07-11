from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from app_mapper.artifacts import write_json
from app_mapper.models import (
    IdentityDecision,
    MatchCandidate,
    NodeFingerprints,
    NodeObservation,
    NodeRecord,
)


AUTO_MATCH_THRESHOLD = 0.78
AMBIGUOUS_MATCH_THRESHOLD = 0.58
STABLE_ATTRIBUTES = {
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXHelp",
    "AXIdentifier",
    "AXEnabled",
}
INTERACTIVE_ROLES = {
    "axbutton",
    "axcheckbox",
    "axmenubaritem",
    "axmenuitem",
    "axpopupbutton",
    "axradiobutton",
    "axslider",
    "axtab",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", "<date>", value)
    value = re.sub(r"\b\d+\.\d+(?:\.\d+)?\b", "<version>", value)
    value = re.sub(r"\b[^\s/]+\.vsp3\b", "<document>.vsp3", value)
    return " ".join(value.split())


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in sorted(value.items())}
    return value


def _normalize_node(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if node is None:
        return None
    raw_attributes = node.get("attributes", {})
    role = raw_attributes.get("AXRole")
    title = raw_attributes.get("AXTitle")
    if role == "AXMenuBarItem" and title == "Apple":
        return None
    attributes = {
        key: _normalize_value(value)
        for key, value in raw_attributes.items()
        if key in STABLE_ATTRIBUTES and value not in (None, "")
    }
    children = [
        normalized
        for child in node.get("children", [])
        if (normalized := _normalize_node(child)) is not None
    ]
    children.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return {"attributes": attributes, "children": children}


def normalize_accessibility(payload: dict[str, Any]) -> dict[str, Any]:
    if "application" in payload or "menu" in payload:
        application = payload.get("application", {})
        menu = payload.get("menu", {})
        return {
            "application": _normalize_node(application.get("root")),
            "menu": _normalize_node(menu.get("root")),
        }
    return {"application": _normalize_node(payload.get("root")), "menu": None}


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def visual_dhash(path: Path) -> str:
    with Image.open(path) as source:
        pixels = list(
            source.convert("L")
            .resize((9, 8), Image.Resampling.LANCZOS)
            .get_flattened_data()
        )
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def visual_similarity(first: str, second: str) -> float:
    distance = (int(first, 16) ^ int(second, 16)).bit_count()
    return 1.0 - distance / 64.0


def _walk_normalized(node: dict[str, Any] | None):
    if node is None:
        return
    yield node
    for child in node.get("children", []):
        yield from _walk_normalized(child)


def _accessibility_summary(normalized: dict[str, Any]) -> dict[str, Any]:
    landmarks: set[str] = set()
    controls: Counter[str] = Counter()
    windows: list[dict[str, str]] = []
    for root in (normalized.get("application"), normalized.get("menu")):
        for node in _walk_normalized(root):
            attributes = node.get("attributes", {})
            role = str(attributes.get("AXRole", ""))
            subrole = str(attributes.get("AXSubrole", ""))
            label = str(
                attributes.get("AXTitle")
                or attributes.get("AXIdentifier")
                or attributes.get("AXDescription")
                or ""
            )
            if label:
                landmarks.add(f"{role}:{label}")
            if role in INTERACTIVE_ROLES:
                controls[role] += 1
            if role == "axwindow":
                windows.append(
                    {
                        "role": role,
                        "subrole": subrole,
                        "title": str(attributes.get("AXTitle") or ""),
                    }
                )
    windows.sort(key=lambda item: (item["subrole"], item["title"]))
    return {
        "stable_landmarks": sorted(landmarks)[:400],
        "interactive_controls": dict(sorted(controls.items())),
        "windows": windows,
        "window_count": len(windows),
        "dialog_count": sum(window["subrole"] == "axdialog" for window in windows),
    }


def _semantic_summary(
    observation_dir: Path, accessibility: dict[str, Any]
) -> dict[str, Any]:
    interpretation_path = observation_dir / "interpretation.json"
    if interpretation_path.is_file():
        interpretation = _read_json(interpretation_path)
        state = interpretation["state"]
        target_labels = sorted(
            _normalize_text(target["label"])
            for target in interpretation.get("navigation_targets", [])
        )
        return {
            "name": state["title"],
            "description": state["description"],
            "state_type": state["type"],
            "source": "holo",
            "target_labels": target_labels,
        }
    state_type = "dialog" if accessibility["dialog_count"] else "workspace"
    titled_windows = [window["title"] for window in accessibility["windows"] if window["title"]]
    dialog_titles = [
        window["title"]
        for window in accessibility["windows"]
        if window["subrole"] == "axdialog" and window["title"]
    ]
    if state_type == "dialog":
        name = dialog_titles[0] if dialog_titles else "OpenVSP dialog"
    else:
        name = next(
            (title for title in titled_windows if "openvsp" in title),
            titled_windows[0] if titled_windows else "OpenVSP workspace",
        )
    return {
        "name": name,
        "description": f"Deterministic OpenVSP {state_type} state",
        "state_type": state_type,
        "source": "deterministic",
        "target_labels": [],
    }


def build_observation_signals(observation_dir: Path) -> dict[str, Any]:
    screenshot_path = observation_dir / "screenshot.png"
    accessibility_path = observation_dir / "accessibility.json"
    if not screenshot_path.is_file() or not accessibility_path.is_file():
        raise FileNotFoundError(
            "Observation must contain screenshot.png and accessibility.json."
        )
    normalized = normalize_accessibility(_read_json(accessibility_path))
    accessibility = _accessibility_summary(normalized)
    semantic = _semantic_summary(observation_dir, accessibility)
    semantic_signature = {
        "name": _normalize_text(semantic["name"]),
        "state_type": semantic["state_type"],
        "target_labels": semantic["target_labels"],
    }
    manifest_path = observation_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    windows_path = observation_dir / "windows.json"
    windows_payload = _read_json(windows_path) if windows_path.is_file() else {}
    target = manifest.get("target", {})
    observed_at = manifest.get("checked_at") or _now()
    signals = {
        "observation_path": str(observation_dir.resolve()),
        "observed_at": observed_at,
        "application": {
            "name": target.get("name", "OpenVSP"),
            "bundle_id": target.get("bundle_id", "org.openvsp.OpenVSP"),
            "version": target.get("version"),
        },
        "semantic": semantic,
        "window_summary": {
            "accessibility_windows": accessibility["windows"],
            "accessibility_window_count": accessibility["window_count"],
            "dialog_count": accessibility["dialog_count"],
            "representative_geometry": [
                {
                    "title": window.get("title", ""),
                    "bounds": window.get("bounds", {}),
                }
                for window in windows_payload.get("windows", [])
                if window.get("layer") == 0 and window.get("on_screen")
            ],
        },
        "stable_landmarks": accessibility["stable_landmarks"],
        "interactive_controls": accessibility["interactive_controls"],
        "normalized_accessibility": normalized,
        "structural_sha256": _sha256(normalized),
        "visual_dhash": visual_dhash(screenshot_path),
        "semantic_sha256": _sha256(semantic_signature),
    }
    return signals


def _jaccard(first: list[str], second: list[str]) -> float:
    left, right = set(first), set(second)
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def score_candidate(signals: dict[str, Any], node: NodeRecord) -> MatchCandidate:
    reasons: list[str] = []
    current_windows = signals["window_summary"]
    stored_windows = node.window_summary
    if current_windows["accessibility_window_count"] != stored_windows["accessibility_window_count"]:
        reasons.append("different accessibility window count")
    if current_windows["dialog_count"] != stored_windows["dialog_count"]:
        reasons.append("different dialog count")
    if (
        signals["semantic"]["state_type"] != "unknown"
        and node.state_type != "unknown"
        and signals["semantic"]["state_type"] != node.state_type
    ):
        reasons.append("different semantic state type")
    if reasons:
        return MatchCandidate(
            node_id=node.node_id,
            score=0.0,
            compatible=False,
            reasons=reasons,
        )

    structural_exact = signals["structural_sha256"] in node.fingerprints.structural_variants
    visual_score = max(
        visual_similarity(signals["visual_dhash"], candidate)
        for candidate in node.fingerprints.visual_variants
    )
    semantic_exact = signals["semantic_sha256"] in node.fingerprints.semantic_variants
    landmark_score = _jaccard(signals["stable_landmarks"], node.stable_landmarks)
    if structural_exact:
        score = 0.65 + 0.20 * visual_score + 0.15 * float(semantic_exact)
        reasons.append("exact normalized accessibility fingerprint")
    else:
        score = 0.45 * visual_score + 0.35 * landmark_score + 0.20 * float(semantic_exact)
        reasons.append(f"landmark similarity={landmark_score:.3f}")
    reasons.extend(
        [
            f"visual similarity={visual_score:.3f}",
            f"semantic signature {'matches' if semantic_exact else 'differs'}",
        ]
    )
    return MatchCandidate(
        node_id=node.node_id,
        score=round(min(1.0, score), 6),
        compatible=True,
        reasons=reasons,
    )


def _load_nodes(registry_root: Path) -> list[NodeRecord]:
    nodes: list[NodeRecord] = []
    if not registry_root.is_dir():
        return nodes
    for path in sorted(registry_root.glob("node-*/node.json")):
        nodes.append(NodeRecord.model_validate(_read_json(path)))
    return nodes


def _observation_reference(signals: dict[str, Any]) -> NodeObservation:
    return NodeObservation(
        path=signals["observation_path"],
        observed_at=signals["observed_at"],
        structural_sha256=signals["structural_sha256"],
        visual_dhash=signals["visual_dhash"],
        semantic_sha256=signals["semantic_sha256"],
    )


def _new_node_id(signals: dict[str, Any], registry_root: Path) -> str:
    primary = f"node-{signals['structural_sha256'][:12]}"
    if not (registry_root / primary).exists():
        return primary
    identity_material = (
        signals["structural_sha256"]
        + signals["visual_dhash"]
        + signals["semantic_sha256"]
    )
    digest = hashlib.sha256(identity_material.encode("ascii")).hexdigest()
    for length in (12, 16, 20, 24, 32, 64):
        candidate = f"node-{signals['structural_sha256'][:12]}-{digest[:length]}"
        if not (registry_root / candidate).exists():
            return candidate
    raise RuntimeError("Unable to allocate a unique node ID.")


def _create_node(
    signals: dict[str, Any], observation_dir: Path, registry_root: Path
) -> NodeRecord:
    node_id = _new_node_id(signals, registry_root)
    node_dir = registry_root / node_id
    node_dir.mkdir(parents=True, exist_ok=False)
    representative = node_dir / "representative.png"
    shutil.copy2(observation_dir / "screenshot.png", representative)
    observation = _observation_reference(signals)
    node = NodeRecord(
        node_id=node_id,
        semantic_name=signals["semantic"]["name"],
        semantic_description=signals["semantic"]["description"],
        state_type=signals["semantic"]["state_type"],
        semantic_source=signals["semantic"]["source"],
        application=signals["application"],
        window_summary=signals["window_summary"],
        stable_landmarks=signals["stable_landmarks"],
        semantic_landmarks=signals["semantic"]["target_labels"],
        interactive_control_summary=signals["interactive_controls"],
        fingerprints=NodeFingerprints(
            structural_sha256=signals["structural_sha256"],
            visual_dhash=signals["visual_dhash"],
            semantic_sha256=signals["semantic_sha256"],
            structural_variants=[signals["structural_sha256"]],
            visual_variants=[signals["visual_dhash"]],
            semantic_variants=[signals["semantic_sha256"]],
        ),
        representative_screenshot=str(representative.resolve()),
        first_seen=signals["observed_at"],
        last_seen=signals["observed_at"],
        observation_count=1,
        observations=[observation],
    )
    write_json(node_dir / "node.json", node.model_dump(mode="json"))
    return node


def _append_variant(values: list[str], value: str, limit: int = 20) -> list[str]:
    if value not in values:
        values.append(value)
    return values[-limit:]


def _update_node(node: NodeRecord, signals: dict[str, Any], registry_root: Path) -> NodeRecord:
    data = node.model_dump(mode="json")
    data["last_seen"] = signals["observed_at"]
    if not any(
        observation["path"] == signals["observation_path"]
        for observation in data["observations"]
    ):
        data["observation_count"] += 1
        data["observations"].append(_observation_reference(signals).model_dump(mode="json"))
    fingerprints = data["fingerprints"]
    fingerprints["structural_variants"] = _append_variant(
        fingerprints["structural_variants"], signals["structural_sha256"]
    )
    fingerprints["visual_variants"] = _append_variant(
        fingerprints["visual_variants"], signals["visual_dhash"]
    )
    fingerprints["semantic_variants"] = _append_variant(
        fingerprints["semantic_variants"], signals["semantic_sha256"]
    )
    data["stable_landmarks"] = sorted(
        set(data["stable_landmarks"]) | set(signals["stable_landmarks"])
    )[:400]
    data["semantic_landmarks"] = sorted(
        set(data.get("semantic_landmarks", []))
        | set(signals["semantic"]["target_labels"])
    )[:100]
    if data["semantic_source"] == "deterministic" and signals["semantic"]["source"] == "holo":
        data["semantic_name"] = signals["semantic"]["name"]
        data["semantic_description"] = signals["semantic"]["description"]
        data["state_type"] = signals["semantic"]["state_type"]
        data["semantic_source"] = "holo"
    elif data["semantic_source"] == "deterministic":
        data["semantic_name"] = signals["semantic"]["name"]
        data["semantic_description"] = signals["semantic"]["description"]
    updated = NodeRecord.model_validate(data)
    write_json(
        registry_root / node.node_id / "node.json",
        updated.model_dump(mode="json"),
    )
    return updated


def _write_index(registry_root: Path) -> None:
    nodes = _load_nodes(registry_root)
    write_json(
        registry_root / "index.json",
        {
            "schema_version": 1,
            "node_count": len(nodes),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "semantic_name": node.semantic_name,
                    "state_type": node.state_type,
                    "observation_count": node.observation_count,
                    "last_seen": node.last_seen,
                }
                for node in nodes
            ],
        },
    )


def identify_observation(
    observation_dir: Path, registry_root: Path
) -> IdentityDecision:
    observation_dir = observation_dir.resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    signals = build_observation_signals(observation_dir)
    write_json(
        observation_dir / "normalized-accessibility.json",
        signals["normalized_accessibility"],
    )
    write_json(
        observation_dir / "fingerprints.json",
        {
            "structural_sha256": signals["structural_sha256"],
            "visual_dhash": signals["visual_dhash"],
            "semantic_sha256": signals["semantic_sha256"],
            "semantic": signals["semantic"],
            "window_summary": signals["window_summary"],
            "stable_landmarks": signals["stable_landmarks"],
            "interactive_control_summary": signals["interactive_controls"],
        },
    )
    candidates = sorted(
        (score_candidate(signals, node) for node in _load_nodes(registry_root)),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    top = candidates[0] if candidates else None
    if top is not None and top.compatible and top.score >= AUTO_MATCH_THRESHOLD:
        existing = next(node for node in _load_nodes(registry_root) if node.node_id == top.node_id)
        node = _update_node(existing, signals, registry_root)
        status, node_id, review_required = "matched", node.node_id, False
    elif top is not None and top.compatible and top.score >= AMBIGUOUS_MATCH_THRESHOLD:
        status, node_id, review_required = "ambiguous", None, True
    else:
        node = _create_node(signals, observation_dir, registry_root)
        status, node_id, review_required = "new", node.node_id, False
    decision = IdentityDecision(
        status=status,
        node_id=node_id,
        observation_path=str(observation_dir),
        candidates=candidates[:10],
        review_required=review_required,
    )
    write_json(observation_dir / "identity.json", decision.model_dump(mode="json"))
    _write_index(registry_root)
    return decision
