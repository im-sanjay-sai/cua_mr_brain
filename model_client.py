from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass
from typing import Any, Literal

try:
    from .coordinate_utils import Box, denormalize_box, denormalize_point, normalize_box, point_to_model_box
except ImportError:
    from coordinate_utils import Box, denormalize_box, denormalize_point, normalize_box, point_to_model_box


DEFAULT_MODEL = "tzafon.northstar-cua-fast"
DEFAULT_BASE_URL = "https://api.tzafon.ai/v1"
Mode = Literal["box_tool", "computer_action"]


@dataclass
class LocalizationResult:
    mode: str
    answer: str
    label: str = ""
    confidence: float | None = None
    model_box: Box | None = None
    pixel_box: Box | None = None
    model_point: tuple[int, int] | None = None
    pixel_point: tuple[int, int] | None = None
    raw: str = ""


def localize_region(
    image: Any,
    question: str,
    *,
    mode: Mode = "box_tool",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LocalizationResult:
    api_key = api_key or os.getenv("TZAFON_API_KEY")
    if not api_key:
        raise RuntimeError("Set TZAFON_API_KEY or paste an API key in the app.")

    base_url = base_url or os.getenv("TZAFON_BASE_URL", DEFAULT_BASE_URL)
    model = model or os.getenv("TZAFON_MODEL", DEFAULT_MODEL)

    if mode == "computer_action":
        return _localize_with_computer_action(image, question, api_key=api_key, base_url=base_url, model=model)
    return _localize_with_box_tool(image, question, api_key=api_key, base_url=base_url, model=model)


def _image_data_url(image: Any, *, max_side: int = 1800) -> str:
    rgb = image.convert("RGB")
    width, height = rgb.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / longest
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        rgb = rgb.resize(new_size)

    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _localize_with_box_tool(
    image: Any,
    question: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> LocalizationResult:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt") from exc

    width, height = image.size
    client = OpenAI(api_key=api_key, base_url=base_url)
    image_url = _image_data_url(image)

    system_prompt = f"""You are a visual localization assistant for medical images.

You are not a doctor and you must not diagnose or recommend treatment. Give a cautious visual description only.

Coordinate rules:
- The image viewport is {width}x{height} pixels.
- Return coordinates in the fixed 0..999 model grid, where (0,0) is top-left and (999,999) is bottom-right.
- For a box, x1/y1 are the top-left corner and x2/y2 are the bottom-right corner.
- Use integers only. Ensure x1 < x2 and y1 < y2.

If the image visibly contains a region that answers the user, call mark_region.
If the requested finding is not visible or cannot be localized, call no_region_found.
For tiny findings, return a small box around the best visual center."""

    user_prompt = (
        "Question: "
        + question.strip()
        + "\nReturn the smallest useful visible region that answers the question. Do not guess outside the image."
    )

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                ],
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "mark_region",
                    "description": "Mark one image region with a 0..999 bounding box.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x1": {"type": "integer", "minimum": 0, "maximum": 999},
                            "y1": {"type": "integer", "minimum": 0, "maximum": 999},
                            "x2": {"type": "integer", "minimum": 0, "maximum": 999},
                            "y2": {"type": "integer", "minimum": 0, "maximum": 999},
                            "label": {"type": "string"},
                            "answer": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["x1", "y1", "x2", "y2", "answer"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "no_region_found",
                    "description": "Use when no reliable visible region can be localized.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["answer"],
                    },
                },
            },
        ],
        tool_choice="auto",
    )

    raw = _dump_response(completion)
    message = completion.choices[0].message
    for tool_call in message.tool_calls or []:
        name = tool_call.function.name
        args = _parse_json_args(tool_call.function.arguments)
        if name == "mark_region":
            model_box = normalize_box(args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2"))
            pixel_box = denormalize_box(model_box, width, height)
            center_model = ((model_box.x1 + model_box.x2) // 2, (model_box.y1 + model_box.y2) // 2)
            center_pixel = denormalize_point(center_model[0], center_model[1], width, height)
            return LocalizationResult(
                mode="box_tool",
                answer=str(args.get("answer") or "Region marked."),
                label=str(args.get("label") or "Region"),
                confidence=_optional_float(args.get("confidence")),
                model_box=model_box,
                pixel_box=pixel_box,
                model_point=center_model,
                pixel_point=center_pixel,
                raw=raw,
            )
        if name == "no_region_found":
            answer = str(args.get("answer") or args.get("reason") or "No reliable region found.")
            return LocalizationResult(mode="box_tool", answer=answer, raw=raw)

    text = message.content or "The model did not return a region tool call."
    return LocalizationResult(mode="box_tool", answer=str(text), raw=raw)


def _localize_with_computer_action(
    image: Any,
    question: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> LocalizationResult:
    try:
        from tzafon import Lightcone
    except ImportError as exc:
        raise RuntimeError("Computer-action mode needs the tzafon SDK: python -m pip install -r requirements.txt") from exc

    width, height = image.size
    image_url = _image_data_url(image)
    sdk_base_url = _sdk_base_url(base_url)
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if sdk_base_url:
        client_kwargs["base_url"] = sdk_base_url
    client = Lightcone(**client_kwargs)

    response = client.responses.create(
        model=model,
        instructions=(
            "You are looking at a single medical image. Do not diagnose or give treatment advice. "
            "Use the computer action space only to localize a visible answer. "
            "Prefer a drag action from the top-left corner to bottom-right corner of the relevant region. "
            "If a box is not appropriate, click the center of the relevant visible point. "
            "Use the documented 0..999 coordinate grid."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Localize the visible region that answers this question: "
                            f"{question.strip()}\nReturn a drag action for a box or a click action for a pointer."
                        ),
                    },
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ],
        tools=[
            {
                "type": "computer_use",
                "display_width": width,
                "display_height": height,
                "environment": "desktop",
            }
        ],
    )

    raw = _dump_response(response)
    for item in response.output or []:
        if _field(item, "type") == "computer_call":
            action = _field(item, "action")
            action_type = _field(action, "type", "")
            if action_type == "drag":
                model_box = normalize_box(
                    _field(action, "x", 0),
                    _field(action, "y", 0),
                    _field(action, "end_x", 0),
                    _field(action, "end_y", 0),
                )
                pixel_box = denormalize_box(model_box, width, height)
                center_model = ((model_box.x1 + model_box.x2) // 2, (model_box.y1 + model_box.y2) // 2)
                center_pixel = denormalize_point(center_model[0], center_model[1], width, height)
                return LocalizationResult(
                    mode="computer_action",
                    answer="The model returned a drag action; the rectangle shows the requested region.",
                    label="Drag region",
                    model_box=model_box,
                    pixel_box=pixel_box,
                    model_point=center_model,
                    pixel_point=center_pixel,
                    raw=raw,
                )
            if action_type in {"click", "double_click", "right_click"}:
                model_x = _field(action, "x", 0)
                model_y = _field(action, "y", 0)
                model_box = point_to_model_box(model_x, model_y)
                return LocalizationResult(
                    mode="computer_action",
                    answer="The model returned a pointer action; the crosshair shows the requested point.",
                    label="Pointer",
                    model_box=model_box,
                    pixel_box=denormalize_box(model_box, width, height),
                    model_point=(model_box.x1 + (model_box.x2 - model_box.x1) // 2, model_box.y1 + (model_box.y2 - model_box.y1) // 2),
                    pixel_point=denormalize_point(model_x, model_y, width, height),
                    raw=raw,
                )
        if _field(item, "type") == "message":
            text = _message_text(item)
            if text:
                return LocalizationResult(mode="computer_action", answer=text, raw=raw)

    return LocalizationResult(mode="computer_action", answer="The model did not return a usable action.", raw=raw)


def _parse_json_args(arguments: str | None) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _message_text(item: Any) -> str:
    parts = []
    for block in _field(item, "content", []) or []:
        text = _field(block, "text")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _dump_response(response: Any) -> str:
    for method_name in ("model_dump_json", "to_json"):
        method = getattr(response, method_name, None)
        if callable(method):
            try:
                return method(indent=2)
            except TypeError:
                try:
                    return method()
                except TypeError:
                    pass
    return repr(response)


def _sdk_base_url(openai_base_url: str) -> str | None:
    if not openai_base_url:
        return None
    if openai_base_url.rstrip("/").endswith("/v1"):
        return openai_base_url.rstrip("/")[:-3]
    return openai_base_url
