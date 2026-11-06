import json
import shutil

import pytest
from fontTools.ttLib import TTFont

from mcfont import config as config_mod
from mcfont.builder import GlyphLimitError, build
from mcfont.cli import main
from mcfont.verify import ALLOWED_NAME_IDS, structural_checks

from .conftest import NARROW, hex_line, make_hex_zip, write_pack


def settings(game_dir, out, **kw):
    d = {"game_path": str(game_dir), "output": str(out), "build_date": "2026-01-02", "formats": ["ttf", "otf", "woff2"]}
    d.update(kw)
    return config_mod.from_dict(d)


def run(game_dir, out, **kw):
    return build(settings(game_dir, out, **kw), log=lambda m: None)


@pytest.mark.parametrize("profile", ["1.13-1.21.1", "1.21.2+"])
def test_build_and_verify_pixels(game_dir, tmp_path, profile):
    out = tmp_path / "out"
    report = run(game_dir, out, render_profile=profile)
    names = {f.name for f in out.iterdir()}
    assert "Mojangles-BoldItalic.otf" in names
    assert {"LICENSE-Unifont.txt", "build-report.json", "build-config.yaml"} <= names
    assert report["fonts"][0]["styles"]["bold"]["glyphs"] > 5
    for f in out.iterdir():
        if f.suffix in (".ttf", ".otf", ".woff2"):
            bad = [r for r in structural_checks(f) if not r.ok]
            assert not bad, (f.name, bad)
    # every glyph of every style compared pixel by pixel with the game's drawing
    assert main(["verify", str(out), "--all", "--scales", "1,2,3,4,5"]) == 0


def test_metadata(game_dir, tmp_path):
    out = tmp_path / "out"
    run(game_dir, out, formats=["ttf"])
    f = TTFont(str(out / "Mojangles-BoldItalic.ttf"))
    name = f["name"]
    assert name.getDebugName(1) == "Mojangles" == name.getDebugName(16)
    assert name.getDebugName(2) == "Bold Italic" == name.getDebugName(17)
    assert name.getDebugName(5) == "Version 99.1"
    assert name.getDebugName(3) == "Mojangles-BoldItalic 99.1"
    assert abs(f["head"].fontRevision - 99.1) < 0.001
    assert name.getDebugName(0) == "Copyright Mojang AB. Unifont glyphs copyright Roman Czyborra, Paul Hardy et al."
    assert name.getDebugName(13) == "Unifont glyphs: SIL OFL 1.1, or GNU GPL v2+ with the font embedding exception."
    assert name.getDebugName(14) == "https://scripts.sil.org/OFL"
    assert {rec.nameID for rec in name.names} <= ALLOWED_NAME_IDS
    assert f["OS/2"].usWeightClass == 700 and f["OS/2"].fsSelection & 0b10100001 == 0b10100001
    assert f["head"].macStyle == 3
    assert abs(f["post"].italicAngle + 14.036) < 0.01
    assert "GSUB" not in f and "GPOS" not in f
    assert {4, 12} <= {t.format for t in f["cmap"].tables}  # U+1F600 needs format 12
    # 9 pixel lines, 8 above the baseline like the game's buttons, the same in all three tables
    os2, hhea = f["OS/2"], f["hhea"]
    assert hhea.ascent == os2.sTypoAscender == os2.usWinAscent == 8 * 120
    assert -hhea.descent == -os2.sTypoDescender == os2.usWinDescent == 1 * 120


