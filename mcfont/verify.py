"""Checks of built fonts: their structure, and every pixel against the game's drawing."""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from fontTools.ttLib import TTFont

from .budget import GLYPH_LIMIT
from .glyphs import Glyph, is_italic
from .profiles import RenderProfile
from .render import OutlineRenderer, ambiguous_pixels, canvas_bounds, render_game

# name IDs the fonts may have: the names, version and copyright (0 to 6), metadata (8 to 14),
# typographic family and subfamily (16, 17)
ALLOWED_NAME_IDS = {0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 16, 17}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PixelResult:
    style: str
    file: str
    glyphs_checked: int = 0
    comparisons: int = 0
    mismatched_pixels: int = 0
    tie_pixels: int = 0  # differences where a pixel center lies exactly on an edge
    advance_errors: int = 0
    worst: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mismatched_pixels == 0 and self.advance_errors == 0


def structural_checks(path: Path, run_ots: bool = True) -> list[CheckResult]:
    res: list[CheckResult] = []
    try:
        font = TTFont(str(path), lazy=False)
        font.ensureDecompiled()
        res.append(CheckResult("fontTools load", True))
    except Exception as e:  # noqa: BLE001
        return [CheckResult("fontTools load", False, str(e))]
    n = len(font.getGlyphOrder())
    res.append(CheckResult("glyph count", n <= GLYPH_LIMIT and font["maxp"].numGlyphs == n, f"{n} / {GLYPH_LIMIT}"))
    cmap = font["cmap"]
    fmts = sorted({(t.platformID, t.platEncID, t.format) for t in cmap.tables})
    best = font.getBestCmap()
    has_smp = any(cp > 0xFFFF for cp in best)
    ok = any(f[2] == 4 for f in fmts) and (not has_smp or any(f[2] == 12 for f in fmts))
    res.append(CheckResult("cmap subtables", ok, str(fmts)))
    unexpected = [t for t in ("GSUB", "GPOS", "kern", "morx") if t in font]
    res.append(CheckResult("no shaping tables", not unexpected, ", ".join(unexpected)))
    os2, hhea, head = font["OS/2"], font["hhea"], font["head"]
    consistent = (hhea.ascent == os2.sTypoAscender == os2.usWinAscent and
                  hhea.descent == os2.sTypoDescender == -os2.usWinDescent and
                  hhea.lineGap == os2.sTypoLineGap == 0)
    res.append(CheckResult("vertical metrics consistent", consistent,
                           f"hhea {hhea.ascent}/{hhea.descent}, typo {os2.sTypoAscender}/{os2.sTypoDescender}, win {os2.usWinAscent}/{os2.usWinDescent}"))
    res.append(CheckResult("USE_TYPO_METRICS", bool(os2.fsSelection & (1 << 7))))
    name = font["name"]
    sub = name.getDebugName(2) or ""
    bold, italic = "Bold" in sub, "Italic" in sub
    style_ok = (bool(os2.fsSelection & 1) == italic and bool(os2.fsSelection & 32) == bold and
                bool(head.macStyle & 2) == italic and bool(head.macStyle & 1) == bold and
                os2.usWeightClass == (700 if bold else 400) and
                (bool(os2.fsSelection & 64) == (not bold and not italic)) and
                name.getDebugName(1) == name.getDebugName(16) and sub == name.getDebugName(17))
    res.append(CheckResult("style linking (nameID 1/2/16/17, fsSelection, macStyle, weight)", style_ok,
                           f"{name.getDebugName(1)!r} / {sub!r}"))
    extra = sorted({rec.nameID for rec in name.names} - ALLOWED_NAME_IDS)
    res.append(CheckResult("name table has only the expected entries", not extra, f"unexpected: {extra}" if extra else ""))
    if run_ots:
        try:
            import ots  # type: ignore

            with tempfile.TemporaryDirectory() as td:
                r = ots.sanitize(str(path), str(Path(td) / "out"), capture_output=True)
            msg = (r.stderr or b"").decode("utf-8", "replace").strip()
            res.append(CheckResult("OTS (OpenType Sanitizer)", r.returncode == 0, msg[:500]))
        except ImportError:
            res.append(CheckResult("OTS (OpenType Sanitizer)", True, "skipped: `pip install opentype-sanitizer`"))
    return res


FONTBAKERY_CHECKS = [
    "opentype/family/underline_thickness",
    "opentype/family/panose_familytype",
    "opentype/family/equal_font_versions",
    "opentype/family/max_4_fonts_per_family_name",
    "opentype/fsselection",
    "opentype/mac_style",
    "opentype/italic_angle",
    "opentype/maxadvancewidth",
    "opentype/name/match_familyname_fullfont",
    "opentype/postscript_name",
    "opentype/family_naming_recommendations",
    "opentype/xavgcharwidth",
    "opentype/family/consistent_family_name",
    "opentype/monospace",
    "opentype/unitsperem",
    "opentype/points_out_of_bounds",
]


