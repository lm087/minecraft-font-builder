"""Draw glyphs the way the game does, and sample font outlines the same way, for verify.

Scale k is screen pixels per game pixel. Canvas x starts at the pen position, y goes down
from the top of the line, so the baseline is at 7k.
"""

from __future__ import annotations

import math

import numpy as np
from fontTools.pens.basePen import BasePen

from .glyphs import Glyph
from .outline import geometry
from .profiles import RenderProfile


def canvas_bounds(g: Glyph, style: str, profile: RenderProfile, k: float, pad: int = 2) -> tuple[int, int, int, int]:
    """A box (x0, y0, x1, y1) in screen pixels that holds the glyph in any case."""
    geo = geometry(g, style, profile)
    left = geo.x0 + min(geo.shear_top, geo.shear_bottom)
    right = geo.x1 + max(geo.shear_top, geo.shear_bottom) + max(geo.copies)
    return (math.floor(left * k) - pad, math.floor(geo.y0 * k) - pad,
            math.ceil(right * k) + pad, math.ceil(geo.y1 * k) + pad)


def render_game(g: Glyph, style: str, profile: RenderProfile, k: float, box: tuple[int, int, int, int],
                jitter: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """The pixels of *box* the game turns on: the texture sampled at each pixel center."""
    x0, y0, x1, y1 = box
    out = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    if not g.has_ink:
        return out
    geo = geometry(g, style, profile)
    h, w = g.mask.shape
    X, Y = np.meshgrid((np.arange(x0, x1) + 0.5 + jitter[0]) / k, (np.arange(y0, y1) + 0.5 + jitter[1]) / k)
    v = (Y - geo.y0) / (geo.y1 - geo.y0)
    shift = geo.shear_top + (geo.shear_bottom - geo.shear_top) * v
    row = np.clip(np.floor(v * h).astype(int), 0, h - 1)
    inside_v = (v >= 0) & (v < 1)
    for off in geo.copies:
        u = (X - off - shift - geo.x0) / (geo.x1 - geo.x0)
        col = np.clip(np.floor(u * w).astype(int), 0, w - 1)
        out |= inside_v & (u >= 0) & (u < 1) & g.mask[row, col]
    return out


def ambiguous_pixels(g: Glyph, style: str, profile: RenderProfile, k: float, box, eps_game: float,
                     jitter: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Pixels whose center lies within *eps_game* of an edge, where the GPU's tie rule decides."""
    base = render_game(g, style, profile, k, box, jitter)
    amb = np.zeros_like(base)
    d = eps_game * k
    for jx, jy in ((d, 0), (-d, 0), (0, d), (0, -d), (d, d), (-d, -d), (d, -d), (-d, d)):
        amb |= render_game(g, style, profile, k, box, (jitter[0] + jx, jitter[1] + jy)) != base
    return amb


class _SegmentPen(BasePen):
    """Collects the straight edges of a glyph as (x0, y0, x1, y1)."""

    def __init__(self, glyphset) -> None:
        super().__init__(glyphset)
        self.segments: list[tuple[float, float, float, float]] = []
        self.start = self.cur = (0.0, 0.0)

    def _moveTo(self, p):
        self.start = self.cur = p

    def _lineTo(self, p):
        self.segments.append((*self.cur, *p))
        self.cur = p

    def _curveToOne(self, p1, p2, p3):
        raise ValueError("unexpected curve in a pixel font")

    def _closePath(self):
        if self.cur != self.start:
            self.segments.append((*self.cur, *self.start))


class OutlineRenderer:
    """Turns on the pixels whose center is inside a glyph outline (non-zero winding).

    No dropout control, so outlines are sampled the way the game samples its textures.
    """

    def __init__(self, path: str, upp: int) -> None:
        from fontTools.ttLib import TTFont

        self.font = TTFont(path)
        self.glyphset = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.hmtx = self.font["hmtx"]
        self.upp = upp
        self._edges: dict[str, np.ndarray] = {}

    def edges(self, name: str) -> np.ndarray:
        if name not in self._edges:
            pen = _SegmentPen(self.glyphset)
            self.glyphset[name].draw(pen)
            self._edges[name] = np.array(pen.segments, dtype=float).reshape(-1, 4)
        return self._edges[name]

    def render(self, cp: int, k: int, box) -> tuple[np.ndarray, float]:
        """The pixels of *box* and the advance in screen pixels for character *cp*."""
        name = self.cmap.get(cp, ".notdef")
        x0, y0, x1, y1 = box
        out = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        advance = self.hmtx[name][0] * k / self.upp
        e = self.edges(name)
        if not len(e):
            return out, advance
        s = k / self.upp  # font units to screen pixels, y flipped so the baseline is at 7k
        ex0, ey0 = e[:, 0] * s, (7 * self.upp - e[:, 1]) * s
        ex1, ey1 = e[:, 2] * s, (7 * self.upp - e[:, 3]) * s
        lo, hi = np.minimum(ey0, ey1), np.maximum(ey0, ey1)
        direction = np.where(ey1 > ey0, 1, -1)
        cx = np.arange(x0, x1) + 0.5
        for j, y in enumerate(np.arange(y0, y1) + 0.5):
            act = (lo <= y) & (y < hi)
            if not act.any():
                continue
            xi = ex0[act] + (y - ey0[act]) * (ex1[act] - ex0[act]) / (ey1[act] - ey0[act])
            out[j] = ((cx[:, None] < xi[None, :]) * direction[act][None, :]).sum(axis=1) != 0
        return out, advance