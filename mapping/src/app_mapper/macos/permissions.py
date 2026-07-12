from __future__ import annotations

from typing import Any

import ApplicationServices
import Quartz


def permission_status() -> dict[str, Any]:
    return {
        "screen_recording": {
            "granted": bool(Quartz.CGPreflightScreenCaptureAccess()),
            "settings_location": "System Settings > Privacy & Security > Screen & System Audio Recording",
        },
        "accessibility": {
            "granted": bool(ApplicationServices.AXIsProcessTrusted()),
            "settings_location": "System Settings > Privacy & Security > Accessibility",
        },
        "note": "These are preflight checks only; they do not request permission or control another app.",
    }

