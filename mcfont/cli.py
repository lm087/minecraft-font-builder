"""The command line: mcfont <command>."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from . import budget as budget_mod
from .builder import (GlyphLimitError, build, count_glyphs, expand_fonts, family_name,
                      file_stem, is_include, make_repo, open_game_files, render_profile,
                      select_font, shadow_adds_glyphs)
from .characters import CharacterRules, format_range, to_ranges
from .config import FORMATS, LINE_METRICS, SHADOW_FORMATS, TEMPLATE, Config, load
from .gamefiles import open_game
from .glyphs import STYLE_NAMES, STYLES
from .mojang import DownloadError
from .profiles import PROFILES
from .providers import FontLoader, FontLoadError, filter_applies


def _int_list(text: str) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", text.strip()) if x]


def _text_or_auto(text: str) -> Optional[str]:
    """"auto" brings back the automatic text, "" writes none."""
    return None if text == "auto" else text


def _family_name(text: str) -> tuple[str, str]:
    font_id, sep, name = text.partition("=")
    if not sep or not font_id.strip() or not name.strip():
        raise argparse.ArgumentTypeError("use ID=NAME, e.g. minecraft:alt=Galactic")
    font_id = font_id.strip()
    return (font_id if ":" in font_id else f"minecraft:{font_id}"), name.strip()


ONOFF = argparse.BooleanOptionalAction


def _game_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("game files")
    g.add_argument("-c", "--config", help="settings file (YAML or JSON)")
    g.add_argument("--version", dest="game_version", metavar="VERSION",
                   help="latest, latest-snapshot or a version such as 1.21.4")
    g.add_argument("--game-path", help="local client.jar or extracted folder instead of downloading")
    g.add_argument("--launcher-dir", help="reuse the Minecraft launcher's files: auto, off or a folder")
    g.add_argument("--cache-dir", help="where downloads are kept")
    g.add_argument("--offline", action=ONOFF, help="use downloaded files only")
    g.add_argument("--resource-pack", action="append", dest="resource_packs", metavar="PATH",
                   help="resource pack or mod jar (repeatable, lowest priority first)")


def _font_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("fonts and characters")
    g.add_argument("--font", action="append", dest="fonts", metavar="ID",
                   help="font id such as minecraft:alt (repeatable), or 'all'")
    g.add_argument("--force-unicode-font", action=ONOFF, help="the game option of that name")
    g.add_argument("--japanese-glyph-variants", action=ONOFF, help="the game option of that name")
    g.add_argument("--style", action="append", dest="styles", choices=STYLES, help="repeatable")
    g.add_argument("--all-characters", action="store_true",
                   help="start from every character instead of the default rules, which leave out most of plane 1")
    g.add_argument("--exclude-plane", action="append", type=int, metavar="N")
    g.add_argument("--exclude-block", action="append", metavar="NAME")
    g.add_argument("--exclude-range", action="append", metavar="RANGE", help="e.g. U+E000..U+F8FF")
    g.add_argument("--exclude-unassigned", action=ONOFF,
                   help="leave out Unifont's placeholder boxes for unassigned code points")
    g.add_argument("--exclude-provider", action="append", metavar="TEXT")
    g.add_argument("--keep-block", action="append", metavar="NAME", help="keep this block whatever the exclude rules say")
    g.add_argument("--only-block", action="append", metavar="NAME")
    g.add_argument("--only-range", action="append", metavar="RANGE")
    g.add_argument("--shadow", action=ONOFF, help="add the game's text shadow")
    g.add_argument("--shadow-format", choices=SHADOW_FORMATS)
    g.add_argument("--shadow-color", metavar="#RRGGBB", help="the colrv0 shadow color")
    g.add_argument("--render-profile", choices=("auto", *PROFILES))


def _output_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("output")
    g.add_argument("--format", action="append", dest="formats", choices=FORMATS, help="repeatable")
    g.add_argument("-o", "--output", help="output folder")
    g.add_argument("--line-metrics", choices=LINE_METRICS)
    g.add_argument("--center-glyphs", action=ONOFF,
                   help="move glyphs right by half a pixel so centered text is centered")
    g.add_argument("--italic-shift", type=int, metavar="PIXELS")
    g.add_argument("--em-pixels", type=int, metavar="PIXELS")
    g.add_argument("--embedded-bitmaps", type=_int_list, metavar="SIZES", help="e.g. 8,16")
    g.add_argument("--units-per-pixel", type=int)
    g.add_argument("-j", "--jobs", type=int, help="styles built at the same time (0 = automatic)")
    g.add_argument("--build-date", metavar="YYYY-MM-DD")

    g = p.add_argument_group("names and metadata", "Text options take \"\" for none. "
                             "--copyright, --license and --license-url also take auto.")
    g.add_argument("--family-name", action="append", dest="family_name", type=_family_name, metavar="ID=NAME",
                   help="family name for one font (repeatable)")
    g.add_argument("--family-suffix", metavar="TEXT", help="added to every family name, e.g. Local")
    g.add_argument("--copyright", type=_text_or_auto, default="unset", metavar="TEXT")
    g.add_argument("--license", type=_text_or_auto, default="unset", metavar="TEXT")
    g.add_argument("--license-url", type=_text_or_auto, default="unset", metavar="URL")
    g.add_argument("--font-version", metavar="TEXT", help="default: the game version")
    g.add_argument("--designer", metavar="TEXT")
    g.add_argument("--designer-url", metavar="URL")
    g.add_argument("--manufacturer", metavar="TEXT")
    g.add_argument("--manufacturer-url", metavar="URL")
    g.add_argument("--description", metavar="TEXT")


# options that replace a setting
_SET = ("game_path", "launcher_dir", "cache_dir", "offline", "resource_packs", "fonts", "styles", "formats", "output",
        "family_suffix", "line_metrics", "italic_shift", "em_pixels", "embedded_bitmaps", "units_per_pixel", "jobs",
        "build_date", "shadow_format", "shadow_color", "render_profile", "force_unicode_font",
        "japanese_glyph_variants", "center_glyphs", "shadow", "font_version", "designer", "designer_url",
        "manufacturer", "manufacturer_url", "description")
_SET_NONE = ("copyright", "license", "license_url")  # these can also be set back to automatic
# character rules added to the ones in the settings
_EXTEND = {"exclude_plane": "exclude_planes", "exclude_block": "exclude_blocks", "exclude_range": "exclude_ranges",
           "exclude_provider": "exclude_providers", "keep_block": "keep_blocks", "only_block": "only_blocks",
           "only_range": "only_ranges"}


def _settings(a: argparse.Namespace) -> Config:
    cfg = load(getattr(a, "config", None))
    if getattr(a, "game_version", None):
        cfg.version = a.game_version
        if getattr(a, "game_path", None) is None:
            cfg.game_path = None
    for name in _SET:
        value = getattr(a, name, None)
        if value is not None:
            setattr(cfg, name, value)
    for name in _SET_NONE:
        if hasattr(a, name) and getattr(a, name) != "unset":
            setattr(cfg, name, getattr(a, name))
    for font_id, family in getattr(a, "family_name", None) or []:
        cfg.family_names[font_id] = family
    if getattr(a, "all_characters", False):
        cfg.characters = CharacterRules.everything()
        cfg.characters_per_font = {}
    rules = cfg.characters
    for arg, key in _EXTEND.items():
        value = getattr(a, arg, None)
        if value:
            current = getattr(rules, key) or []
            setattr(rules, key, current + [v for v in value if v not in current])
    if getattr(a, "exclude_unassigned", None) is not None:
        rules.exclude_unassigned = a.exclude_unassigned
    cfg.validate()
    return cfg


def cmd_gui(a) -> int:
    from .gui.server import serve

    serve(port=a.port, open_browser=not a.no_browser, initial=load(a.config) if a.config else None)
    return 0


def cmd_versions(a) -> int:
    cfg = _settings(a)
    repo = make_repo(cfg, lambda m: print(m, file=sys.stderr))
    latest = repo.manifest()["latest"]
    print(f"latest release: {latest['release']}    latest snapshot: {latest['snapshot']}")
    versions = repo.versions(include_snapshots=a.snapshots)
    for v in versions[: a.limit] if a.limit else versions:
        print(f"{v.id:<28} {v.type:<9} {v.release_time[:10]}")
    return 0


def cmd_fonts(a) -> int:
    cfg = _settings(a)
    game = open_game_files(cfg)
    loader = FontLoader(game.rm)
    print(f"Minecraft {game.version_id}")
    for fid in game.rm.font_ids():
        include = is_include(fid)
        if include and not a.all:
            continue
        try:
            resolved = loader.load(fid, cfg.game_options)
            n = len(resolved.glyph_map())
        except FontLoadError as e:
            print(f"{fid:<30} error: {e}")
            continue
        note = "  (fragment used by other fonts)" if include else ""
        print(f"{fid:<30} {n:>8} code points   {family_name(cfg, fid)}{note}")
        if a.verbose:
            for c in resolved.providers:
                state = "on " if filter_applies(c.filter, cfg.game_options) else "off"
                flt = f"  filter {c.filter}" if c.filter else ""
                print(f"    [{state}] {c.provider.label}{flt}  ({len(list(c.provider.codepoints()))} characters)")
    if not a.all:
        print("(use --all to also list the include/ fragments)")
    return 0


def cmd_count(a) -> int:
    cfg = _settings(a)
    game = open_game_files(cfg)
    profile, warnings = render_profile(cfg, game)
    loader = FontLoader(game.rm)
    result: dict = {"game_version": game.version_id, "render_profile": profile.name, "fonts": []}
    over = False
    if not a.json:
        print(f"Minecraft {game.version_id}, render profile {profile.name}")
        for w in warnings:
            print(f"warning: {w}")
    for fid in expand_fonts(cfg, game, warn=None if a.json else lambda m: print(f"warning: {m}")):
        sel = select_font(cfg, loader, fid)
        count_glyphs(cfg, sel, profile)
        entry = {"font_id": fid, "family": family_name(cfg, fid), "available": len(sel.available),
                 "selected": len(sel.glyphs), "removed": sel.removed, "kept": sel.kept, "styles": []}
        if not a.json:
            print(f"\n{entry['family']} ({fid}): {len(sel.glyphs)} of {len(sel.available)} code points")
            for reason, n in sorted(sel.removed.items(), key=lambda kv: -kv[1])[:15]:
                print(f"    excluded {n:>7}  {reason}")
            for reason, n in sorted(sel.kept.items(), key=lambda kv: -kv[1]):
                print(f"    kept     {n:>7}  {reason}, but in keep_blocks")
        for st in cfg.styles:
            b = sel.counts[st]
            over |= not b.fits
            entry["styles"].append(budget_mod.budget_to_dict(b))
            if a.json:
                entry["styles"][-1]["suggestions"] = budget_mod.suggest_cuts(b, shadow_adds_glyphs(cfg))
                continue
            print()
            print(budget_mod.format_budget(b, detail=a.detail, top=a.top))
            if not b.fits:
                for i, s in enumerate(budget_mod.suggest_cuts(b, shadow_adds_glyphs(cfg)), 1):
                    print(f"  suggestion {i}: {budget_mod.format_suggestion(s)}")
                print("  (Nothing is removed by itself. Change `characters` in your settings.)")
        if a.coverage:
            entry["coverage"] = [format_range(x, y) for x, y in to_ranges(sel.glyphs)]
            if not a.json:
                print("  coverage: " + ", ".join(entry["coverage"]))
        result["fonts"].append(entry)
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if over else 0


def cmd_build(a) -> int:
    cfg = _settings(a)
    try:
        report = build(cfg)
    except GlyphLimitError as e:
        print(str(e), file=sys.stderr)
        return 2
    files = sum(len(s["files"]) for f in report["fonts"] for s in f["styles"].values())
    print(f"done: {files} {'file' if files == 1 else 'files'} in {Path(cfg.output).expanduser().resolve()}")
    return 0


def cmd_verify(a) -> int:
    from .parallel import auto_jobs
    from .parallel import run as run_parallel
    from .verify import pixel_task, run_fontbakery, structural_task

    out_dir = Path(a.folder)
    if not out_dir.is_dir():
        print(f"error: {out_dir} is not a folder", file=sys.stderr)
        return 1
    a.config = a.config or str(out_dir / "build-config.yaml")
    if not Path(a.config).is_file():
        print(f"error: {a.config} not found. Give the build settings with -c.", file=sys.stderr)
        return 1
    cfg = _settings(a)
    files = sorted(p for p in out_dir.iterdir() if p.suffix in (".ttf", ".otf", ".woff2"))
    if not files:
        print(f"no font files in {out_dir}", file=sys.stderr)
        return 1
    big = any(p.stat().st_size > 2_000_000 for p in files)
    failed = False
    print("Structure")
    jobs = auto_jobs(len(files), cfg.jobs, big=big)
    for f, res in zip(files, run_parallel(structural_task, [(str(f), not a.no_ots) for f in files], jobs)):
        bad = [r for r in res if not r.ok]
        failed |= bool(bad)
        print(f"  {f.name}: {'ok' if not bad else 'FAILED'}")
        for r in res:
            if not r.ok or a.verbose:
                print(f"      [{'ok' if r.ok else 'FAIL'}] {r.name} {r.detail}")
    if a.fontbakery:
        rc = run_fontbakery(files, print)
        if rc not in (None, 0):
            print("Font Bakery reported problems (see above)")
    if a.no_pixels:
        return 1 if failed else 0
    print("Pixels compared with the game's rendering")
    game = open_game_files(cfg)
    profile, _ = render_profile(cfg, game)
    loader = FontLoader(game.rm)
    scales = _int_list(a.scales)
    tasks = []
    for fid in expand_fonts(cfg, game):
        sel = select_font(cfg, loader, fid)
        stem_base = file_stem(cfg, fid)
        for st in cfg.styles:
            stem = f"{stem_base}-{STYLE_NAMES[st].replace(' ', '')}"
            for f in files:
                if f.name.startswith(stem + "."):
                    tasks.append({"path": str(f), "glyphs": sel.glyphs, "style": st, "profile": profile,
                                  "upp": cfg.units_per_pixel, "scales": scales,
                                  "sample": None if a.all else a.sample, "italic_shift": cfg.italic_shift,
                                  "x_offset": 0.5 if cfg.center_glyphs else 0.0})
    if not tasks:
        print("  no font file matches the fonts and styles in the settings", file=sys.stderr)
        return 1
    for r in run_parallel(pixel_task, tasks, auto_jobs(len(tasks), cfg.jobs, big=big)):
        print(f"  {r.file}: {'ok' if r.ok else 'MISMATCH'}  {r.glyphs_checked} glyphs, {r.comparisons} comparisons, "
              f"{r.mismatched_pixels} wrong pixels, {r.advance_errors} wrong advances, {r.tie_pixels} tie pixels")
        if r.worst:
            print("      worst: " + ", ".join(f"{c} at {k}x: {n} px" for c, k, n in r.worst[:10]))
        failed |= not r.ok
    return 1 if failed else 0


def cmd_diff(a) -> int:
    from .diff import diff_glyphs, diff_to_dict, format_diff

    cfg = _settings(a)

    def game_for(spec: str):
        if Path(spec).exists():  # a client.jar or extracted folder
            return open_game(game_path=spec, resource_packs=cfg.resource_packs)
        return open_game_files(cfg, version=spec)

    old_game, new_game = game_for(a.old), game_for(a.new)
    results = []
    for fid in expand_fonts(cfg, new_game):
        old = FontLoader(old_game.rm).load(fid, cfg.game_options).glyph_map() if fid in old_game.rm.font_ids() else {}
        new = FontLoader(new_game.rm).load(fid, cfg.game_options).glyph_map()
        d = diff_glyphs(old, new, old_game.version_id, new_game.version_id, fid)
        results.append(d)
        if not a.json:
            print(format_diff(d))
    if a.json:
        print(json.dumps([diff_to_dict(d) for d in results], indent=2))
    return 0


def cmd_init(a) -> int:
    p = Path(a.file)
    if p.exists() and not a.force:
        print(f"{p} already exists (use --force to replace it)", file=sys.stderr)
        return 1
    p.write_text(TEMPLATE, "utf-8")
    print(f"wrote {p}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="mcfont", description="Minecraft Font Builder: turn the fonts of "
                                 "Minecraft: Java Edition into fonts you can install.")
    ap.add_argument("-V", "--version", action="version", version=f"Minecraft Font Builder {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    p = sub.add_parser("gui", help="open the App in your web browser")
    p.add_argument("-c", "--config", help="settings file to start with")
    p.add_argument("--port", type=int, default=None, help="local port (default 8737, or the next free one, 0 for any)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("versions", help="list Minecraft versions")
    _game_args(p)
    p.add_argument("--snapshots", action="store_true", help="include snapshots")
    p.add_argument("--limit", type=int, default=30, help="show at most this many (0 = all)")
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("fonts", help="list the fonts of a Minecraft version")
    _game_args(p)
    p.add_argument("--force-unicode-font", action=ONOFF, help="the game option of that name")
    p.add_argument("--japanese-glyph-variants", action=ONOFF, help="the game option of that name")
    p.add_argument("--all", action="store_true", help="also list the include/ fragments")
    p.add_argument("-v", "--verbose", action="store_true", help="show each font's providers")
    p.set_defaults(func=cmd_fonts)

    p = sub.add_parser("count", help="count glyphs per style without writing fonts")
    _game_args(p)
    _font_args(p)
    p.add_argument("--detail", choices=("blocks", "providers", "none"), default="blocks")
    p.add_argument("--top", type=int, default=0, help="only show the N largest rows")
    p.add_argument("--coverage", action="store_true", help="print the included code point ranges")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("build", help="write the fonts")
    _game_args(p)
    _font_args(p)
    _output_args(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="check built fonts and compare them with the game's rendering")
    p.add_argument("folder", nargs="?", default="output", help="the output folder of a build (default: output)")
    _game_args(p)
    p.add_argument("--scales", default="1,2,3,4", help="screen pixels per game pixel to compare at")
    p.add_argument("--sample", type=int, default=3000, help="Unifont glyphs to sample per style")
    p.add_argument("--all", action="store_true", help="compare every glyph")
    p.add_argument("--no-pixels", action="store_true", help="only check the file structure")
    p.add_argument("--no-ots", action="store_true", help="skip the OpenType Sanitizer")
    p.add_argument("--fontbakery", action="store_true", help="also run selected Font Bakery checks")
    p.add_argument("-j", "--jobs", type=int)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("diff", help="compare a font between two Minecraft versions")
    p.add_argument("old", help="version, or a local client.jar / extracted folder")
    p.add_argument("new", help="version, or a local client.jar / extracted folder")
    _game_args(p)
    p.add_argument("--font", action="append", dest="fonts", metavar="ID", help="font id (repeatable), or 'all'")
    p.add_argument("--force-unicode-font", action=ONOFF, help="the game option of that name")
    p.add_argument("--japanese-glyph-variants", action=ONOFF, help="the game option of that name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("init", help="write a settings file with every option and its default")
    p.add_argument("file", nargs="?", default="mcfont.yaml")
    p.add_argument("--force", action="store_true", help="replace an existing file")
    p.set_defaults(func=cmd_init)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        return a.func(a)
    except (DownloadError, FontLoadError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    except BrokenPipeError:  # mcfont count | head: the reader stopped early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())