"""How each game version draws bold and italic.

Game coordinates: x goes right, y goes down from the top of the line, the baseline is at y 7.
Italic moves a quad's top corners right by 1 - 0.25 * top and its bottom corners by
1 - 0.25 * bottom. Bold draws the quad again, moved right by the glyph's bold offset, and
since 1.21.2 grows every copy by 0.1 on each side.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class RenderProfile:
    name: str
    italic_shear: float = 0.25
    italic_offset: float = 1.0
    bold_extra_thickness: float = 0.0
    shadow_color_factor: float = 0.25
    provenance: str = ""
    verified: bool = True


PROFILE_1_13 = RenderProfile(
    name="1.13-1.21.1",
    provenance="BakedGlyph.render in 1.16.2, 1.20.1, 1.21 and 1.21.1: italic 1 - 0.25 y, no bold growth",
)
PROFILE_1_21_2 = RenderProfile(
    name="1.21.2+",
    bold_extra_thickness=0.1,
    provenance="BakedGlyph and BakedSheetGlyph in 1.21.2 to 1.21.11, 26.1 and 26.3: italic 1 - 0.25 y, bold growth 0.1",
)
PROFILES = {p.name: p for p in (PROFILE_1_13, PROFILE_1_21_2)}

# 1.21.1 is the last release checked without the bold growth, 1.21.2 the first with it
_LAST_WITHOUT = datetime(2024, 8, 8, 23, 59, tzinfo=timezone.utc)
_FIRST_WITH = datetime(2024, 10, 22, 0, 0, tzinfo=timezone.utc)


def _parse_time(text: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def select_profile(release_time: str, name: str = "auto") -> tuple[RenderProfile, list[str]]:
    """The profile called *name*, or for "auto" the one that fits *release_time*, and any warnings."""
    if name and name != "auto":
        if name not in PROFILES:
            raise ValueError(f"unknown render profile {name!r}, choose from auto, {', '.join(PROFILES)}")
        return PROFILES[name], []
    dt = _parse_time(release_time or "")
    if dt is None:
        return PROFILE_1_21_2, ["release time unknown, assuming the newest render profile (1.21.2+)"]
    if dt <= _LAST_WITHOUT:
        return PROFILE_1_13, []
    if dt >= _FIRST_WITH:
        return PROFILE_1_21_2, []
    return replace(PROFILE_1_21_2, verified=False), [
        "this version was released between 1.21.1 and 1.21.2, and the snapshot that introduced the bold "
        "growth has not been checked. Using the 1.21.2+ behavior. Set render_profile to 1.13-1.21.1 to change that."
    ]