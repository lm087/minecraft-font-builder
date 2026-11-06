"""Font definitions (font/*.json) and their providers, resolved as the game does.

Every resource pack's copy of a definition adds providers, highest priority pack first.
References are replaced by the referenced font's providers. A provider counts when its
filter matches the game options, and for each character the first provider that has it wins.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import numpy as np

from .gamefiles import ResourceManager, asset_path, parse_identifier
from .glyphs import Glyph

FONT_OPTIONS = ("uniform", "jp")  # Force Unicode Font, Japanese Glyph Variants
INK_ALPHA = 26  # the text shader drops pixels with alpha below 0.1


class FontLoadError(RuntimeError):
    pass


def _codepoint(value) -> int:
    if isinstance(value, int):
        return value
    if len(value) != 1:
        raise FontLoadError(f"expected exactly one character, got {value!r}")
    return ord(value)


def merge_filters(outer: dict, inner: dict) -> dict:
    """The referencing entry's keys win over the referenced provider's."""
    return {**inner, **outer}


def filter_applies(flt: dict, options: dict) -> bool:
    return all(bool(options.get(k, False)) == bool(v) for k, v in flt.items())


class Provider:
    kind = "?"

    def __init__(self, label: str) -> None:
        self.label = label
        self.glyphs: dict[int, Glyph] = {}

    def codepoints(self) -> Iterable[int]:
        return self.glyphs.keys()

    def get(self, cp: int) -> Optional[Glyph]:
        return self.glyphs.get(cp)

    def files(self) -> list[str]:
        return []


class SpaceProvider(Provider):
    kind = "space"

    def __init__(self, label: str, advances: dict) -> None:
        super().__init__(label)
        for k, v in advances.items():
            self.glyphs[_codepoint(k)] = Glyph(advance=float(np.float32(v)), provider=label)


def _ink_values(data: bytes) -> np.ndarray:
    """Alpha per pixel, or luminance for images without alpha, as the game reads it."""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode == "P":
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode in ("RGBA", "LA", "PA"):
        return np.asarray(im.getchannel("A"), dtype=np.uint8)
    if im.mode in ("L", "1", "I", "I;16"):
        return np.asarray(im.convert("L"), dtype=np.uint8)
    return np.full((im.height, im.width), 255, dtype=np.uint8)  # RGB: fully opaque


class BitmapProvider(Provider):
    kind = "bitmap"

    def __init__(self, label: str, rm: ResourceManager, d: dict, warn: Callable[[str], None]) -> None:
        super().__init__(label)
        self.height = int(d.get("height", 8))
        self.ascent = int(d["ascent"])
        if self.ascent > self.height:
            raise FontLoadError(f"{label}: ascent {self.ascent} higher than height {self.height}")
        grid = [[ord(c) for c in row] for row in d["chars"]]
        if not grid or not grid[0]:
            raise FontLoadError(f"{label}: the chars grid is empty")
        if any(len(r) != len(grid[0]) for r in grid):
            raise FontLoadError(f"{label}: the rows of the chars grid have different lengths")
        self.path = asset_path(d["file"], "textures/")
        values = _ink_values(rm.read_required(self.path))
        cell_w = values.shape[1] // len(grid[0])
        cell_h = values.shape[0] // len(grid)
        scale = np.float32(self.height) / np.float32(cell_h)  # float32 as in the game
        self.scale = float(scale)
        top = 7.0 - self.ascent
        for row_i, row in enumerate(grid):
            for col_i, cp in enumerate(row):
                if cp == 0:  # \u0000 marks an empty cell
                    continue
                cell = values[row_i * cell_h:(row_i + 1) * cell_h, col_i * cell_w:(col_i + 1) * cell_w]
                cols = np.flatnonzero((cell != 0).any(axis=0))
                ink_width = int(cols[-1]) + 1 if cols.size else 0
                if cp in self.glyphs:
                    warn(f"{self.path}: character {cp:x} is listed more than once, the last one is used")
                self.glyphs[cp] = Glyph(
                    advance=float(int(0.5 + float(np.float32(ink_width) * scale)) + 1),
                    mask=cell >= INK_ALPHA, left=0.0, right=cell_w * self.scale,
                    top=top, bottom=top + cell_h * self.scale, provider=label,
                )

    def files(self):
        return [self.path]


_HEX_WIDTH = {32: 8, 64: 16, 96: 24, 128: 32}  # hex digits per glyph -> bitmap width


class UnihexProvider(Provider):
    kind = "unihex"

    def __init__(self, label: str, rm: ResourceManager, d: dict, warn: Callable[[str], None]) -> None:
        super().__init__(label)
        self.path = asset_path(d["hex_file"])
        self.license_texts: dict[str, str] = {}
        self.bits: dict[int, tuple[int, str]] = {}  # later .hex files override earlier ones
        with zipfile.ZipFile(io.BytesIO(rm.read_required(self.path))) as zf:
            for info in zf.infolist():
                name = info.filename
                if not name.endswith(".hex"):
                    if re.search(r"license|copying|readme", name, re.I):
                        self.license_texts[name] = zf.read(info).decode("utf-8", "replace")
                    continue
                for lineno, line in enumerate(zf.read(info).decode("ascii").splitlines()):
                    if not line:
                        continue
                    cp_hex, sep, data = line.partition(":")
                    width = _HEX_WIDTH.get(len(data))
                    if not sep or len(cp_hex) not in (4, 5, 6) or width is None:
                        raise FontLoadError(f"{name}: invalid entry at line {lineno}")
                    self.bits[int(cp_hex, 16)] = (width, data)
        self.overrides: dict[int, tuple[int, int]] = {}  # the first matching range wins
        for ov in d.get("size_overrides", []):
            lo, hi = _codepoint(ov["from"]), _codepoint(ov["to"])
            if lo >= hi:
                raise FontLoadError(f"{label}: invalid range {lo}..{hi}")
            for cp in range(lo, hi + 1):
                if cp in self.bits:
                    self.overrides.setdefault(cp, (int(ov["left"]), int(ov["right"])))

    def codepoints(self):
        return self.bits.keys()

    def get(self, cp):
        if cp in self.glyphs or cp not in self.bits:
            return self.glyphs.get(cp)
        width, data = self.bits[cp]
        packed = np.frombuffer(bytes.fromhex(data), dtype=np.uint8).reshape(16, width // 8)
        rows = np.zeros((16, 32), dtype=bool)
        rows[:, :width] = np.unpackbits(packed, axis=1).astype(bool)
        if cp in self.overrides:
            left, right = self.overrides[cp]
        else:
            cols = np.flatnonzero(rows.any(axis=0))
            left, right = (int(cols[0]), int(cols[-1])) if cols.size else (0, width)
        glyph_w = right - left + 1
        mask = np.zeros((16, glyph_w), dtype=bool)
        lo, hi = max(left, 0), min(right, 31)
        if lo <= hi:
            mask[:, lo - left:hi - left + 1] = rows[:, lo:hi + 1]
        # 16 rows of half pixels over y 0..8, bold and shadow move by half a pixel
        g = Glyph(advance=float(glyph_w // 2 + 1), bold_offset=0.5, shadow_offset=0.5, mask=mask,
                  left=0.0, right=glyph_w / 2.0, top=0.0, bottom=8.0, provider=self.label)
        self.glyphs[cp] = g
        return g

    def files(self):
        return [self.path]


@dataclass
class ConditionalProvider:
    provider: Provider
    filter: dict


@dataclass
class ResolvedFont:
    font_id: str
    providers: list[ConditionalProvider]  # highest priority first
    options: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def active(self) -> list[Provider]:
        return [c.provider for c in self.providers if filter_applies(c.filter, self.options)]

    def glyph_map(self) -> dict[int, Glyph]:
        """Every character and the glyph the game draws for it, in code point order."""
        out: dict[int, Glyph] = {}
        for p in reversed(self.active):  # higher priority providers overwrite lower ones
            for cp in p.codepoints():
                g = p.get(cp)
                if g is not None:
                    out[cp] = g
        return dict(sorted(out.items()))


class FontLoader:
    def __init__(self, rm: ResourceManager, warn: Optional[Callable[[str], None]] = None) -> None:
        self.rm = rm
        self.warnings: list[str] = []
        self._on_warning = warn
        self._resolved: dict[str, list[ConditionalProvider]] = {}

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if self._on_warning:
            self._on_warning(msg)

    def definitions(self, font_id: str) -> list[tuple[str, list[dict]]]:
        """(pack, providers) for every pack that has the definition, lowest priority first."""
        out = []
        for pack_id, data in self.rm.read_stack(asset_path(font_id, "font/", ".json")):
            try:
                out.append((pack_id, list(json.loads(data.decode("utf-8-sig"))["providers"])))
            except (ValueError, KeyError, TypeError) as e:
                self.warn(f"font {font_id} in pack {pack_id} can't be read: {e}")
        return out

    def _load_provider(self, label: str, d: dict) -> Optional[Provider]:
        kind = d.get("type")
        try:
            if kind == "space":
                return SpaceProvider(label, d.get("advances", {}))
            if kind == "bitmap":
                return BitmapProvider(label, self.rm, d, self.warn)
            if kind == "unihex":
                return UnihexProvider(label, self.rm, d, self.warn)
        except (KeyError, TypeError, ValueError) as e:
            if isinstance(e, FontLoadError):
                raise
            raise FontLoadError(f"{label}: missing or invalid value {e}") from e
        if kind == "ttf":
            self.warn(f"{label}: TrueType providers from resource packs are not supported, skipped")
            return None
        if kind == "legacy_unicode":
            self.warn(f"{label}: legacy_unicode providers (before 1.20) are not supported, skipped")
            return None
        raise FontLoadError(f"{label}: unknown provider type {kind!r}")

    def resolve(self, font_id: str, _stack: tuple[str, ...] = ()) -> list[ConditionalProvider]:
        font_id = "{}:{}".format(*parse_identifier(font_id))
        if font_id in self._resolved:
            return self._resolved[font_id]
        if font_id in _stack:
            raise FontLoadError(f"font reference cycle: {' > '.join(_stack + (font_id,))}")
        defs = self.definitions(font_id)
        if not defs:
            via = " > ".join(_stack) or "the settings"
            raise FontLoadError(f"font {font_id!r} not found (asked for by {via})")
        out: list[ConditionalProvider] = []
        for pack_id, providers in reversed(defs):
            for i, d in enumerate(providers):
                flt = {k: bool(v) for k, v in (d.get("filter") or {}).items()}
                unknown = set(flt) - set(FONT_OPTIONS)
                if unknown:
                    raise FontLoadError(f"{font_id}: unknown filter option {sorted(unknown)[0]!r}")
                if d.get("type") == "reference":
                    if "id" not in d:
                        raise FontLoadError(f"{font_id}: provider {i} is a reference without an id")
                    for inner in self.resolve(d["id"], _stack + (font_id,)):
                        out.append(ConditionalProvider(inner.provider, merge_filters(flt, inner.filter)))
                    continue
                label = f"{font_id}#{i} {d.get('type')}"
                if d.get("file") or d.get("hex_file"):
                    label += f" {d.get('file') or d.get('hex_file')}"
                if pack_id != "game":
                    label += f" [{pack_id}]"
                provider = self._load_provider(label, d)
                if provider is not None:
                    out.append(ConditionalProvider(provider, flt))
        self._resolved[font_id] = out
        return out

    def load(self, font_id: str, options: dict) -> ResolvedFont:
        return ResolvedFont(font_id, self.resolve(font_id), dict(options), list(self.warnings))