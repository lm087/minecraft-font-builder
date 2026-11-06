import pytest

from mcfont.gamefiles import DirectoryPack, ResourceManager, open_game
from mcfont.providers import FontLoadError, FontLoader, filter_applies, merge_filters

from .conftest import write_pack


def load(game_dir, font="minecraft:default", **opts):
    src = open_game(game_path=str(game_dir))
    return FontLoader(src.rm).load(font, {"uniform": False, "jp": False, **opts})


def test_version_info_from_jar_layout(game_dir):
    src = open_game(game_path=str(game_dir))
    assert src.version_id == "99.1"
    assert src.release_time.startswith("2026-01-01")
    assert "minecraft:default" in src.rm.font_ids()


def test_provider_order_and_reference_expansion(game_dir):
    f = load(game_dir)
    labels = [c.provider.label for c in f.providers]
    assert labels[0].startswith("minecraft:include/space#0 space")
    assert "test.png" in labels[1]
    assert "unifont_jp.zip" in labels[2] and "unifont.zip" in labels[3]
    # the reference's filter is merged into the referenced providers
    assert f.providers[1].filter == {"uniform": False}
    assert f.providers[2].filter == {"jp": True}


def test_first_provider_wins(game_dir):
    gm = load(game_dir).glyph_map()
    assert "bitmap" in gm[ord("A")].provider  # bitmap beats unifont (which also has A)
    assert "unihex" in gm[ord("B")].provider
    assert gm[ord(" ")].advance == 4 and not gm[ord(" ")].has_ink
    assert gm[0x200C].advance == 0


def test_filters(game_dir):
    gm = load(game_dir, uniform=True).glyph_map()
    assert "unihex" in gm[ord("A")].provider  # Force Unicode Font: bitmap provider disabled
    gm_jp = load(game_dir, jp=True).glyph_map()
    assert "unifont_jp" in gm_jp[0x4E00].provider
    assert "unifont_jp" not in load(game_dir).glyph_map()[0x4E00].provider


def test_filter_merge_semantics():
    assert merge_filters({"uniform": False}, {"uniform": True, "jp": True}) == {"uniform": False, "jp": True}
    assert filter_applies({}, {})
    assert filter_applies({"jp": False}, {"jp": False})
    assert not filter_applies({"jp": True}, {})


def test_bitmap_advance_rule(game_dir):
    gm = load(game_dir).glyph_map()
    # advance = (int)(0.5 + actualWidth * scale) + 1, actualWidth = last column with alpha != 0
    assert gm[ord("A")].advance == 6
    assert gm[ord("i")].advance == 2
    assert gm[ord("o")].advance == 5
    a = gm[ord("A")]
    assert (a.left, a.right, a.top, a.bottom) == (0.0, 8.0, 0.0, 8.0)
    assert a.bold_offset == 1.0 and a.shadow_offset == 1.0


def test_unihex_width_rules(game_dir):
    gm = load(game_dir).glyph_map()
    b = gm[ord("B")]  # ink in columns 1 to 5: width 5, advance 5 // 2 + 1 = 3
    assert b.mask.shape == (16, 5) and b.advance == 3
    assert (b.right, b.bottom) == (2.5, 8.0)
    assert b.bold_offset == 0.5 and b.shadow_offset == 0.5
    d = gm[ord("D")]  # empty and 8 wide: left 0, right 8, width 9, advance 5
    assert d.advance == 5 and not d.has_ink
    wide = gm[0x4E00]
    assert wide.mask.shape == (16, 16) and wide.advance == 9
    ov = gm[0x3042]  # size_overrides forces 0..15 even though ink is narrow
    assert ov.mask.shape == (16, 16) and ov.advance == 9
    assert 0x1F600 in gm


def test_reference_cycle_fails(tmp_path):
    root = write_pack(tmp_path / "p", {
        "assets/minecraft/font/a.json": {"providers": [{"type": "reference", "id": "minecraft:b"}]},
        "assets/minecraft/font/b.json": {"providers": [{"type": "reference", "id": "minecraft:a"}]},
    })
    loader = FontLoader(ResourceManager([DirectoryPack(root, "game")]))
    with pytest.raises(FontLoadError):
        loader.load("minecraft:a", {})


def test_resource_pack_overrides(game_dir, tmp_path):
    pack = write_pack(tmp_path / "rp", {
        "assets/minecraft/font/default.json": {"providers": [{"type": "space", "advances": {"A": 7}}]},
    })
    src = open_game(game_path=str(game_dir), resource_packs=[str(pack)])
    gm = FontLoader(src.rm).load("minecraft:default", {}).glyph_map()
    assert gm[ord("A")].advance == 7 and not gm[ord("A")].has_ink  # higher pack's providers come first
    assert "unihex" in gm[ord("B")].provider  # game_dir providers still follow