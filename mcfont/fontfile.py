"""One font file (one style) with TrueType or CID-keyed CFF outlines."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from fontTools.colorLib.builder import buildCOLR, buildCPAL
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._g_a_s_p import table__g_a_s_p
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates

from .budget import GLYPH_LIMIT
from .glyphs import STYLE_NAMES, Glyph, is_bold, is_italic, missing_glyph
from .outline import Contour, outline
from .profiles import RenderProfile

LINE_HEIGHT = 9  # the game's line height in pixels
BASELINE = 7  # the baseline, in pixels from the top of the line


class TooManyGlyphs(RuntimeError):
    pass


@dataclass
class FontInfo:
    """What goes into the name table."""

    family: str
    ps_family: str
    game_version: str
    build_date: datetime
    copyright: str = ""
    license: str = ""
    license_url: str = ""
    version: str = ""  # empty: the game version
    designer: str = ""
    designer_url: str = ""
    manufacturer: str = ""
    manufacturer_url: str = ""
    description: str = ""

    @property
    def version_text(self) -> str:
        return self.version or self.game_version


@dataclass
class FontSettings:
    upp: int = 120  # font units per game pixel
    em_pixels: int = 8
    italic_shift: int = 1  # whole pixels, so the start of an italic line isn't cut off
    x_offset: float = 0.0  # 0.5 centers the 1 pixel gap the game leaves after each glyph
    line_metrics: str = "centered"  # centered, game or fit
    shadow: bool = False
    shadow_format: str = "colrv1"
    shadow_color: str = "#3F3F3F"  # colrv0 only
    shadow_factor: float = 0.25  # colrv1: the shadow is the text color times this
    embedded_bitmaps: list[int] = field(default_factory=list)  # pixels per em

    @property
    def upm(self) -> int:
        return self.upp * self.em_pixels


@dataclass
class GlyphRecord:
    name: str
    advance: int
    contours: list[Contour]
    source: Glyph
    shadow_of: Optional[str] = None


def _cid_name(i: int) -> str:
    return ".notdef" if i == 0 else f"cid{i:05d}"


def glyph_shift(settings: FontSettings, style: str) -> float:
    """How far every glyph of *style* moves right, in game pixels."""
    return settings.x_offset + (settings.italic_shift if is_italic(style) else 0)


def line_ascent_pixels(line_metrics: str) -> int:
    """How much of the 9 pixel line is above the baseline."""
    return BASELINE if line_metrics == "game" else BASELINE + 1


def plan_glyphs(glyphs: dict[int, Glyph], style: str, profile: RenderProfile, settings: FontSettings):
    """The outline of every distinct glyph: (records in glyph order, cmap, color layers)."""
    upp = settings.upp
    grow = profile.bold_extra_thickness
    dx = glyph_shift(settings, style)
    nd = missing_glyph()
    records = [GlyphRecord(".notdef", round(nd.advance_for(style) * upp), outline(nd, style, profile, upp, dx), nd)]
    names: dict = {}
    cmap: dict[int, str] = {}
    for cp, g in glyphs.items():
        key = g.style_key(style, grow)
        if key not in names:
            names[key] = _cid_name(len(records))
            records.append(GlyphRecord(names[key], round(g.advance_for(style) * upp),
                                       outline(g, style, profile, upp, dx), g))
        cmap[cp] = names[key]
    layers: dict = {}
    if settings.shadow and settings.shadow_format == "colrv1":
        for rec in records[1:]:
            if rec.contours:
                layers[rec.name] = _colrv1_paint(rec.name, round(rec.source.shadow_offset * upp))
    elif settings.shadow:  # colrv0: a shadow glyph under each shape
        shadows: dict = {}
        for rec in records[1:]:
            if not rec.contours:
                continue
            key = rec.source.style_key(style, grow, with_shadow=True)
            if key not in shadows:
                shadows[key] = _cid_name(len(records))
                d = round(rec.source.shadow_offset * upp)
                moved = [[(x + d, y - d) for x, y in c] for c in rec.contours]
                records.append(GlyphRecord(shadows[key], 0, moved, rec.source, shadow_of=rec.name))
            layers[rec.name] = [(shadows[key], 0), (rec.name, 0xFFFF)]
    if len(records) > GLYPH_LIMIT:
        raise TooManyGlyphs(f"{len(records)} glyphs, more than {GLYPH_LIMIT}")
    return records, cmap, layers


def _colrv1_paint(name: str, d: int) -> dict:
    """The glyph over a copy moved by (d, -d) in the text color times palette entry 0.

    Multiplying the text color by a gray of 0.25 gives the game's shadow for any color.
    """
    def glyph(palette: int) -> dict:
        return {"Format": ot.PaintFormat.PaintGlyph, "Glyph": name,
                "Paint": {"Format": ot.PaintFormat.PaintSolid, "PaletteIndex": palette, "Alpha": 1.0}}

    shadow = {"Format": ot.PaintFormat.PaintComposite, "SourcePaint": glyph(0),
              "CompositeMode": "multiply", "BackdropPaint": glyph(0xFFFF)}
    return {"Format": ot.PaintFormat.PaintColrLayers, "Layers": [
        {"Format": ot.PaintFormat.PaintTranslate, "dx": d, "dy": -d, "Paint": shadow},
        glyph(0xFFFF),
    ]}


def _tt_glyph(contours: list[Contour]) -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = len(contours)
    if not contours:
        return g
    coords = [p for c in contours for p in c]
    ends, total = [], 0
    for c in contours:
        total += len(c)
        ends.append(total - 1)
    g.coordinates = GlyphCoordinates(coords)
    g.endPtsOfContours = ends
    g.flags = bytearray([1] * len(coords))  # every point is on the outline
    g.program = ttProgram.Program()
    g.program.fromBytecode(b"")
    return g


def _bounds(contours: list[Contour]) -> tuple[int, int, int, int]:
    xs = [x for c in contours for x, _ in c]
    ys = [y for c in contours for _, y in c]
    return min(xs), min(ys), max(xs), max(ys)


def _font_revision(version: str) -> float:
    """head.fontRevision from a version text: "26.3" gives 26.3, "1.21.4" gives 1.21."""
    m = re.match(r"(\d+)(?:\.(\d+))?", version)
    return min(32767.0, float(f"{m.group(1)}.{m.group(2) or 0}")) if m else 1.0


def _ps_safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", s)[:63]


def _ascii(s: str) -> str:
    """Text for CFF fields, which only take printable ASCII."""
    return re.sub(r"[^\x20-\x7e]", "", s.replace("©", "(c)"))


def _ink_top(g: Optional[Glyph], upp: int, fallback: int) -> int:
    """Height of the glyph's top pixel above the baseline, in font units (x height, cap height)."""
    if g is None or not g.has_ink:
        return fallback
    first_row = int(next(i for i, row in enumerate(g.mask) if row.any()))
    pixel_h = (g.bottom - g.top) / g.mask.shape[0]
    return round((BASELINE - (g.top + first_row * pixel_h)) * upp)


