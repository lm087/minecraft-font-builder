"""Embedded bitmaps (EBDT and EBLC), drawn the way the game draws each glyph at that size.

Only whole multiples of the em are allowed, so every game pixel lands on whole screen pixels.
"""

from __future__ import annotations

import math

import numpy as np
from fontTools.ttLib import TTFont, newTable

from .fontfile import LINE_HEIGHT, glyph_shift, line_ascent_pixels
from .render import canvas_bounds, render_game


def add_embedded_bitmaps(font: TTFont, records, style: str, profile, settings) -> None:
    from fontTools.ttLib.tables import E_B_D_T_, E_B_L_C_
    from fontTools.ttLib.tables.BitmapGlyphMetrics import SmallGlyphMetrics

    em = settings.em_pixels
    eblc = newTable("EBLC")
    eblc.version = 2.0
    eblc.strikes = []
    ebdt = newTable("EBDT")
    ebdt.version = 2.0
    ebdt.strikeData = []
    font["EBDT"] = ebdt  # EBLC needs EBDT to compile
    font["EBLC"] = eblc
    order = font.getGlyphOrder()
    gid = {n: i for i, n in enumerate(order)}

    for ppem in sorted(set(settings.embedded_bitmaps)):
        if ppem % em or ppem <= 0:
            raise ValueError(f"embedded bitmap size {ppem} must be a multiple of {em}")
        k = ppem // em
        if k > 4:
            raise ValueError(f"embedded bitmaps larger than {4 * em} pixels per em are not supported")
        glyphs = {}
        widths = []
        min_lsb = 127
        max_top = -128
        min_bottom = 127
        for rec in records:
            if rec.shadow_of is not None or not rec.contours:
                continue
            g = rec.source
            box = canvas_bounds(g, style, profile, k)
            shift = glyph_shift(settings, style) * k  # in screen pixels
            whole = math.floor(shift)  # whole pixels go into the bearing, the rest into the drawing
            img = render_game(g, style, profile, k, box, (-(shift - whole), 0.0))
            if not img.any():
                continue
            rows = np.flatnonzero(img.any(axis=1))
            cols = np.flatnonzero(img.any(axis=0))
            crop = img[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
            m = SmallGlyphMetrics()
            m.height, m.width = crop.shape
            m.BearingX = int(box[0] + cols[0] + whole)
            m.BearingY = int(7 * k - (box[1] + rows[0]))  # first row above the baseline
            m.Advance = int(round(rec.advance * k / settings.upp))
            bmp = E_B_D_T_.ebdt_bitmap_format_2(None, font)
            del bmp.data, bmp.ttFont  # built from rows, not decoded from data
            bmp.metrics = m
            bmp.setRows([np.packbits(r).tobytes() for r in crop], bitDepth=1, metrics=m)
            glyphs[rec.name] = bmp
            widths.append(m.Advance)
            min_lsb = min(min_lsb, m.BearingX)
            max_top = max(max_top, m.BearingY)
            min_bottom = min(min_bottom, m.BearingY - m.height)
        if not glyphs:
            continue
        names = sorted(glyphs, key=lambda n: gid[n])
        sub = E_B_L_C_.eblc_index_sub_table_1(None, font)
        del sub.data, sub.ttFont
        sub.indexFormat = 1
        sub.imageFormat = 2
        sub.firstGlyphIndex = gid[names[0]]
        sub.lastGlyphIndex = gid[names[-1]]
        sub.names = names
        strike = E_B_L_C_.Strike()
        bst = strike.bitmapSizeTable
        for direction in ("hori", "vert"):
            lm = E_B_L_C_.SbitLineMetrics()
            lm.ascender = line_ascent_pixels(settings.line_metrics) * k
            lm.descender = -(LINE_HEIGHT - line_ascent_pixels(settings.line_metrics)) * k
            lm.widthMax = min(255, max(widths))
            lm.caretSlopeNumerator = 1 if direction == "hori" else 0
            lm.caretSlopeDenominator = 0 if direction == "hori" else 1
            lm.caretOffset = 0
            lm.minOriginSB = max(-128, min_lsb)
            lm.minAdvanceSB = 0
            lm.maxBeforeBL = min(127, max_top)
            lm.minAfterBL = max(-128, min_bottom)
            lm.pad1 = lm.pad2 = 0
            setattr(bst, direction, lm)
        bst.colorRef = 0
        bst.startGlyphIndex = sub.firstGlyphIndex
        bst.endGlyphIndex = sub.lastGlyphIndex
        bst.ppemX = bst.ppemY = ppem
        bst.bitDepth = 1
        bst.flags = 1
        strike.indexSubTables = [sub]
        eblc.strikes.append(strike)
        ebdt.strikeData.append(glyphs)
    if not eblc.strikes:
        del font["EBDT"]
        del font["EBLC"]