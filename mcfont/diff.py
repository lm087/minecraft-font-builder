"""What changed in a font between two game versions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .characters import format_range, to_ranges
from .glyphs import Glyph
from .unicode_blocks import block_of


@dataclass
class FontDiff:
    old_version: str
    new_version: str
    font_id: str
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    changed_shape: list[int] = field(default_factory=list)
    changed_advance: list[int] = field(default_factory=list)
    changed_provider: list[int] = field(default_factory=list)  # also counted as unchanged when it looks the same
    unchanged: int = 0


def _shape(g: Glyph) -> tuple:
    return (g.ink_digest(), round(g.bold_offset, 6))


def _provider(g: Glyph) -> str:
    return g.provider.split(" [")[0]  # without the resource pack


def diff_glyphs(old: dict[int, Glyph], new: dict[int, Glyph], old_v: str, new_v: str, font_id: str) -> FontDiff:
    d = FontDiff(old_v, new_v, font_id)
    for cp in sorted(set(old) | set(new)):
        a, b = old.get(cp), new.get(cp)
        if a is None:
            d.added.append(cp)
        elif b is None:
            d.removed.append(cp)
        else:
            shape, advance = _shape(a) != _shape(b), a.advance != b.advance
            if shape:
                d.changed_shape.append(cp)
            if advance:
                d.changed_advance.append(cp)
            if _provider(a) != _provider(b):
                d.changed_provider.append(cp)
            if not (shape or advance):
                d.unchanged += 1
    return d


def _ranges_text(cps: list[int]) -> list[str]:
    return [format_range(a, b) for a, b in to_ranges(cps)]


def format_diff(d: FontDiff, max_blocks: int = 40) -> str:
    lists = (("added", d.added), ("removed", d.removed), ("shape changed", d.changed_shape),
             ("advance changed", d.changed_advance), ("provider changed", d.changed_provider))
    lines = [f"{d.font_id}: {d.old_version} to {d.new_version}",
             "  " + ", ".join(f"{label} {len(cps)}" for label, cps in lists) + f", unchanged {d.unchanged}"]
    for label, cps in lists:
        if not cps:
            continue
        by_block: dict[str, list[int]] = defaultdict(list)
        for cp in cps:
            by_block[block_of(cp)].append(cp)
        lines.append(f"  {label}:")
        for shown, (blk, members) in enumerate(by_block.items()):
            if shown == max_blocks:
                lines.append(f"    ... {len(by_block) - shown} more blocks")
                break
            ranges = _ranges_text(members)
            more = f", ... ({len(ranges) - 8} more ranges)" if len(ranges) > 8 else ""
            lines.append(f"    {blk} ({len(members)}): {', '.join(ranges[:8])}{more}")
    return "\n".join(lines)


def diff_to_dict(d: FontDiff) -> dict:
    return {"font_id": d.font_id, "old": d.old_version, "new": d.new_version,
            "added": _ranges_text(d.added), "removed": _ranges_text(d.removed),
            "changed_shape": _ranges_text(d.changed_shape), "changed_advance": _ranges_text(d.changed_advance),
            "changed_provider": _ranges_text(d.changed_provider), "unchanged": d.unchanged}