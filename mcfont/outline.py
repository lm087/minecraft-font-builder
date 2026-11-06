"""Glyph outlines: the drawn pixels, merged into polygons, with the game's bold and italic.

Everything is on a grid of whole font units (upp per game pixel), so merging is exact. The
bold copy is merged with the original column by column, the result is traced into contours
(outer ones clockwise, holes counter-clockwise, as TrueType wants), and italic then moves
every corner by the game's shear for its row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .glyphs import Glyph, is_bold, is_italic
from .profiles import RenderProfile

Contour = list[tuple[int, int]]


@dataclass
class Geometry:
    """Where a glyph's quad is drawn in a style, in game pixels (y down)."""

    x0: float
    x1: float
    y0: float
    y1: float
    shear_top: float  # italic offset of the top corners
    shear_bottom: float
    copies: tuple[float, ...]  # x offsets of the copies drawn (two for bold)


def geometry(g: Glyph, style: str, profile: RenderProfile) -> Geometry:
    bold = is_bold(style)
    grow = profile.bold_extra_thickness if bold else 0.0
    if is_italic(style):
        shear_top = profile.italic_offset - profile.italic_shear * g.top
        shear_bottom = profile.italic_offset - profile.italic_shear * g.bottom
    else:
        shear_top = shear_bottom = 0.0
    copies = (0.0, g.bold_offset) if bold else (0.0,)
    return Geometry(g.left - grow, g.right + grow, g.top - grow, g.bottom + grow, shear_top, shear_bottom, copies)


def _trace(cells: np.ndarray) -> list[Contour]:
    """The boundaries of the filled cells as contours of (column, row) corners.

    Outer boundaries run clockwise with rows going down. Only corners are kept.
    """
    padded = np.pad(cells, 1)
    inner = padded[1:-1, 1:-1]
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    sides = (
        (inner & ~padded[:-2, 1:-1], lambda c, r: ((c, r), (c + 1, r))),  # top
        (inner & ~padded[1:-1, 2:], lambda c, r: ((c + 1, r), (c + 1, r + 1))),  # right
        (inner & ~padded[2:, 1:-1], lambda c, r: ((c + 1, r + 1), (c, r + 1))),  # bottom
        (inner & ~padded[1:-1, :-2], lambda c, r: ((c, r + 1), (c, r))),  # left
    )
    for found, edge in sides:
        for r, c in zip(*np.nonzero(found)):
            a, b = edge(int(c), int(r))
            edges.setdefault(a, []).append(b)

    contours = []
    while edges:
        start = cur = min(edges)  # the same start every time, so the output is reproducible
        points = [start]
        heading = None
        while True:
            outs = edges[cur]
            if len(outs) > 1 and heading is not None:
                # two cells touch at a corner: take the right turn so they stay separate
                outs.sort(key=lambda o: heading[1] * (o[0] - cur[0]) - heading[0] * (o[1] - cur[1]))
            nxt = outs.pop(0)
            if not outs:
                del edges[cur]
            step = (nxt[0] - cur[0], nxt[1] - cur[1])
            if step == heading:
                points[-1] = nxt
            else:
                points.append(nxt)
            heading, cur = step, nxt
            if cur == start:
                break
        points.pop()  # the start again
        if len(points) >= 3:
            a, b, c = points[-1], points[0], points[1]
            if (b[0] - a[0]) * (c[1] - b[1]) == (b[1] - a[1]) * (c[0] - b[0]):
                points.pop(0)  # the start sat in the middle of a straight run
        contours.append(points)
    return contours


def outline(g: Glyph, style: str, profile: RenderProfile, upp: int,
            dx: float = 0.0, dy: float = 0.0) -> list[Contour]:
    """The contours of *g* in *style*, in font units, moved by dx, dy game pixels."""
    if not g.has_ink:
        return []
    mask = g.mask
    h, w = mask.shape
    geo = geometry(g, style, profile)
    cols = np.arange(w + 1)
    rows = np.arange(h + 1)
    xs = np.round((geo.x0 + dx + (geo.x1 - geo.x0) * cols / w) * upp).astype(np.int64)
    ys_down = np.round((geo.y0 + dy + (geo.y1 - geo.y0) * rows / h) * upp).astype(np.int64)
    shifts = np.round((geo.shear_top + (geo.shear_bottom - geo.shear_top) * rows / h) * upp).astype(np.int64)

    offsets = [round(c * upp) for c in geo.copies]
    if offsets == [0]:
        edges_x, cells = xs, mask
    else:
        # the copies overlap on a finer set of columns: one per distinct edge
        edges_x = np.unique(np.concatenate([xs + off for off in offsets]))
        mids2 = edges_x[:-1] + edges_x[1:]  # twice the middle of each column
        cells = np.zeros((h, len(mids2)), dtype=bool)
        for off in offsets:
            src = np.searchsorted(2 * xs, mids2 - 2 * off, side="right") - 1
            valid = (src >= 0) & (src < w)
            cells[:, valid] |= mask[:, src[valid]]
    baseline = 7 * upp
    return [[(int(edges_x[c] + shifts[r]), int(baseline - ys_down[r])) for c, r in contour]
            for contour in _trace(cells)]


def contour_area2(c: Contour) -> int:
    """Twice the signed area of a contour."""
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1]))