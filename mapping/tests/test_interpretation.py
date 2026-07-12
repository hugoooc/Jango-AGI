import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app_mapper.holo import (
    HoloConfigurationError,
    HoloSettings,
    build_request,
    request_interpretation,
)
from app_mapper.interpretation import (
    add_normalized_boxes,
    validate_and_filter,
)
from app_mapper.macos.screenshots import capture_window_composite


def _target(
    label: str,
    *,
    risk: str = "safe_navigation",
    confidence: float = 0.9,
    box: list[int] | None = None,
) -> dict:
    return {
        "label": label,
        "action_type": "click",
        "bounding_box": box or [10, 20, 100, 80],
        "expected_destination": f"The {label} navigation destination",
        "risk": risk,
        "confidence": confidence,
        "evidence": "hybrid",
        "accessibility_match": label,
    }


def _response(targets: list[dict]) -> str:
    return json.dumps(
        {
            "state": {
                "title": "OpenVSP Main Window",
                "type": "workspace",
                "description": "Blank vehicle workspace",
            },
            "navigation_targets": targets,
        }
    )


def test_policy_accepts_safe_targets_and_rejects_unsafe_or_uncertain() -> None:
    result = validate_and_filter(
        _response(
            [
                _target("Help"),
                _target("Save As", risk="safe_navigation"),
                _target("Exit", risk="destructive"),
                _target("View", confidence=0.2),
            ]
        ),
        min_confidence=0.5,
    )

    assert [target.label for target in result.navigation_targets] == ["Help"]
    assert [item.target.label for item in result.rejected_targets] == ["Save As", "Exit", "View"]
    assert result.actions_executed == 0


def test_invalid_or_out_of_range_box_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_and_filter(_response([_target("Help", box=[0, 0, 1001, 10])]), 0.5)
    with pytest.raises(ValidationError):
        validate_and_filter(_response([_target("Help", box=[10, 10, 5, 20])]), 0.5)


def test_settings_require_environment_key(monkeypatch) -> None:
    monkeypatch.delenv("HAI_API_KEY", raising=False)
    with pytest.raises(HoloConfigurationError):
        HoloSettings.from_environment()


def test_accessibility_boxes_are_normalized_to_exact_capture() -> None:
    context = {
        "landmarks": [
            {
                "AXPosition": {"x": 20.0, "y": 40.0},
                "AXSize": {"width": 20.0, "height": 10.0},
            }
        ]
    }

    add_normalized_boxes(
        context,
        {"x": 10.0, "y": 20.0, "width": 100.0, "height": 100.0},
    )

    assert context["landmarks"][0]["normalized_bounding_box"] == [100, 200, 300, 300]


def test_composite_uses_distinct_openvsp_windows_and_deduplicates_aliases(
    monkeypatch, tmp_path: Path
) -> None:
    windows = [
        {
            "window_id": 1,
            "area": 1_000,
            "bounds": {"x": 50.0, "y": 0.0, "width": 20.0, "height": 50.0},
        },
        {
            "window_id": 2,
            "area": 2_000,
            "bounds": {"x": 0.0, "y": 10.0, "width": 50.0, "height": 40.0},
        },
        {
            "window_id": 3,
            "area": 2_500,
            "bounds": {"x": 0.0, "y": 0.0, "width": 50.0, "height": 50.0},
        },
    ]

    def fake_capture(window_id: int, destination: Path) -> None:
        if window_id == 1:
            Image.new("RGBA", (40, 100), (0, 0, 255, 255)).save(destination)
        else:
            Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(destination)

    monkeypatch.setattr("app_mapper.macos.screenshots.capture_window", fake_capture)
    destination = tmp_path / "screenshot.png"

    bounds, components = capture_window_composite(windows, destination)

    assert bounds == {"x": 0.0, "y": 0.0, "width": 70.0, "height": 50.0}
    assert components == ["window-1.png", "window-3.png"]
    with Image.open(destination) as composite:
        assert composite.size == (140, 100)
        assert composite.getpixel((120, 50)) == (0, 0, 255)


def test_request_embeds_image_schema_and_context_without_recording_key(tmp_path: Path) -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    image = tmp_path / "screenshot.png"
    image.write_bytes(png)
    settings = HoloSettings(api_key="secret", model="test-model")

    messages, extra_body, record = build_request(
        image, {"landmarks": [{"AXTitle": "Help"}]}, settings
    )

    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "structured_outputs" in extra_body
    assert record["image"]["width"] == 1
    assert record["model"] == "test-model"
    assert "secret" not in json.dumps(record)

    calls = []

    class FakeResponse:
        choices = [SimpleNamespace(message=SimpleNamespace(content=_response([_target("Help")])))]

        def model_dump(self, mode: str) -> dict:
            assert mode == "json"
            return {"id": "response-1"}

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: calls.append(kwargs) or FakeResponse()
            )
        )
    )
    raw, content, _ = request_interpretation(
        settings,
        image,
        {"landmarks": [{"AXTitle": "Help"}]},
        client=fake_client,
    )

    assert raw == {"id": "response-1"}
    assert json.loads(content)["state"]["type"] == "workspace"
    assert calls[0]["model"] == "test-model"
    assert "structured_outputs" in calls[0]["extra_body"]
