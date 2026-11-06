"""Which characters go into the fonts."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, fields
from typing import Optional

from .glyphs import Glyph
from .unicode_blocks import block_of

# Plane 1 is mostly historic scripts and Unifont's placeholder boxes. Leaving it out but
# keeping these blocks fits the most useful characters under the glyph limit.
DEFAULT_KEEP_BLOCKS = (
    "Miscellaneous Symbols and Pictographs",
    "Emoticons",
    "Transport and Map Symbols",
    "Supplemental Symbols and Pictographs",
    "Symbols and Pictographs Extended-A",
    "Enclosed Alphanumeric Supplement",
    "Enclosed Ideographic Supplement",
    "Mathematical Alphanumeric Symbols",
    "Playing Cards",
    "Mahjong Tiles",
    "Chess Symbols",
    "Geometric Shapes Extended",
    "Small Kana Extension",
)


@dataclass
class CharacterRules:
    """What to leave out. keep_blocks wins over the exclude rules, only_* limit everything."""

    exclude_planes: list[int] = field(default_factory=lambda: [1])
    exclude_blocks: list[str] = field(default_factory=list)
    exclude_ranges: list[str] = field(default_factory=list)
    exclude_unassigned: bool = False
    exclude_providers: list[str] = field(default_factory=list)  # part of a provider label
    keep_blocks: list[str] = field(default_factory=lambda: list(DEFAULT_KEEP_BLOCKS))
    only_blocks: Optional[list[str]] = None
    only_ranges: Optional[list[str]] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "CharacterRules":
        d = d or {}
        unknown = set(d) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown key(s) in characters: {', '.join(sorted(unknown))}")
        return cls(**d)

    @classmethod
    def everything(cls) -> "CharacterRules":
        return cls(exclude_planes=[], keep_blocks=[])


def parse_range(spec: str) -> tuple[int, int]:
    """"U+E000..U+F8FF", "E000-F8FF", "0xE000..0xF8FF" or one code point."""
    text = spec.strip().upper().replace("U+", "").replace("0X", "")
    parts = re.split(r"\s*(?:\.\.|-)\s*", text)
    try:
        a, b = (int(parts[0], 16),) * 2 if len(parts) == 1 else (int(parts[0], 16), int(parts[1], 16))
    except (ValueError, IndexError):
        a, b = 1, 0
    if len(parts) > 2 or a > b:
        raise ValueError(f"invalid range {spec!r}")
    return a, b


def to_ranges(cps) -> list[tuple[int, int]]:
    """Code points as (first, last) runs."""
    out: list[tuple[int, int]] = []
    for cp in sorted(cps):
        if out and cp == out[-1][1] + 1:
            out[-1] = (out[-1][0], cp)
        else:
            out.append((cp, cp))
    return out


def format_range(a: int, b: int) -> str:
    return f"U+{a:04X}" if a == b else f"U+{a:04X}..U+{b:04X}"


def _match_any(name: str, patterns: list[str], substring: bool = False) -> bool:
    """Case-insensitive: exact name, glob pattern such as "CJK*", or part of the name."""
    name = name.lower()
    for p in (p.lower() for p in patterns):
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatchcase(name, p):
                return True
        elif p == name or (substring and p in name):
            return True
    return False


def select(glyphs: dict[int, Glyph], rules: CharacterRules,
           kept: Optional[dict[str, int]] = None) -> tuple[dict[int, Glyph], dict[str, int]]:
    """The glyphs the rules let through, and how many were left out for each reason.

    *kept* receives how many characters keep_blocks saved from each rule.
    """
    only_ranges = [parse_range(r) for r in rules.only_ranges] if rules.only_ranges is not None else None
    exclude_ranges = [parse_range(r) for r in rules.exclude_ranges]
    planes = {int(p) for p in rules.exclude_planes}
    out: dict[int, Glyph] = {}
    removed: dict[str, int] = {}

    def count(d: dict, reason: str) -> None:
        d[reason] = d.get(reason, 0) + 1

    def limit(cp: int, blk: str) -> Optional[str]:
        if rules.only_blocks is not None and not _match_any(blk, rules.only_blocks):
            return "not in only_blocks"
        if only_ranges is not None and not any(a <= cp <= b for a, b in only_ranges):
            return "not in only_ranges"
        return None

    def exclusion(cp: int, blk: str) -> Optional[str]:
        if cp >> 16 in planes:
            return f"plane {cp >> 16}"
        if rules.exclude_unassigned and blk.startswith("Unassigned"):
            return "unassigned code points"
        if rules.exclude_blocks and _match_any(blk, rules.exclude_blocks):
            return f"block {blk}"
        if any(a <= cp <= b for a, b in exclude_ranges):
            return "exclude_ranges"
        return None

    for cp, g in glyphs.items():
        if rules.exclude_providers and _match_any(g.provider, rules.exclude_providers, substring=True):
            count(removed, f"provider {g.provider}")
            continue
        blk = block_of(cp)
        reason = limit(cp, blk)
        if reason is None:
            reason = exclusion(cp, blk)
            if reason is not None and rules.keep_blocks and _match_any(blk, rules.keep_blocks):
                if kept is not None:
                    count(kept, reason)
                reason = None
        if reason is None:
            out[cp] = g
        else:
            count(removed, reason)
    return out, removed