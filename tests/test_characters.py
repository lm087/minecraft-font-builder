import pytest

from mcfont import budget
from mcfont.characters import CharacterRules, parse_range, select
from mcfont.gamefiles import open_game
from mcfont.providers import FontLoader


def rules(**kw) -> CharacterRules:
    """Rules that leave nothing out except what the test asks for."""
    return CharacterRules(**{"exclude_planes": [], "keep_blocks": [], **kw})


def test_default_rules_drop_plane_1_but_keep_emoji(game_dir):
    out, removed = select(glyphs(game_dir), CharacterRules())
    assert 0x1F600 in out and "Emoticons" in CharacterRules().keep_blocks
    assert CharacterRules.everything().exclude_planes == [] and CharacterRules.everything().keep_blocks == []


def glyphs(game_dir, **opts):
    src = open_game(game_path=str(game_dir))
    return FontLoader(src.rm).load("minecraft:default", {"uniform": False, "jp": False, **opts}).glyph_map()


def test_dedup_counts_identical_glyphs_once(game_dir):
    gm = glyphs(game_dir)
    b = budget.compute(gm, "regular", 0.1)
    # B and C, and U+4E00 and U+1F600, look the same. The two spaces differ in advance.
    assert b.codepoints == len(gm)
    assert b.outline_glyphs == len(gm) - 2
    key = lambda cp: gm[cp].style_key("regular", 0.1)  # noqa: E731
    assert key(ord("B")) == key(ord("C")) and key(0x4E00) == key(0x1F600)
    assert key(ord(" ")) != key(0x200C)
    assert b.glyphs == b.outline_glyphs + 1  # and .notdef


def test_shadow_layers_counted(game_dir):
    gm = glyphs(game_dir)
    plain = budget.compute(gm, "regular", 0.1)
    shadow = budget.compute(gm, "regular", 0.1, shadow=True)
    assert shadow.glyphs > plain.glyphs


def test_over_limit_suggestions(game_dir, monkeypatch):
    gm = glyphs(game_dir)
    monkeypatch.setattr(budget, "GLYPH_LIMIT", 5)
    b = budget.compute(gm, "regular", 0.1)
    assert not b.fits and b.over == b.glyphs - 5
    sugg = budget.suggest_cuts(b)
    assert sugg and all(s["frees"] >= b.over for s in sugg)


def test_parse_range():
    assert parse_range("U+E000-U+F8FF") == (0xE000, 0xF8FF)
    assert parse_range("e000..f8ff") == (0xE000, 0xF8FF)
    assert parse_range("0x41") == (0x41, 0x41)
    with pytest.raises(ValueError):
        parse_range("F8FF-E000")


def test_select(game_dir):
    gm = glyphs(game_dir)
    out, removed = select(gm, rules(exclude_planes=[1]))
    assert 0x1F600 not in out and removed == {"plane 1": 1}
    out, _ = select(gm, rules(only_blocks=["Basic Latin"]))
    assert all(cp < 0x80 for cp in out)
    out, _ = select(gm, rules(exclude_blocks=["CJK*"]))
    assert 0x4E00 not in out
    out, _ = select(gm, rules(exclude_providers=["unifont.zip"]))
    assert ord("B") not in out and ord("A") in out


def test_keep_blocks_overrides_excludes(game_dir):
    gm = glyphs(game_dir)
    out, _ = select(gm, rules(exclude_planes=[1], keep_blocks=["Emoticons"]))
    assert 0x1F600 in out
    out, _ = select(gm, rules(exclude_blocks=["CJK*"], keep_blocks=["CJK Unified Ideographs"]))
    assert 0x4E00 in out
    out, _ = select(gm, rules(keep_blocks=["Emoticons"], exclude_providers=["unifont.zip"]))
    assert 0x1F600 not in out  # leaving out a provider still applies


def test_keep_blocks_are_reported(game_dir):
    gm = glyphs(game_dir)
    kept = {}
    out, removed = select(gm, rules(exclude_planes=[1], keep_blocks=["Emoticons"]), kept)
    assert 0x1F600 in out and kept == {"plane 1": 1} and "plane 1" not in removed


def test_only_blocks_are_a_hard_limit(game_dir):
    # the default keep_blocks must not bring emoji back when only Basic Latin is asked for
    out, removed = select(glyphs(game_dir), CharacterRules(only_blocks=["Basic Latin"]))
    assert all(cp < 0x80 for cp in out) and removed.get("not in only_blocks")
    out, _ = select(glyphs(game_dir), CharacterRules(only_ranges=["U+0041..U+0042"]))
    assert set(out) <= {0x41, 0x42}