def build_font(glyphs: dict[int, Glyph], style: str, profile: RenderProfile, settings: FontSettings,
               info: FontInfo, outlines: str = "ttf", plan=None) -> TTFont:
    """One style as a font in memory, with "ttf" or "otf" (CFF) outlines.

    *plan* from plan_glyphs lets the TrueType and CFF files share the outline work.
    """
    upp, upm = settings.upp, settings.upm
    records, cmap, layers = plan if plan is not None else plan_glyphs(glyphs, style, profile, settings)
    fb = FontBuilder(upm, isTTF=outlines == "ttf")
    fb.setupGlyphOrder([r.name for r in records])
    fb.setupCharacterMap(cmap)
    if outlines == "ttf":
        fb.setupGlyf({r.name: _tt_glyph(r.contours) for r in records})
    else:
        _setup_cid_cff(fb, records, info, style)
    fb.setupHorizontalMetrics({r.name: (r.advance, _bounds(r.contours)[0] if r.contours else 0) for r in records})

    italic, bold = is_italic(style), is_bold(style)
    ascent = line_ascent_pixels(settings.line_metrics) * upp
    descent = LINE_HEIGHT * upp - ascent
    if settings.line_metrics == "fit":  # the line covers every glyph
        boxes = [_bounds(r.contours) for r in records if r.contours]
        top = max([b[3] for b in boxes] + [ascent])
        bottom = min([b[1] for b in boxes] + [-descent])
        win_ascent, win_descent, hhea_ascent, hhea_descent = top, -bottom, top, bottom
    else:
        win_ascent, win_descent, hhea_ascent, hhea_descent = ascent, descent, ascent, -descent
    fb.setupHorizontalHeader(ascent=hhea_ascent, descent=hhea_descent, lineGap=0,
                             caretSlopeRise=1000 if italic else 1,
                             caretSlopeRun=round(profile.italic_shear * 1000) if italic else 0)

    fs_selection = (1 << 7)  # USE_TYPO_METRICS
    fs_selection |= (1 << 0 if italic else 0) | (1 << 5 if bold else 0) | (1 << 6 if not (italic or bold) else 0)
    fb.setupOS2(
        version=4, achVendID="NONE", usWeightClass=700 if bold else 400, usWidthClass=5, fsType=0,
        fsSelection=fs_selection,
        sTypoAscender=ascent, sTypoDescender=-descent, sTypoLineGap=0,
        usWinAscent=max(0, win_ascent), usWinDescent=max(0, win_descent),
        sxHeight=_ink_top(glyphs.get(ord("x")), upp, 5 * upp),
        sCapHeight=_ink_top(glyphs.get(ord("H")), upp, 7 * upp),
        yStrikeoutSize=upp, yStrikeoutPosition=round((BASELINE - 3.5) * upp),
        ySubscriptXSize=upm // 2, ySubscriptYSize=upm // 2, ySubscriptYOffset=upp,
        ySuperscriptXSize=upm // 2, ySuperscriptYSize=upm // 2, ySuperscriptYOffset=3 * upp,
        usDefaultChar=0, usBreakChar=32, usMaxContext=0,
    )
    fb.setupPost(underlinePosition=-upp, underlineThickness=upp,  # the game's underline: 1 to 2 px below
                 italicAngle=-math.degrees(math.atan(profile.italic_shear)) if italic else 0.0,
                 isFixedPitch=0, keepGlyphNames=False)
    fb.setupNameTable(_names(info, style), mac=False)

    head = fb.font["head"]
    head.fontRevision = _font_revision(info.version_text)
    head.created = head.modified = int(info.build_date.timestamp()) + 2082844800  # seconds since 1904
    head.macStyle = (1 if bold else 0) | (2 if italic else 0)
    head.flags = 0b1001  # baseline at y 0, whole pixel sizes
    head.lowestRecPPEM = settings.em_pixels
    gasp = table__g_a_s_p()
    gasp.version = 1
    gasp.gaspRange = {0xFFFF: 0x000A}  # smooth at every size
    fb.font["gasp"] = gasp

    if layers:
        if settings.shadow_format == "colrv1":
            gray = math.floor(255 * settings.shadow_factor) / 255  # the game rounds 255 * 0.25 down
            fb.font["CPAL"] = buildCPAL([[(gray, gray, gray, 1.0)]])
            fb.font["COLR"] = buildCOLR(layers, version=1)
        else:
            c = settings.shadow_color.lstrip("#")
            alpha = int(c[6:8], 16) / 255 if len(c) == 8 else 1.0
            fb.font["CPAL"] = buildCPAL([[(int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255, alpha)]])
            fb.font["COLR"] = buildCOLR(layers, version=0)
    if settings.embedded_bitmaps:
        from .bitmaps import add_embedded_bitmaps

        add_embedded_bitmaps(fb.font, records, style, profile, settings)

    os2 = fb.font["OS/2"]
    os2.recalcUnicodeRanges(fb.font)
    os2.recalcCodePageRanges(fb.font)
    os2.recalcAvgCharWidth(fb.font)
    os2.panose.bFamilyType = 2
    os2.panose.bWeight = 8 if bold else 5
    os2.panose.bLetterForm = 9 if italic else 2
    return fb.font


