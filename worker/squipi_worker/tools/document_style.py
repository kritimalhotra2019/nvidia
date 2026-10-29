"""Small, portable presentation preferences; never code, CSS, file paths or templates."""
from __future__ import annotations

import math
import re
from typing import Any, Dict

from ..registry import InvalidArguments, validate

STYLE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": (
        "Optional presentation preferences from the user's local memory/skill files or current request. "
        "The latest explicit request takes precedence. Omit fields to retain defaults. "
        "Colors are #RRGGBB, fonts are plain installed font names (Office may substitute unavailable fonts). "
        "chart_palette affects newly generated charts, not existing chart images. No code, CSS, URLs or templates."
    ),
    "properties": {
        **{name: {"type": "string", "minLength": 7, "maxLength": 7, "description": "Hex color #RRGGBB"}
           for name in ("accent_color", "heading_color", "body_color", "background_color")},
        "font_family": {"type": "string", "minLength": 1, "maxLength": 64, "description": "Plain font family, e.g. Arial or Aptos"},
        "font_size": {"type": "number", "minimum": 9, "maximum": 32, "description": "Body text size in points; headings scale and table text stays compact"},
        "chart_palette": {"type": "array", "minItems": 1, "maxItems": 10,
                          "items": {"type": "string", "minLength": 7, "maxLength": 7},
                          "description": "One to ten #RRGGBB colors, cycled across chart series"},
    },
}


def normalize_style(value: Any = None) -> Dict[str, Any]:
    if value is None:
        return {}
    validate(STYLE_SCHEMA, value, "style")
    result = dict(value)
    for name in ("accent_color", "heading_color", "body_color", "background_color"):
        if name in result:
            result[name] = _color(result[name], name)
    if "chart_palette" in result:
        result["chart_palette"] = [_color(color, "chart_palette") for color in result["chart_palette"]]
    if "font_family" in result:
        name = result["font_family"].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", name):
            raise InvalidArguments("style.font_family must be a plain font name, without paths, URLs, markup or code")
        result["font_family"] = name
    if "font_size" in result and not math.isfinite(result["font_size"]):
        raise InvalidArguments("style.font_size must be finite")
    return result


def _color(value: str, field: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise InvalidArguments(f"style.{field} must use #RRGGBB hex colors")
    return value.upper()


def contrast_text(background: str) -> str:
    channels = [int(background[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    luminance = sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    return "#000000" if luminance > .179 else "#FFFFFF"


def heading_color(style: Dict[str, Any], default: str = "#000000") -> str:
    return style.get("heading_color", style.get("accent_color", default))
