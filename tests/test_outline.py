import numpy as np

from mcfont.glyphs import Glyph, missing_glyph
from mcfont.outline import _trace, contour_area2, outline
from mcfont.profiles import PROFILE_1_13, PROFILE_1_21_2


def box_glyph(mask):
    m = np.array(mask, dtype=bool)
    h, w = m.shape
    return Glyph(advance=w + 1, mask=m, left=0, right=w, top=0, bottom=h)


def test_trace_merges_pixels_and_orients_holes():
    m = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=bool)
    cs = _trace(m)
    assert len(cs) == 2
    assert sorted(len(c) for c in cs) == [4, 4]  # merged rectangles, corners only


def test_regular_outline_area_matches_ink():
    g = box_glyph([[1, 1, 0], [0, 1, 1]])
    cs = outline(g, "regular", PROFILE_1_13, 10)
    area = sum(contour_area2(c) for c in cs) / 2
    assert area == -4 * 100  # clockwise (TrueType) and 4 pixels of 10x10 units


def test_regular_coordinates_baseline():
    g = missing_glyph()  # 5x8 box at y 0..8, baseline at 7
    cs = outline(g, "regular", PROFILE_1_13, 10)
    ys = [y for c in cs for _, y in c]
    xs = [x for c in cs for x, _ in c]
    assert max(ys) == 70 and min(ys) == -10
    assert min(xs) == 0 and max(xs) == 50


def test_bold_is_union_of_two_copies():
    g = box_glyph([[1]])
    cs = outline(g, "bold", PROFILE_1_13, 10)
    assert len(cs) == 1
    xs = sorted({x for x, _ in cs[0]})
    assert xs == [0, 20]  # [0,1] u [1,2]


def test_bold_extra_thickness_from_1_21_2():
    g = box_glyph([[1]])
    cs = outline(g, "bold", PROFILE_1_21_2, 10)
    xs = sorted({x for x, _ in cs[0]})
    ys = sorted({y for _, y in cs[0]})
    assert xs == [-1, 21]  # each copy grown by 0.1 px on every side
    assert ys == [59, 71]  # texel 6..7 px above the baseline grown to 5.9..7.1


def test_italic_shear_matches_game_formula():
    g = box_glyph([[1]] * 8)  # 1x8 column, top 0, bottom 8
    cs = outline(g, "italic", PROFILE_1_13, 100)
    pts = set(cs[0])
    # the top (game y 0) moves right by 1, the bottom (game y 8) by 1 - 0.25 * 8 = -1
    assert (100, 700) in pts and (200, 700) in pts
    assert (-100, -100) in pts and (0, -100) in pts


def test_diagonal_touch_is_valid():
    m = np.array([[1, 0], [0, 1]], dtype=bool)
    cs = _trace(m)
    assert len(cs) == 2 and all(len(c) == 4 for c in cs)