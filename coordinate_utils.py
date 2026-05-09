from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MODEL_GRID_SIZE = 1000
MODEL_MIN = 0
MODEL_MAX = 999


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2


def clamp_model_coord(value: Any) -> int:
    """Clamp a model coordinate into the documented 0..999 grid."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = 0
    return max(MODEL_MIN, min(MODEL_MAX, number))


def denormalize_point(model_x: Any, model_y: Any, width: int, height: int) -> tuple[int, int]:
    """Convert a 0..999 model point into image pixel coordinates.

    Lightcone's coordinate docs use `int(model_x / 1000 * display_width)`.
    The `min(..., width - 1)` guard keeps edge values inside the image.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Image width and height must be positive.")

    x = clamp_model_coord(model_x)
    y = clamp_model_coord(model_y)
    pixel_x = min(width - 1, int(x / MODEL_GRID_SIZE * width))
    pixel_y = min(height - 1, int(y / MODEL_GRID_SIZE * height))
    return pixel_x, pixel_y


def normalize_box(x1: Any, y1: Any, x2: Any, y2: Any) -> Box:
    left = clamp_model_coord(x1)
    top = clamp_model_coord(y1)
    right = clamp_model_coord(x2)
    bottom = clamp_model_coord(y2)

    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top

    if right == left:
        right = min(MODEL_MAX, left + 1)
        left = max(MODEL_MIN, right - 1)
    if bottom == top:
        bottom = min(MODEL_MAX, top + 1)
        top = max(MODEL_MIN, bottom - 1)

    return Box(left, top, right, bottom)


def denormalize_box(box: Box, width: int, height: int) -> Box:
    x1, y1 = denormalize_point(box.x1, box.y1, width, height)
    x2, y2 = denormalize_point(box.x2, box.y2, width, height)

    if x2 <= x1:
        x2 = min(width - 1, x1 + 1)
    if y2 <= y1:
        y2 = min(height - 1, y1 + 1)

    return Box(x1, y1, x2, y2)


def point_to_model_box(model_x: Any, model_y: Any, radius: int = 35) -> Box:
    x = clamp_model_coord(model_x)
    y = clamp_model_coord(model_y)
    return normalize_box(x - radius, y - radius, x + radius, y + radius)

