"""How many glyphs each style needs, and what to leave out when there are too many.

A font file holds at most 65535 glyphs, .notdef included. Characters that look exactly the
same in a style share one glyph, so every style is counted on its own.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .glyphs import STYLE_NAMES, Glyph
from .unicode_blocks import block_of

GLYPH_LIMIT = 65535


@dataclass
class GroupUsage:
    provider: str
    block: str
    codepoints: int = 0
    glyphs: int = 0  # glyphs this group adds that no earlier group had
    exclusive: int = 0  # glyphs that go away when only this group is left out
    first_cp: int = 0
    last_cp: int = 0


@dataclass
class StyleBudget:
    style: str
    codepoints: int
    glyphs: int  # .notdef, outlines and shadow layers
    outline_glyphs: int
    layer_glyphs: int
    groups: list[GroupUsage]  # per provider and block
    blocks: list[GroupUsage]

    @property
    def over(self) -> int:
        return max(0, self.glyphs - GLYPH_LIMIT)

    @property
    def fits(self) -> bool:
        return self.glyphs <= GLYPH_LIMIT


def compute(glyphs: dict[int, Glyph], style: str, extra_thickness: float, shadow: bool = False) -> StyleBudget:
    """Count *glyphs* in *style*. With *shadow*, every inked shape also needs a shadow glyph."""
    keys = {cp: g.style_key(style, extra_thickness) for cp, g in glyphs.items()}
    users = Counter(keys.values())
    layers = len({g.style_key(style, extra_thickness, with_shadow=True) for g in glyphs.values() if g.has_ink}) \
        if shadow else 0
    seen: set = set()
    groups: dict[tuple[str, str], GroupUsage] = {}
    blocks: dict[str, GroupUsage] = {}
    block_users: dict[str, Counter] = defaultdict(Counter)
    for cp, g in glyphs.items():
        blk = block_of(cp)
        key = keys[cp]
        group = groups.setdefault((g.provider, blk), GroupUsage(g.provider, blk, first_cp=cp))
        block = blocks.setdefault(blk, GroupUsage("*", blk, first_cp=cp))
        for u in (group, block):
            u.codepoints += 1
            u.last_cp = cp
        block_users[blk][key] += 1
        if key not in seen:
            seen.add(key)
            group.glyphs += 1
            block.glyphs += 1
    for blk, counts in block_users.items():
        blocks[blk].exclusive = sum(1 for key, n in counts.items() if users[key] == n)
    return StyleBudget(
        style=style,
        codepoints=len(glyphs),
        glyphs=1 + len(users) + layers,
        outline_glyphs=len(users),
        layer_glyphs=layers,
        groups=sorted(groups.values(), key=lambda u: u.first_cp),
        blocks=sorted(blocks.values(), key=lambda u: u.first_cp),
    )


def _pick(candidates: list[tuple[str, int]], need: int) -> tuple[list[str], int]:
    """Take candidates in order until they free *need* glyphs."""
    chosen, got = [], 0
    for name, frees in candidates:
        if got >= need:
            break
        chosen.append(name)
        got += frees
    return chosen, got


def suggest_cuts(budget: StyleBudget, shadow: bool = False) -> list[dict]:
    """Rules that would bring *budget* under the limit. Nothing is applied by itself.

    Estimates use each block's exclusive glyphs, so they never promise too much.
    """
    need = budget.over
    if need <= 0:
        return []
    per_glyph = 2 if shadow else 1  # a shadow glyph goes with every outline
    blocks = [b for b in budget.blocks if b.exclusive > 0]
    suggestions: list[dict] = []

    # whole planes above 0, highest first, then drop the ones not needed after all
    planes: dict[int, int] = defaultdict(int)
    for b in blocks:
        planes[b.first_cp >> 16] += b.exclusive * per_glyph
    chosen, got = _pick([(p, planes[p]) for p in sorted((p for p in planes if p > 0), reverse=True)], need)
    if got >= need:
        for p in sorted(chosen):
            if got - planes[p] >= need:
                chosen.remove(p)
                got -= planes[p]
        suggestions.append({"exclude_planes": sorted(chosen), "frees": got})

    # the fewest blocks: the largest first
    largest = sorted(blocks, key=lambda b: -b.exclusive)
    chosen, got = _pick([(b.block, b.exclusive * per_glyph) for b in largest], need)
    if got >= need:
        suggestions.append({"exclude_blocks": chosen, "frees": got})

    # blocks beyond plane 0 first (historic scripts, unassigned), the largest first
    rare_first = sorted(blocks, key=lambda b: (b.first_cp < 0x10000, -b.exclusive))
    chosen, got = _pick([(b.block, b.exclusive * per_glyph) for b in rare_first], need)
    if got >= need and all(s.get("exclude_blocks") != chosen for s in suggestions):
        suggestions.append({"exclude_blocks": chosen, "frees": got})
    return suggestions


def format_suggestion(s: dict, limit: int = 15) -> str:
    if "exclude_planes" in s:
        return f"exclude_planes: {s['exclude_planes']}   (frees about {s['frees']} glyphs)"
    blocks = s["exclude_blocks"]
    shown = ", ".join(blocks[:limit]) + (f", ... ({len(blocks) - limit} more)" if len(blocks) > limit else "")
    return f"exclude_blocks: [{shown}]   (frees about {s['frees']} glyphs)"


def format_budget(b: StyleBudget, detail: str = "blocks", top: int = 0) -> str:
    status = "OK" if b.fits else f"OVER by {b.over}"
    lines = [f"{STYLE_NAMES[b.style]}: {b.glyphs} / {GLYPH_LIMIT} glyphs ({status})",
             f"    {b.codepoints} code points, {b.outline_glyphs} unique outlines, "
             f"{b.layer_glyphs} shadow layers, 1 .notdef"]
    if detail not in ("providers", "blocks"):
        return "\n".join(lines)
    by_provider = detail == "providers"
    rows = b.groups if by_provider else b.blocks
    if top:
        rows = sorted(rows, key=lambda r: -r.glyphs)[:top]
    lines.append(f"    {'range':<18} {'chars':>7} {'glyphs':>7} {'exclusive':>9}  block{' / provider' if by_provider else ''}")
    for r in rows:
        rng = f"U+{r.first_cp:04X}..{r.last_cp:04X}"
        name = f"{r.block}  ← {r.provider}" if by_provider else r.block
        exclusive = "" if by_provider else r.exclusive
        lines.append(f"    {rng:<18} {r.codepoints:>7} {r.glyphs:>7} {exclusive:>9}  {name}")
    return "\n".join(lines)


def budget_to_dict(b: StyleBudget) -> dict:
    return {
        "style": b.style,
        "limit": GLYPH_LIMIT,
        "glyphs": b.glyphs,
        "over": b.over,
        "codepoints": b.codepoints,
        "outline_glyphs": b.outline_glyphs,
        "layer_glyphs": b.layer_glyphs,
        "blocks": [{"block": r.block, "first": f"U+{r.first_cp:04X}", "last": f"U+{r.last_cp:04X}",
                    "codepoints": r.codepoints, "glyphs": r.glyphs, "exclusive": r.exclusive} for r in b.blocks],
        "groups": [{"provider": r.provider, "block": r.block, "codepoints": r.codepoints, "glyphs": r.glyphs}
                   for r in b.groups],
    }