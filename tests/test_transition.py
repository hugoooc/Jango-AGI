from pathlib import Path

from app_mapper.cli import build_parser
from app_mapper.macos.accessibility import choose_new_window, windows_match
from app_mapper.transition import exercise_about, wait_for


def _window(title: str, subrole: str = "AXStandardWindow"):
    return object(), {
        "AXRole": "AXWindow",
        "AXSubrole": subrole,
        "AXTitle": title,
        "AXSize": "100x100",
    }


def test_new_window_and_return_state_are_detected() -> None:
    source = [_window("Main")]
    destination = [source[0], _window("About OpenVSP")]

    assert choose_new_window(source, destination) == destination[1]
    assert windows_match(source, list(source)) is True
    assert windows_match(source, destination) is False


def test_wait_for_returns_none_after_bounded_timeout() -> None:
    assert wait_for(lambda: None, timeout=0, interval=0) is None


def test_exercise_about_uses_only_selected_targets(monkeypatch, tmp_path: Path) -> None:
    source = [_window("Main")]
    dialog = _window("About OpenVSP", "AXDialog")
    states = iter([source, [source[0], dialog], source])
    actions: list[tuple[object, str]] = []
    menu_element = object()
    dismiss_element = object()

    monkeypatch.setattr("app_mapper.transition.application_windows", lambda _pid: next(states))
    monkeypatch.setattr(
        "app_mapper.transition.find_menu_item",
        lambda _pid, titles: (menu_element, {"AXRole": "AXMenuItem", "AXTitle": titles[0]}),
    )
    monkeypatch.setattr(
        "app_mapper.transition.choose_dismiss_action",
        lambda _window: (dismiss_element, "AXPress", {"AXRole": "AXButton", "AXTitle": "OK"}),
    )
    monkeypatch.setattr(
        "app_mapper.transition.perform_action", lambda element, action: actions.append((element, action))
    )
    monkeypatch.setattr(
        "app_mapper.transition.capture_phase",
        lambda *_args, **_kwargs: {
            "screenshots": ["capture.png"],
            "errors": [],
            "complete": True,
            "visible_window_ids": [1],
        },
    )
    monkeypatch.setattr(
        "app_mapper.transition.list_windows",
        lambda _pid: [
            {
                "window_id": 1,
                "on_screen": True,
                "alpha": 1.0,
                "layer": 0,
                "area": 10_000,
            }
        ],
    )

    trace = exercise_about(123, tmp_path, timeout=0.1, max_depth=2, max_elements=20)

    assert trace["success"] is True
    assert actions == [(menu_element, "AXPress"), (dismiss_element, "AXPress")]
    assert (tmp_path / "trace.json").is_file()


def test_cli_exposes_exercise_about_command() -> None:
    args = build_parser().parse_args(["exercise-about", "--timeout", "3"])
    assert args.command == "exercise-about"
    assert args.timeout == 3