def run_fontbakery(paths: list[Path], log: Callable[[str], None]) -> Optional[int]:
    """The Font Bakery checks that matter here, once per family and outline format."""
    exe = shutil.which("fontbakery")
    if not exe:
        log('Font Bakery not installed (`pip install ".[verify,fontbakery]"`), skipped')
        return None
    groups: dict[tuple[str, str], list[Path]] = {}
    for p in paths:
        if p.suffix not in (".ttf", ".otf"):
            continue  # a WOFF2 file holds the same font as its TTF
        groups.setdefault((p.name.split("-")[0], p.suffix), []).append(p)
    worst = 0
    for (family, ext), files in sorted(groups.items()):
        cmd = [exe, "check-opentype", "--succinct", "-l", "WARN"]
        for c in FONTBAKERY_CHECKS:
            cmd += ["-c", c]
        cmd += [str(p) for p in sorted(files)]
        log(f"Font Bakery: {family} ({ext}, {len(files)} files)")
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = "\n".join(line for line in r.stdout.splitlines() if line.strip() and not line.startswith("Start"))
        log(out[-4000:])
        worst = max(worst, r.returncode)
    return worst


def _as_sfnt(path: Path, tmp: Path) -> Path:
    if path.suffix == ".woff2":
        f = TTFont(str(path))
        f.flavor = None
        out = tmp / (path.stem + ".sfnt")
        f.save(str(out))
        return out
    return path


def pixel_compare(
    path: Path,
    glyphs: dict[int, Glyph],
    style: str,
    profile: RenderProfile,
    upp: int,
    scales: list[int],
    sample: Optional[int] = None,
    seed: int = 0,
    italic_shift: int = 0,
    x_offset: float = 0.0,
) -> PixelResult:
    """Compare every glyph of *path* with the game's drawing at each scale."""
    res = PixelResult(style, path.name)
    with tempfile.TemporaryDirectory() as td:
        sfnt = _as_sfnt(path, Path(td))
        exact = OutlineRenderer(str(sfnt), upp)
        italic = is_italic(style)
        cps = list(glyphs)
        if sample is not None and sample < len(cps):
            rnd = random.Random(seed)
            # every glyph that isn't from Unifont, plus *sample* Unifont glyphs at random
            fixed = [cp for cp in cps if "unihex" not in glyphs[cp].provider]
            rest = [cp for cp in cps if "unihex" in glyphs[cp].provider]
            cps = fixed + rnd.sample(rest, min(len(rest), sample))
        for cp in cps:
            g = glyphs[cp]
            res.glyphs_checked += 1
            for k in scales:
                if g.has_ink:
                    texel = (g.right - g.left) / g.mask.shape[1] * k
                    if texel < 1:
                        continue  # the game itself skips texels here
                shift = ((italic_shift if italic else 0) + x_offset) * k  # in screen pixels
                if shift != int(shift):
                    continue  # edges on pixel centers: every edge pixel would be a tie
                s = int(shift)
                gb = canvas_bounds(g, style, profile, k)
                box = (gb[0], gb[1], gb[2] + s, gb[3])  # the font's glyph
                gbox = (gb[0] - s, gb[1], gb[2], gb[3])  # the same pixels before the shift
                ref = render_game(g, style, profile, k, gbox)
                got, adv = exact.render(cp, k, box)
                res.comparisons += 1
                if abs(adv - g.advance_for(style) * k) > 0.5 / upp * k + 1e-9:
                    res.advance_errors += 1
                diff = ref != got
                if diff.any():
                    amb = ambiguous_pixels(g, style, profile, k, gbox, 1.5 / upp)
                    hard = int((diff & ~amb).sum())
                    res.tie_pixels += int((diff & amb).sum())
                    if hard:
                        res.mismatched_pixels += hard
                        if len(res.worst) < 20:
                            res.worst.append((f"U+{cp:04X}", k, hard))
    return res


def structural_task(args: tuple) -> list[CheckResult]:
    """structural_checks for one file, in a worker process."""
    path, run_ots = args
    return structural_checks(Path(path), run_ots=run_ots)


def pixel_task(task: dict) -> PixelResult:
    """pixel_compare for one file, in a worker process."""
    t = dict(task)
    return pixel_compare(Path(t.pop("path")), t.pop("glyphs"), t.pop("style"), t.pop("profile"),
                         t.pop("upp"), t.pop("scales"), **t)