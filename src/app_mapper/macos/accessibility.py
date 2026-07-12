from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import ApplicationServices


SAFE_ATTRIBUTES = (
    ApplicationServices.kAXRoleAttribute,
    ApplicationServices.kAXSubroleAttribute,
    ApplicationServices.kAXRoleDescriptionAttribute,
    ApplicationServices.kAXTitleAttribute,
    ApplicationServices.kAXDescriptionAttribute,
    ApplicationServices.kAXHelpAttribute,
    ApplicationServices.kAXIdentifierAttribute,
    ApplicationServices.kAXValueAttribute,
    ApplicationServices.kAXEnabledAttribute,
    ApplicationServices.kAXExpandedAttribute,
    ApplicationServices.kAXSelectedAttribute,
    ApplicationServices.kAXFocusedAttribute,
    ApplicationServices.kAXPositionAttribute,
    ApplicationServices.kAXSizeAttribute,
)


@dataclass
class TraversalState:
    max_depth: int
    max_elements: int
    element_count: int = 0
    truncated: bool = False


def _copy_attribute(element: Any, attribute: str) -> tuple[int, Any]:
    result = ApplicationServices.AXUIElementCopyAttributeValue(element, attribute, None)
    if isinstance(result, tuple) and len(result) == 2:
        return int(result[0]), result[1]
    return 0, result


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, ApplicationServices.AXValueRef):
        value_type = ApplicationServices.AXValueGetType(value)
        success, extracted = ApplicationServices.AXValueGetValue(value, value_type, None)
        if success:
            if value_type == ApplicationServices.kAXValueCGPointType:
                return {"x": float(extracted.x), "y": float(extracted.y)}
            if value_type == ApplicationServices.kAXValueCGSizeType:
                return {"width": float(extracted.width), "height": float(extracted.height)}
            if value_type == ApplicationServices.kAXValueCGRectType:
                return {
                    "x": float(extracted.origin.x),
                    "y": float(extracted.origin.y),
                    "width": float(extracted.size.width),
                    "height": float(extracted.size.height),
                }
            if value_type == ApplicationServices.kAXValueCFRangeType:
                return {"location": int(extracted.location), "length": int(extracted.length)}

    description = str(value)
    if len(description) > 1_000:
        description = description[:997] + "..."
    return {"type": type(value).__name__, "description": description}


def _read_node(element: Any, depth: int, state: TraversalState) -> dict[str, Any] | None:
    if state.element_count >= state.max_elements:
        state.truncated = True
        return None
    state.element_count += 1

    node: dict[str, Any] = {"depth": depth, "attributes": {}, "children": []}
    for attribute in SAFE_ATTRIBUTES:
        error, value = _copy_attribute(element, attribute)
        if error == ApplicationServices.kAXErrorSuccess:
            node["attributes"][str(attribute)] = _serialize_value(value)

    if depth >= state.max_depth:
        error, children = _copy_attribute(element, ApplicationServices.kAXChildrenAttribute)
        if error == ApplicationServices.kAXErrorSuccess and children:
            state.truncated = True
            node["children_truncated"] = True
        return node

    error, children = _copy_attribute(element, ApplicationServices.kAXChildrenAttribute)
    if error != ApplicationServices.kAXErrorSuccess or not children:
        return node

    for child in children:
        child_node = _read_node(child, depth + 1, state)
        if child_node is None:
            break
        node["children"].append(child_node)
    return node


def read_application_tree(pid: int, max_depth: int, max_elements: int) -> dict[str, Any]:
    application = ApplicationServices.AXUIElementCreateApplication(pid)
    state = TraversalState(max_depth=max_depth, max_elements=max_elements)
    root = _read_node(application, depth=0, state=state)
    return {
        "pid": pid,
        "max_depth": max_depth,
        "max_elements": max_elements,
        "element_count": state.element_count,
        "truncated": state.truncated,
        "root": root,
    }


def read_menu_tree(pid: int, max_depth: int, max_elements: int) -> dict[str, Any]:
    application = ApplicationServices.AXUIElementCreateApplication(pid)
    menu_bar = attribute_value(application, ApplicationServices.kAXMenuBarAttribute)
    if menu_bar is None:
        return {
            "pid": pid,
            "max_depth": max_depth,
            "max_elements": max_elements,
            "element_count": 0,
            "truncated": False,
            "root": None,
        }
    state = TraversalState(max_depth=max_depth, max_elements=max_elements)
    root = _read_node(menu_bar, depth=0, state=state)
    return {
        "pid": pid,
        "max_depth": max_depth,
        "max_elements": max_elements,
        "element_count": state.element_count,
        "truncated": state.truncated,
        "root": root,
    }


class AccessibilityActionError(RuntimeError):
    """Raised when a deliberately selected Accessibility action cannot be performed."""


def attribute_value(element: Any, attribute: str) -> Any | None:
    error, value = _copy_attribute(element, attribute)
    if error != ApplicationServices.kAXErrorSuccess:
        return None
    return value


def element_summary(element: Any) -> dict[str, Any]:
    """Return only stable, non-mutating attributes used to identify an element."""
    summary: dict[str, Any] = {}
    for attribute in (
        ApplicationServices.kAXRoleAttribute,
        ApplicationServices.kAXSubroleAttribute,
        ApplicationServices.kAXTitleAttribute,
        ApplicationServices.kAXDescriptionAttribute,
        ApplicationServices.kAXIdentifierAttribute,
        ApplicationServices.kAXEnabledAttribute,
        ApplicationServices.kAXPositionAttribute,
        ApplicationServices.kAXSizeAttribute,
    ):
        value = attribute_value(element, attribute)
        if value is not None:
            summary[str(attribute)] = _serialize_value(value)
    return summary


