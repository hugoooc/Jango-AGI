from pathlib import Path

from app_mapper.config import OPENVSP
from app_mapper.macos.applications import choose_primary_window, inspect_bundle


def test_inspect_installed_openvsp_bundle() -> None:
    bundle = inspect_bundle(OPENVSP)

    assert bundle["installed"] is True
    assert bundle["bundle_id"] == "org.openvsp.OpenVSP"
    assert bundle["version"] == "3.51.0"


def test_choose_primary_window_prefers_largest_eligible_window() -> None:
    windows = [
        {"window_id": 1, "title": "small", "on_screen": True, "alpha": 1.0, "layer": 0, "area": 20_000},
        {"window_id": 2, "title": "main", "on_screen": True, "alpha": 1.0, "layer": 0, "area": 200_000},
        {"window_id": 3, "title": "overlay", "on_screen": True, "alpha": 1.0, "layer": 2, "area": 500_000},
    ]

    assert choose_primary_window(windows)["window_id"] == 2


def test_bundle_path_is_stable() -> None:
    assert OPENVSP.bundle_path == Path("/Applications/OpenVSP.app")

