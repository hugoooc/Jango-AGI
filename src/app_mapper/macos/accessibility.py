from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