def test_reproducible(game_dir, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    run(game_dir, a, formats=["ttf"], styles=["regular"])
    run(game_dir, b, formats=["ttf"], styles=["regular"])
    f = "Mojangles-Regular.ttf"
    assert (a / f).read_bytes() == (b / f).read_bytes()


def test_too_many_glyphs_writes_nothing(game_dir, tmp_path, monkeypatch):
    from mcfont import budget, fontfile

    monkeypatch.setattr(budget, "GLYPH_LIMIT", 4)
    monkeypatch.setattr(fontfile, "GLYPH_LIMIT", 4)
    out = tmp_path / "out"
    with pytest.raises(GlyphLimitError) as e:
        run(game_dir, out)
    assert "over the limit" in str(e.value) and "Nothing was written" in str(e.value)
    assert not out.exists()


def test_shadow_and_embedded_bitmaps(game_dir, tmp_path):
    out, plain = tmp_path / "out", tmp_path / "plain"
    run(game_dir, out, formats=["ttf", "otf"], styles=["bold"], shadow=True, embedded_bitmaps=[8, 16])
    run(game_dir, plain, formats=["ttf"], styles=["bold"])
    f = TTFont(str(out / "MojanglesShadow-Bold.ttf"))
    assert f["name"].getDebugName(1) == "Mojangles Shadow"
    assert "COLR" in f and "CPAL" in f and "EBLC" in f and "EBDT" in f
    assert [s.bitmapSizeTable.ppemY for s in f["EBLC"].strikes] == [8, 16]
    # colrv1 adds no glyphs: the shadow is the glyph itself, moved by the game's shadow offset
    assert len(f.getGlyphOrder()) == len(TTFont(str(plain / "Mojangles-Bold.ttf")).getGlyphOrder())
    assert f["COLR"].version == 1
    pal = f["CPAL"].palettes[0][0]
    assert (pal.red, pal.green, pal.blue) == (63, 63, 63)  # the text color times 0.25
    colr, cmap = f["COLR"].table, f.getBestCmap()
    offsets = {}
    for rec in colr.BaseGlyphList.BaseGlyphPaintRecord:
        layers = colr.LayerList.Paint[rec.Paint.FirstLayerIndex:rec.Paint.FirstLayerIndex + rec.Paint.NumLayers]
        offsets[rec.BaseGlyph] = (layers[0].dx, layers[0].dy)
    assert offsets[cmap[ord("A")]] == (120, -120)  # a bitmap glyph moves 1 game pixel
    assert offsets[cmap[ord("B")]] == (60, -60)  # a Unifont glyph half a game pixel
    for p in (out / "MojanglesShadow-Bold.ttf", out / "MojanglesShadow-Bold.otf"):
        assert all(r.ok for r in structural_checks(p)), p


def test_shadow_colrv0(game_dir, tmp_path):
    out = tmp_path / "out"
    run(game_dir, out, formats=["ttf"], styles=["regular"], shadow=True, shadow_format="colrv0")
    f = TTFont(str(out / "MojanglesShadow-Regular.ttf"))
    assert f["COLR"].version == 0
    assert all(r.ok for r in structural_checks(out / "MojanglesShadow-Regular.ttf"))


def test_cli_count_and_init(game_dir, tmp_path, capsys):
    assert main(["count", "--game-path", str(game_dir), "--detail", "providers"]) == 0
    out = capsys.readouterr().out
    assert "glyphs (OK)" in out and "skipped: minecraft:alt, minecraft:illageralt\n" in out
    path = tmp_path / "s.yaml"
    assert main(["init", str(path)]) == 0
    cfg = config_mod.load(str(path))
    assert cfg.fonts == list(config_mod.DEFAULT_FONTS) and cfg.styles == ["regular", "bold", "italic", "bold_italic"]
    assert main(["init", str(path)]) == 1  # never overwrites


def test_old_settings_files_still_load(game_dir, tmp_path, capsys):
    old = {"vanilla_path": str(game_dir), "output_dir": str(tmp_path / "out"), "options": {"uniform": False, "jp": True},
           "charset": {"exclude_planes": [1], "include_blocks": None}, "naming": {"suffix": "Local"},
           "metrics": "bbox", "shadow": {"enabled": False, "mode": "colrv1"}, "formats": ["ttf"]}
    path = tmp_path / "old.yaml"
    path.write_text(json.dumps(old))
    cfg = config_mod.load(str(path))
    assert cfg.game_path == str(game_dir) and cfg.japanese_glyph_variants and cfg.characters.exclude_planes == [1]
    assert cfg.family_suffix == "Local" and cfg.line_metrics == "fit" and cfg.shadow is False
    assert cfg.fonts == ["minecraft:default"] and cfg.characters.keep_blocks == []  # 0.1 defaults
    assert "converted" in capsys.readouterr().err


def test_diff_between_local_versions(game_dir, tmp_path, capsys):
    new = tmp_path / "new"
    shutil.copytree(game_dir, new)
    lines = [hex_line(0x0042, NARROW, 8), hex_line(0x0043, [0xFF] * 16, 8), hex_line(0x0045, NARROW, 8)]
    (new / "assets/minecraft/font/unifont.zip").write_bytes(make_hex_zip(lines, "unifont_all-18.0.0.hex"))
    assert main(["diff", str(game_dir), str(new), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)[0]
    assert d["added"] == ["U+0045"]
    assert "U+0043" in d["changed_shape"]
    assert "U+4E00" in ",".join(d["removed"])


def test_all_fonts_and_family_names(game_dir, tmp_path):
    report = run(game_dir, tmp_path / "out", fonts=["all"], formats=["ttf"], styles=["regular"])
    assert {f["font_id"]: f["family"] for f in report["fonts"]} == {
        "minecraft:default": "Mojangles", "minecraft:uniform": "Mojangles Uniform"}
    assert all(f["game_files_sha1"] for f in report["fonts"])
    out2 = tmp_path / "out2"
    report = run(game_dir, out2, formats=["ttf"], styles=["regular"], family_names={"minecraft:default": "My Pixel"},
                 force_unicode_font=True)
    assert report["fonts"][0]["family"] == "My Pixel Unicode"
    assert (out2 / "MyPixelUnicode-Regular.ttf").exists()


def test_copyright_and_license_settings(game_dir, tmp_path):
    write_pack(game_dir, {"assets/minecraft/font/alt.json": {"providers": [
        {"type": "bitmap", "file": "minecraft:font/test.png", "ascent": 7, "chars": ["Ai", "o\u0000"]}]}})
    out = tmp_path / "out"
    run(game_dir, out, fonts=["minecraft:alt"], formats=["ttf", "otf"], styles=["regular"])
    f = TTFont(str(out / "MojanglesGalactic-Regular.otf"))
    assert f["name"].getDebugName(0) == "Copyright Mojang AB"  # no Unifont glyphs in this font
    assert f["name"].getDebugName(13) is None and f["name"].getDebugName(14) is None
    assert f["CFF "].cff.topDictIndex[0].Notice == "Copyright Mojang AB"
    assert not (out / "LICENSE-Unifont.txt").exists()
    out2 = tmp_path / "out2"
    run(game_dir, out2, formats=["ttf"], styles=["regular"], copyright="", license="Mine")
    n = TTFont(str(out2 / "Mojangles-Regular.ttf"))["name"]
    assert n.getDebugName(0) is None and n.getDebugName(13) == "Mine" and n.getDebugName(14) is None


def test_metadata_settings(game_dir, tmp_path):
    out = tmp_path / "out"
    run(game_dir, out, formats=["ttf"], styles=["regular"], font_version="2.5", designer="Limo",
        designer_url="https://example.com/limo", manufacturer="Pixel Works", manufacturer_url="https://example.com",
        description="Fonts from the game.", license_url="")
    f = TTFont(str(out / "Mojangles-Regular.ttf"))
    n = f["name"]
    assert n.getDebugName(5) == "Version 2.5" and abs(f["head"].fontRevision - 2.5) < 0.001
    assert (n.getDebugName(9), n.getDebugName(12)) == ("Limo", "https://example.com/limo")
    assert (n.getDebugName(8), n.getDebugName(11)) == ("Pixel Works", "https://example.com")
    assert n.getDebugName(10) == "Fonts from the game." and n.getDebugName(14) is None
    assert n.getDebugName(13)  # the automatic Unifont license is still there
    assert main(["verify", str(out), "--no-pixels", "--no-ots"]) == 0  # the extra name IDs are allowed


def test_cli_options(game_dir, tmp_path, capsys):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"game_path: {game_dir}\nshadow: true\ncopyright: Mine\n")
    out = tmp_path / "out"
    assert main(["build", "-c", str(cfg), "-o", str(out), "--style", "regular", "--no-shadow", "--copyright", "auto",
                 "--family-name", "default=My Pixel", "--designer", "Limo"]) == 0
    n = TTFont(str(out / "MyPixel-Regular.ttf"))["name"]
    assert n.getDebugName(1) == "My Pixel" and n.getDebugName(9) == "Limo"
    assert n.getDebugName(0).startswith("Copyright Mojang AB")  # auto brought the automatic text back
    with pytest.raises(SystemExit):
        main(["build", "--family-name", "no-equals-sign"])
    capsys.readouterr()
    assert main([]) == 0 and "usage: mcfont" in capsys.readouterr().out


def _min_x(path):
    f = TTFont(str(path))
    g = f["glyf"]
    return min(g[n].xMin for n in f.getGlyphOrder() if g[n].numberOfContours)


def test_italic_shift_removes_left_overhang(game_dir, tmp_path):
    a, b = tmp_path / "shifted", tmp_path / "game"
    run(game_dir, a, formats=["ttf"], styles=["italic", "regular"])
    run(game_dir, b, formats=["ttf"], styles=["italic"], italic_shift=0)
    assert _min_x(b / "Mojangles-Italic.ttf") < 0  # the game's italic leans left of the origin
    assert _min_x(a / "Mojangles-Italic.ttf") >= 0  # moved right by 1 game pixel (120 units)
    assert _min_x(a / "Mojangles-Italic.ttf") - _min_x(b / "Mojangles-Italic.ttf") == 120
    fa, fb = TTFont(str(a / "Mojangles-Italic.ttf")), TTFont(str(b / "Mojangles-Italic.ttf"))
    assert [fa["hmtx"][n][0] for n in fa.getGlyphOrder()] == [fb["hmtx"][n][0] for n in fb.getGlyphOrder()]
    assert _min_x(a / "Mojangles-Regular.ttf") >= 0  # upright styles do not move
    assert main(["verify", str(b), "--all", "--scales", "1,2,3"]) == 0


def test_em_pixels(game_dir, tmp_path):
    out = tmp_path / "out"
    run(game_dir, out, formats=["ttf"], styles=["regular", "bold_italic"], em_pixels=10, embedded_bitmaps=[10, 20])
    assert TTFont(str(out / "Mojangles-Regular.ttf"))["head"].unitsPerEm == 1200
    assert main(["verify", str(out), "--all", "--scales", "1,2,3"]) == 0


def test_parallel_build_matches_serial(game_dir, tmp_path):
    a, b = tmp_path / "serial", tmp_path / "parallel"
    ra = run(game_dir, a, fonts=["all"], jobs=1)
    rb = run(game_dir, b, fonts=["all"], jobs=3)
    strip = lambda r: [{**f, "styles": {s: {k: v for k, v in d.items() if k != "seconds"} for s, d in f["styles"].items()}}
                       for f in r["fonts"]]  # noqa: E731
    assert strip(ra) == strip(rb)
    names = sorted(p.name for p in a.iterdir() if p.suffix in (".ttf", ".otf", ".woff2"))
    assert len(names) == 2 * 4 * 3
    for n in names:
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
    assert list(ra["fonts"][0]["styles"]) == ["regular", "bold", "italic", "bold_italic"]


def test_line_metrics_and_centered_glyphs(game_dir, tmp_path):
    a, b = tmp_path / "game", tmp_path / "centered"
    run(game_dir, a, formats=["ttf"], styles=["regular", "italic"], line_metrics="game")
    run(game_dir, b, formats=["ttf", "otf"], styles=["regular", "bold_italic"], center_glyphs=True,
        embedded_bitmaps=[8, 16])
    hhea = TTFont(str(a / "Mojangles-Regular.ttf"))["hhea"]
    assert (hhea.ascent, hhea.descent) == (7 * 120, -2 * 120)
    fa, fb = TTFont(str(a / "Mojangles-Regular.ttf")), TTFont(str(b / "Mojangles-Regular.ttf"))
    name_a, name_b = fa.getBestCmap()[ord("A")], fb.getBestCmap()[ord("A")]
    assert fb["glyf"][name_b].xMin - fa["glyf"][name_a].xMin == 60  # half a game pixel
    assert fb["hmtx"][name_b][0] == fa["hmtx"][name_a][0]  # same advance
    # the pixel check skips odd scales, where the half pixel shift isn't whole
    assert main(["verify", str(b), "--all", "--scales", "1,2,3,4"]) == 0


def test_family_names_without_latin_letters_and_duplicates(game_dir, tmp_path):
    out = tmp_path / "out"
    run(game_dir, out, formats=["ttf"], styles=["regular"], family_names={"minecraft:default": "像素"})
    assert (out / "MojanglesDefault-Regular.ttf").exists()  # named after the id
    assert TTFont(str(out / "MojanglesDefault-Regular.ttf"))["name"].getDebugName(1) == "像素"
    run(game_dir, out, formats=["otf"], styles=["regular"], family_names={"minecraft:default": "像素"})
    write_pack(game_dir, {"assets/minecraft/font/alt.json": {"providers": [
        {"type": "bitmap", "file": "minecraft:font/test.png", "ascent": 7, "chars": ["Ai", "o\u0000"]}]}})
    with pytest.raises(ValueError, match="would both be written"):
        run(game_dir, tmp_path / "dup", formats=["ttf"], styles=["regular"], fonts=["default", "alt"],
            family_names={"minecraft:default": "Same", "minecraft:alt": "Same"})