def _walk_descendants(root: Any, max_elements: int = 2_000) -> list[Any]:
    pending = [root]
    visited: list[Any] = []
    while pending and len(visited) < max_elements:
        element = pending.pop()
        visited.append(element)
        children = attribute_value(element, ApplicationServices.kAXChildrenAttribute) or []
        pending.extend(reversed(list(children)))
    return visited


def find_descendant(
    root: Any,
    predicate: Callable[[dict[str, Any]], bool],
    max_elements: int = 2_000,
) -> tuple[Any, dict[str, Any]] | None:
    for element in _walk_descendants(root, max_elements=max_elements):
        summary = element_summary(element)
        if predicate(summary):
            return element, summary
    return None


def find_menu_item(pid: int, allowed_titles: tuple[str, ...]) -> tuple[Any, dict[str, Any]] | None:
    application = ApplicationServices.AXUIElementCreateApplication(pid)
    menu_bar = attribute_value(application, ApplicationServices.kAXMenuBarAttribute)
    if menu_bar is None:
        return None

    def is_allowed(summary: dict[str, Any]) -> bool:
        return (
            summary.get(str(ApplicationServices.kAXRoleAttribute))
            == ApplicationServices.kAXMenuItemRole
            and summary.get(str(ApplicationServices.kAXTitleAttribute)) in allowed_titles
            and summary.get(str(ApplicationServices.kAXEnabledAttribute), True) is True
        )

    return find_descendant(menu_bar, is_allowed)


def application_windows(pid: int) -> list[tuple[Any, dict[str, Any]]]:
    application = ApplicationServices.AXUIElementCreateApplication(pid)
    windows = attribute_value(application, ApplicationServices.kAXWindowsAttribute) or []
    return [(window, element_summary(window)) for window in windows]


def window_fingerprint(summary: dict[str, Any]) -> str:
    stable_attributes = (
        ApplicationServices.kAXRoleAttribute,
        ApplicationServices.kAXSubroleAttribute,
        ApplicationServices.kAXTitleAttribute,
        ApplicationServices.kAXDescriptionAttribute,
        ApplicationServices.kAXIdentifierAttribute,
        ApplicationServices.kAXPositionAttribute,
        ApplicationServices.kAXSizeAttribute,
    )
    return repr(tuple(summary.get(str(attribute)) for attribute in stable_attributes))


def choose_new_window(
    before: list[tuple[Any, dict[str, Any]]],
    after: list[tuple[Any, dict[str, Any]]],
) -> tuple[Any, dict[str, Any]] | None:
    before_fingerprints = {window_fingerprint(summary) for _, summary in before}
    for window in after:
        if window_fingerprint(window[1]) not in before_fingerprints:
            return window
    return None


def windows_match(
    expected: list[tuple[Any, dict[str, Any]]],
    actual: list[tuple[Any, dict[str, Any]]],
) -> bool:
    return sorted(window_fingerprint(summary) for _, summary in expected) == sorted(
        window_fingerprint(summary) for _, summary in actual
    )


def choose_dismiss_action(window: Any) -> tuple[Any, str, dict[str, Any]] | None:
    """Select only a standard close control or an explicitly harmless dialog button."""

    close_button_element = attribute_value(window, ApplicationServices.kAXCloseButtonAttribute)
    if close_button_element is not None:
        close_summary = element_summary(close_button_element)
        if close_summary.get(str(ApplicationServices.kAXEnabledAttribute), True) is True:
            return close_button_element, ApplicationServices.kAXPressAction, close_summary

    def is_close_button(summary: dict[str, Any]) -> bool:
        return (
            summary.get(str(ApplicationServices.kAXRoleAttribute))
            == ApplicationServices.kAXButtonRole
            and summary.get(str(ApplicationServices.kAXSubroleAttribute))
            == ApplicationServices.kAXCloseButtonSubrole
            and summary.get(str(ApplicationServices.kAXEnabledAttribute), True) is True
        )

    close_button = find_descendant(window, is_close_button)
    if close_button:
        return close_button[0], ApplicationServices.kAXPressAction, close_button[1]

    def is_safe_dialog_button(summary: dict[str, Any]) -> bool:
        return (
            summary.get(str(ApplicationServices.kAXRoleAttribute))
            == ApplicationServices.kAXButtonRole
            and summary.get(str(ApplicationServices.kAXTitleAttribute)) in {"OK", "Close", "Cancel"}
            and summary.get(str(ApplicationServices.kAXEnabledAttribute), True) is True
        )

    button = find_descendant(window, is_safe_dialog_button)
    if button:
        return button[0], ApplicationServices.kAXPressAction, button[1]

    result = ApplicationServices.AXUIElementCopyActionNames(window, None)
    if isinstance(result, tuple) and len(result) == 2:
        error, actions = int(result[0]), result[1]
    else:
        error, actions = ApplicationServices.kAXErrorSuccess, result
    if error == ApplicationServices.kAXErrorSuccess and ApplicationServices.kAXCancelAction in (actions or []):
        return window, ApplicationServices.kAXCancelAction, element_summary(window)
    return None


def perform_action(element: Any, action: str) -> None:
    result = ApplicationServices.AXUIElementPerformAction(element, action)
    error = int(result[0] if isinstance(result, tuple) else result)
    if error != ApplicationServices.kAXErrorSuccess:
        raise AccessibilityActionError(f"Accessibility action {action!r} failed with error {error}")
