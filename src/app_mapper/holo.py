from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from app_mapper.models import HoloInterpretation


DEFAULT_HOLO_BASE_URL = "https://api.hcompany.ai/v1/"
DEFAULT_HOLO_MODEL = "holo3-1-35b-a3b"


class HoloConfigurationError(RuntimeError):
    pass


class HoloResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class HoloSettings:
    api_key: str
    base_url: str = DEFAULT_HOLO_BASE_URL
    model: str = DEFAULT_HOLO_MODEL
    timeout: float = 60.0

    @classmethod
    def from_environment(
        cls,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> "HoloSettings":
        api_key = os.environ.get("HAI_API_KEY", "").strip()
        if not api_key:
            raise HoloConfigurationError(
                "HAI_API_KEY is not set. Create a Portal-H key and export it before interpreting."
            )
        return cls(
            api_key=api_key,
            base_url=(base_url or os.environ.get("HOLO_BASE_URL") or DEFAULT_HOLO_BASE_URL).strip(),
            model=(model or os.environ.get("HOLO_MODEL") or DEFAULT_HOLO_MODEL).strip(),
            timeout=timeout,
        )

    def public_dict(self) -> dict[str, Any]:
        return {"base_url": self.base_url, "model": self.model, "timeout": self.timeout}


def _png_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise HoloConfigurationError("Holo input must be a valid PNG screenshot.")
    return struct.unpack(">II", image_bytes[16:24])


def build_request(
    image_path: Path,
    accessibility_context: dict[str, Any],
    settings: HoloSettings,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    image_bytes = image_path.read_bytes()
    width, height = _png_dimensions(image_bytes)
    schema = HoloInterpretation.model_json_schema()
    system = (
        "You are a read-only desktop UI analyst. Describe the visible OpenVSP state and identify "
        "real navigation controls. Do not operate the UI and do not recommend typing, editing geometry, "
        "saving, opening files, importing, exporting, quitting, or destructive actions. Classify every "
        "candidate's risk honestly. Bounding boxes are [left, top, right, bottom] integers normalized to "
        "[0, 1000] relative to the exact supplied image. Use accessibility evidence when it agrees with "
        "the image. Return only the required JSON object."
        f"\n\n<output_format>\n```json\n{json.dumps(schema, sort_keys=True)}\n```\n</output_format>"
    )
    context_text = json.dumps(accessibility_context, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<observation>\nOpenVSP accessibility landmarks:\n" + context_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                {"type": "text", "text": "\n</observation>"},
            ],
        },
    ]
    extra_body = {
        "structured_outputs": {"json": schema},
        "chat_template_kwargs": {"enable_thinking": True},
    }
    request_record = {
        **settings.public_dict(),
        "image": {
            "path": image_path.name,
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
        },
        "accessibility_context": accessibility_context,
        "schema": schema,
        "read_only": True,
    }
    return messages, extra_body, request_record


def request_interpretation(
    settings: HoloSettings,
    image_path: Path,
    accessibility_context: dict[str, Any],
    *,
    client: Any | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    messages, extra_body, request_record = build_request(
        image_path, accessibility_context, settings
    )
    api = client or OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
    )
    response = api.chat.completions.create(
        model=settings.model,
        messages=messages,
        temperature=0.2,
        extra_body=extra_body,
    )
    raw = response.model_dump(mode="json")
    if not response.choices:
        raise HoloResponseError("Holo returned no choices.")
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise HoloResponseError("Holo returned no structured content.")
    return raw, content, request_record
