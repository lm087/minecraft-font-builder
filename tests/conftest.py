"""Synthetic test fixtures.

No game assets are used: the textures and hex data below are small made-up
patterns written by the tests themselves.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# 8 x 8 cells in 2 columns and 2 rows: A (5 px wide), i (1 px), o (4 px, with a hole), an empty cell
CELLS = {
    "A": [
        ".###....",
        "#...#...",
        "#####...",
        "#...#...",
        "#...#...",
        "#...#...",
        "#...#...",
        "........",
    ],
    "i": [
        "#.......",
        "........",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "........",
    ],
    "o": [
        "........",
        "........",
        "####....",
        "#..#....",
        "#..#....",
        "#..#....",
        "####....",
        "........",
    ],
    "x": ["........"] * 8,
}


def make_png(rows: list[list[str]]) -> bytes:
    h = len(rows) * 8
    w = len(rows[0]) * 8
    img = np.zeros((h, w, 4), dtype=np.uint8)
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            for y, row in enumerate(CELLS[ch]):
                for x, px in enumerate(row):
                    if px == "#":
                        img[r * 8 + y, c * 8 + x] = (255, 255, 255, 255)
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def hex_line(cp: int, rows: list[int], width: int) -> str:
    digits = width // 4
    return f"{cp:04X}:" + "".join(f"{v:0{digits}X}" for v in rows)


def make_hex_zip(lines: list[str], name: str = "test.hex", license_text: str = "TEST LICENSE") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LICENSE.txt", license_text)
        z.writestr(name, "\n".join(lines) + "\n")
    return buf.getvalue()


# a made-up 16 x 16 ideograph: two stacked frames
WIDE = [0xFFFF, 0x8001, 0x8001, 0x8001, 0x8001, 0x8001, 0x8001, 0xFFFF,
        0x8001, 0x8001, 0x8001, 0x8001, 0x8001, 0x8001, 0x8001, 0xFFFF]
# a made-up 8 x 16 glyph in columns 1 to 5
NARROW = [0x00, 0x00, 0x7C, 0x44, 0x44, 0x44, 0x7C, 0x44, 0x44, 0x44, 0x44, 0x44, 0x00, 0x00, 0x00, 0x00]


def write_pack(root: Path, files: dict[str, bytes | str | dict]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            data = json.dumps(data)
        if isinstance(data, str):
            data = data.encode("utf-8")
        p.write_bytes(data)
    return root


@pytest.fixture
def game_dir(tmp_path: Path) -> Path:
    """A miniature copy of the game's files, with the same folder structure as the real ones."""
    lines = [
        hex_line(0x0041, [0] * 16, 8),  # empty, the bitmap A wins anyway
        hex_line(0x0042, NARROW, 8),  # B: only in Unifont
        hex_line(0x0043, NARROW, 8),  # C: the same as B, so they share a glyph
        hex_line(0x0044, [0] * 16, 8),  # D: empty and 8 wide, so the advance is 5
        hex_line(0x4E00, WIDE, 16),  # wide
        hex_line(0x3042, [0x0180] * 16, 16),  # narrow ink inside a size_overrides range, forced to 16 wide
        hex_line(0x1F600, WIDE, 16),  # beyond the BMP
    ]
    jp_lines = [hex_line(0x4E00, [0x8001] * 16, 16)]  # the Japanese form of U+4E00
    return write_pack(tmp_path / "game", {
        "version.json": {"id": "99.1", "name": "99.1", "build_time": "2026-01-01T00:00:00+00:00"},
        "assets/minecraft/textures/font/test.png": make_png([["A", "i"], ["o", "x"]]),
        "assets/minecraft/font/unifont.zip": make_hex_zip(lines, "unifont_all-17.0.01.hex",
                                                          "LICENSE\nThe license for the compiled fonts is covered by test terms.\nSee \"https://scripts.sil.org/OFL\" for more details.\n"),
        "assets/minecraft/font/unifont_jp.zip": make_hex_zip(jp_lines, "unifont_jp-17.0.01.hex"),
        "assets/minecraft/font/include/space.json": {"providers": [{"type": "space", "advances": {" ": 4, "‌": 0}}]},
        "assets/minecraft/font/include/default.json": {"providers": [
            {"type": "bitmap", "file": "minecraft:font/test.png", "ascent": 7, "chars": ["Ai", "o\u0000"]},
        ]},
        "assets/minecraft/font/include/unifont.json": {"providers": [
            {"type": "unihex", "hex_file": "minecraft:font/unifont_jp.zip", "filter": {"jp": True}},
            {"type": "unihex", "hex_file": "minecraft:font/unifont.zip",
             "size_overrides": [{"from": "、", "to": "ヿ", "left": 0, "right": 15}]},
        ]},
        "assets/minecraft/font/default.json": {"providers": [
            {"type": "reference", "id": "minecraft:include/space"},
            {"type": "reference", "id": "minecraft:include/default", "filter": {"uniform": False}},
            {"type": "reference", "id": "minecraft:include/unifont"},
        ]},
        "assets/minecraft/font/uniform.json": {"providers": [
            {"type": "reference", "id": "minecraft:include/space"},
            {"type": "reference", "id": "minecraft:include/unifont"},
        ]},
    })