def _setup_cid_cff(fb: FontBuilder, records: list[GlyphRecord], info: FontInfo, style: str) -> None:
    """CID-keyed CFF: glyph names aren't stored, so all 65535 glyphs fit."""
    from fontTools.cffLib import (CFFFontSet, CharStrings, FDArrayIndex, FDSelect, FontDict,
                                  GlobalSubrsIndex, PrivateDict, TopDict, TopDictIndex)
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    font = fb.font
    font.sfntVersion = "OTTO"
    order = font.getGlyphOrder()
    ps_name = _ps_safe(f"{info.ps_family}-{STYLE_NAMES[style].replace(' ', '')}")
    private = PrivateDict()
    private.defaultWidthX = private.nominalWidthX = 0
    fd = FontDict()
    fd.FontName = ps_name
    fd.Private = private
    fd_array = FDArrayIndex()
    fd_array.append(fd)
    gsubrs = GlobalSubrsIndex()
    top = TopDict()
    top.ROS = ("Adobe", "Identity", 0)
    top.CIDCount = len(order)
    top.charset = order
    top.FDArray = fd_array
    top.FDSelect = FDSelect()
    top.FDSelect.format = 3
    top.FDSelect.gidArray = [0] * len(order)
    top.GlobalSubrs = gsubrs
    top.FullName = _ascii(f"{info.family} {STYLE_NAMES[style]}")
    top.FamilyName = _ascii(info.family)
    top.Weight = "Bold" if is_bold(style) else "Regular"
    upm = font["head"].unitsPerEm
    top.FontMatrix = [1 / upm, 0, 0, 1 / upm, 0, 0]
    if info.copyright:
        top.Notice = _ascii(info.copyright)[:500]
    charstrings = CharStrings(None, order, gsubrs, private, top.FDSelect, fd_array)
    for r in records:
        pen = T2CharStringPen(r.advance, None)
        for c in r.contours:
            points = c[::-1]  # CFF wants outer contours counter-clockwise
            pen.moveTo(points[0])
            for p in points[1:]:
                pen.lineTo(p)
            pen.closePath()
        cs = pen.getCharString(private, gsubrs)
        cs.private = private
        charstrings[r.name] = cs
    top.CharStrings = charstrings
    fs = CFFFontSet()
    fs.major, fs.minor = 1, 0
    fs.otFont = font
    fs.fontNames = [ps_name]
    fs.GlobalSubrs = gsubrs
    fs.topDictIndex = TopDictIndex()
    fs.topDictIndex.append(top)
    font["CFF "] = newTable("CFF ")
    font["CFF "].cff = fs


def _names(info: FontInfo, style: str) -> dict:
    sub = STYLE_NAMES[style]
    ps = _ps_safe(f"{info.ps_family}-{sub.replace(' ', '')}")
    names = {
        "familyName": info.family,
        "styleName": sub,
        "uniqueFontIdentifier": f"{ps} {info.version_text}",
        "fullName": f"{info.family} {sub}",
        "version": f"Version {info.version_text}",  # name 5 must start with "Version "
        "psName": ps,
        "typographicFamily": info.family,
        "typographicSubfamily": sub,
    }
    optional = (("copyright", info.copyright), ("licenseDescription", info.license),
                ("licenseInfoURL", info.license_url), ("manufacturer", info.manufacturer),
                ("designer", info.designer), ("description", info.description),
                ("vendorURL", info.manufacturer_url), ("designerURL", info.designer_url))
    names.update({key: value for key, value in optional if value})
    return names


def save(font: TTFont, path: Path, flavor: Optional[str] = None) -> Path:
    """Write *font*. flavor "woff2" compresses it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = flavor
    font.save(str(path), reorderTables=True)
    font.flavor = None
    return path