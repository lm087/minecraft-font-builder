"""Glyphs as the game stores them: an advance and, optionally, a textured quad."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

STYLES = ("regular", "bold", "italic", "bold_italic")
STYLE_NAMES = {"regular": "Regular", "bold": "Bold", "italic": "Italic", "bold_italic": "Bold Italic"}


def is_bold(style: str) -> bool:
    return style in ("bold", "bold_italic")


def is_italic(style: str) -> bool:
    return style in ("italic", "bold_italic")


@dataclass
class Glyph:
    """One character. The ink *mask* (rows x columns) covers left..right, top..bottom in game pixels."""

    advance: float
    bold_offset: float = 1.0
    shadow_offset: float = 1.0
    mask: Optional[np.ndarray] = None
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    provider: str = ""
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def advance_for(self, style: str) -> float:
        return self.advance + (self.bold_offset if is_bold(style) else 0.0)

    @property
    def has_ink(self) -> bool:
        return self.mask is not None and bool(self.mask.any())

    def ink_digest(self) -> bytes:
        """A hash of the drawn pixels and where they are, ignoring empty rows and columns."""
        if "ink" not in self._cache:
            digest = b""
            if self.has_ink:
                m = self.mask
                h, w = m.shape
                rows = np.flatnonzero(m.any(axis=1))
                cols = np.flatnonzero(m.any(axis=0))
                crop = np.ascontiguousarray(m[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1])
                pw = (self.right - self.left) / w
                ph = (self.bottom - self.top) / h
                place = (round(self.left + cols[0] * pw, 6), round(self.top + rows[0] * ph, 6),
                         round(pw, 6), round(ph, 6), crop.shape)
                digest = hashlib.blake2b(repr(place).encode() + np.packbits(crop).tobytes(), digest_size=16).digest()
            self._cache["ink"] = digest
        return self._cache["ink"]

    def style_key(self, style: str, extra_thickness: float, with_shadow: bool = False) -> tuple:
        """Equal for two glyphs exactly when they give the same outline and advance in *style*."""
        advance = round(self.advance_for(style), 6)
        if not self.has_ink:
            return ("empty", advance)
        key: tuple = ("ink", advance, self.ink_digest())
        if is_bold(style):
            key += (round(self.bold_offset, 6),)
            if extra_thickness:
                # the grown quad stretches the whole cell, empty texels included
                key += (self.left, self.right, self.top, self.bottom, self.mask.shape)
        if with_shadow:
            key += ("shadow", round(self.shadow_offset, 6))
        return key


def missing_glyph() -> Glyph:
    """The game's box for characters no font has: 5 x 8 pixels, advance 6."""
    m = np.zeros((8, 5), dtype=bool)
    m[0, :] = m[-1, :] = True
    m[:, 0] = m[:, -1] = True
    return Glyph(advance=6.0, mask=m, left=0.0, right=5.0, top=0.0, bottom=8.0, provider="missing")