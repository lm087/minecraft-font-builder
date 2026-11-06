"""Build settings, kept as YAML (or JSON) so a build can be repeated exactly."""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Optional

from .characters import CharacterRules, parse_range
from .glyphs import STYLES
from .profiles import PROFILES

FORMATS = ("ttf", "otf", "woff2")
DEFAULT_FONTS = ("minecraft:alt", "minecraft:default", "minecraft:illageralt", "minecraft:uniform")
LINE_METRICS = ("centered", "game", "fit")
SHADOW_FORMATS = ("colrv1", "colrv0")
TEXT_SETTINGS = ("version", "game_path", "cache_dir", "launcher_dir", "output", "family_suffix", "copyright",
                 "license", "license_url", "font_version", "designer", "designer_url", "manufacturer",
                 "manufacturer_url", "description", "shadow_color", "build_date")


@dataclass
class Config:
    # game files
    version: str = "latest"
    game_path: Optional[str] = None  # a client.jar or extracted folder instead of a download
    launcher_dir: str = "auto"  # auto, off or a folder
    cache_dir: Optional[str] = None
    offline: bool = False
    resource_packs: list[str] = field(default_factory=list)

    # what to build
    fonts: list[str] = field(default_factory=lambda: list(DEFAULT_FONTS))
    force_unicode_font: bool = False
    japanese_glyph_variants: bool = False
    styles: list[str] = field(default_factory=lambda: list(STYLES))
    formats: list[str] = field(default_factory=lambda: ["ttf"])
    output: str = "output"
    characters: CharacterRules = field(default_factory=CharacterRules)
    characters_per_font: dict = field(default_factory=dict)  # font id: its own rules

    # names
    family_names: dict = field(default_factory=dict)  # font id: family name
    family_suffix: str = ""

    # metadata: None is automatic, "" writes nothing
    copyright: Optional[str] = None
    license: Optional[str] = None
    license_url: Optional[str] = None
    font_version: Optional[str] = None  # None: the game version
    designer: str = ""
    designer_url: str = ""
    manufacturer: str = ""
    manufacturer_url: str = ""
    description: str = ""

    # appearance
    line_metrics: str = "centered"
    center_glyphs: bool = False
    italic_shift: int = 1
    em_pixels: int = 8
    shadow: bool = False
    shadow_format: str = "colrv1"
    shadow_color: str = "#3F3F3F"
    embedded_bitmaps: list[int] = field(default_factory=list)

    # advanced
    units_per_pixel: int = 120
    render_profile: str = "auto"
    jobs: int = 0
    build_date: Optional[str] = None

    @property
    def game_options(self) -> dict:
        """The game options by the names font definition filters use."""
        return {"uniform": self.force_unicode_font, "jp": self.japanese_glyph_variants}

    def rules_for(self, font_id: str) -> CharacterRules:
        if font_id in self.characters_per_font:
            return CharacterRules.from_dict(self.characters_per_font[font_id])
        return self.characters

    def validate(self) -> None:
        def check(ok: bool, msg: str) -> None:
            if not ok:
                raise ValueError(msg)

        for name in TEXT_SETTINGS:
            value = getattr(self, name)
            check(value is None or isinstance(value, str), f"{name} must be text")
        check(bool(self.version), "version must not be empty")
        check(bool(self.fonts), "choose at least one font")
        for s in self.styles:
            check(s in STYLES, f"unknown style {s!r}, choose from {', '.join(STYLES)}")
        for f in self.formats:
            check(f in FORMATS, f"unknown format {f!r}, choose from {', '.join(FORMATS)}")
        check(self.line_metrics in LINE_METRICS, f"line_metrics must be one of {', '.join(LINE_METRICS)}")
        check(self.shadow_format in SHADOW_FORMATS, f"shadow_format must be one of {', '.join(SHADOW_FORMATS)}")
        check(bool(re.fullmatch(r"#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", self.shadow_color or "")),
              f"shadow_color must look like #3F3F3F, not {self.shadow_color!r}")
        check(isinstance(self.italic_shift, int) and 0 <= self.italic_shift <= 4,
              "italic_shift must be a whole number of game pixels from 0 to 4")
        check(isinstance(self.em_pixels, int) and 4 <= self.em_pixels <= 32, "em_pixels must be from 4 to 32")
        check(isinstance(self.units_per_pixel, int) and self.units_per_pixel >= 2
              and self.units_per_pixel * self.em_pixels <= 16384,
              "units_per_pixel × em_pixels must be at most 16384")
        check(isinstance(self.jobs, int) and self.jobs >= 0, "jobs must be 0 (automatic) or more")
        check(self.render_profile == "auto" or self.render_profile in PROFILES,
              f"render_profile must be auto or one of {', '.join(PROFILES)}")
        for size in self.embedded_bitmaps:
            check(isinstance(size, int) and size > 0 and size % self.em_pixels == 0 and size // self.em_pixels <= 4,
                  f"embedded bitmap size {size} must be 1 to 4 times em_pixels ({self.em_pixels})")
        if self.build_date:
            try:
                datetime.strptime(self.build_date, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"build_date must look like 2026-01-31, not {self.build_date!r}") from None
        for rules in [self.characters, *map(CharacterRules.from_dict, self.characters_per_font.values())]:
            for plane in rules.exclude_planes:
                check(isinstance(plane, int) and 0 <= plane <= 16, f"exclude_planes: {plane!r} is not a plane from 0 to 16")
            for spec in rules.exclude_ranges + (rules.only_ranges or []):
                parse_range(str(spec))

    def to_dict(self) -> dict:
        return asdict(self)


def _upgrade(d: dict) -> tuple[dict, list[str]]:
    """Settings files from version 0.1, translated. Missing keys get the old defaults."""
    old = {"vanilla_path", "minecraft_dir", "options", "charset", "charset_overrides", "naming", "metadata",
           "output_dir", "metrics", "bitmap_strikes", "gasp_mono_below", "alpha_threshold"}
    if not old & set(d) and not isinstance(d.get("shadow"), dict) and not isinstance(d.get("render_profile"), dict):
        return d, []
    d = copy.deepcopy(d)
    notes = []

    def move(src: str, dst: str) -> None:
        if src in d:
            d[dst] = d.pop(src)
            notes.append(f"{src} → {dst}")

    d.setdefault("fonts", ["minecraft:default"])
    d.setdefault("charset", {})
    move("vanilla_path", "game_path")
    move("output_dir", "output")
    move("bitmap_strikes", "embedded_bitmaps")
    move("charset_overrides", "characters_per_font")
    if "minecraft_dir" in d:
        d["launcher_dir"] = d.pop("minecraft_dir") or "auto"
        notes.append("minecraft_dir → launcher_dir")
    if "options" in d:
        o = d.pop("options") or {}
        d["force_unicode_font"] = bool(o.get("uniform"))
        d["japanese_glyph_variants"] = bool(o.get("jp"))
        notes.append("options → force_unicode_font / japanese_glyph_variants")
    if "charset" in d:
        c = {"exclude_planes": [], "keep_blocks": [], **dict(d.pop("charset") or {})}
        if "include_blocks" in c:
            c["only_blocks"] = c.pop("include_blocks")
        if "include_ranges" in c:
            c["only_ranges"] = c.pop("include_ranges")
        d["characters"] = c
        notes.append("charset → characters")
    for per in (d.get("characters_per_font") or {}).values():
        if isinstance(per, dict):
            per.setdefault("exclude_planes", [])
            per.setdefault("keep_blocks", [])
            if "include_blocks" in per:
                per["only_blocks"] = per.pop("include_blocks")
            if "include_ranges" in per:
                per["only_ranges"] = per.pop("include_ranges")
    if "naming" in d:
        n = d.pop("naming") or {}
        d["family_names"] = n.get("families") or {}
        d["family_suffix"] = n.get("suffix") or ""
        notes.append("naming → family_names / family_suffix")
    if "metadata" in d:
        m = d.pop("metadata") or {}
        d["copyright"] = m.get("copyright")
        d["license"] = m.get("license")
        notes.append("metadata → copyright / license")
    if "metrics" in d:
        d["line_metrics"] = {"bbox": "fit"}.get(d.pop("metrics"), "centered")
        notes.append("metrics → line_metrics")
    if isinstance(d.get("shadow"), dict):
        s = d.pop("shadow")
        d["shadow"] = bool(s.get("enabled"))
        d["shadow_format"] = s.get("mode", "colrv1")
        d["shadow_color"] = s.get("color", "#3F3F3F")
        notes.append("shadow.* → shadow / shadow_format / shadow_color")
    if isinstance(d.get("render_profile"), dict):
        d["render_profile"] = d["render_profile"].get("name") or "auto"
    for gone in ("gasp_mono_below", "alpha_threshold"):
        if d.pop(gone, None) is not None:
            notes.append(f"{gone} (no longer used)")
    return d, notes


def from_dict(d: Optional[dict], warn=lambda m: print(m, file=sys.stderr)) -> Config:
    d, notes = _upgrade(dict(d or {}))
    if notes:
        warn("note: settings from an older version were converted: " + ", ".join(notes))
    unknown = set(d) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    for name in TEXT_SETTINGS:  # YAML reads 1.20 as a number, keep what was written
        value = d.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            d[name] = getattr(value, "text", str(value))
    d["characters"] = CharacterRules.from_dict(d.get("characters"))
    cfg = Config(**d)
    for name in ("fonts", "styles", "formats", "resource_packs"):
        if isinstance(getattr(cfg, name), str):
            setattr(cfg, name, [getattr(cfg, name)])
    if cfg.launcher_dir in (None, ""):
        cfg.launcher_dir = "auto"
    cfg.validate()
    return cfg


def parse_text(text: str, json_format: bool = False) -> dict:
    """The settings in a YAML or JSON text. Numbers and dates keep how they were written."""
    if json_format:
        data = json.loads(text)
    else:
        import yaml

        class Loader(yaml.SafeLoader):
            pass

        class Number(float):
            text = ""

        def number(loader, node):
            value = Number(loader.construct_yaml_float(node))
            value.text = node.value
            return value

        Loader.add_constructor("tag:yaml.org,2002:float", number)
        Loader.add_constructor("tag:yaml.org,2002:timestamp", lambda loader, node: node.value)  # dates as written
        data = yaml.load(text, Loader=Loader) or {}  # noqa: S506 - a SafeLoader
    if not isinstance(data, dict):
        raise ValueError("this is not a settings file")
    return data


def load(path: Optional[str]) -> Config:
    if not path:
        return Config()
    p = Path(path)
    try:
        return from_dict(parse_text(p.read_text("utf-8"), p.suffix.lower() == ".json"))
    except ValueError as e:
        raise ValueError(f"{p.name}: {e}") from None


def _hide_home(value):
    """The home folder as ~, so user names stay out of build outputs."""
    home = str(Path.home())
    if isinstance(value, str) and home not in ("", "/"):
        if value == home:
            return "~"
        for sep in ("/", "\\"):
            if value.startswith(home + sep):
                return "~" + value[len(home):]
    if isinstance(value, list):
        return [_hide_home(v) for v in value]
    return value


def dumps(cfg: Config, json_format: bool = False) -> str:
    """The settings as YAML or JSON text, with the home folder written as ~."""
    d = cfg.to_dict()
    for k in ("game_path", "launcher_dir", "cache_dir", "resource_packs", "output"):
        d[k] = _hide_home(d[k])
    if json_format:
        return json.dumps(d, indent=2, ensure_ascii=False) + "\n"
    import yaml

    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


def dump(cfg: Config, path: Path) -> None:
    path.write_text(dumps(cfg, path.suffix.lower() == ".json"), "utf-8")


TEMPLATE = """\
# Minecraft Font Builder settings. Every option is listed with its default value.

# Game files
version: latest            # latest, latest-snapshot, or a version such as 1.21.4
game_path: null            # a local client.jar or extracted folder to use instead of downloading
launcher_dir: auto         # reuse files the Minecraft launcher already downloaded: auto, off, or a folder
cache_dir: null            # where downloads are kept (null: your user cache folder)
offline: false             # use downloaded files only
resource_packs: []         # resource packs or mod jars, lowest priority first

# What to build
# Fonts a version doesn't have are skipped. "all" means every font except the include/ fragments.
fonts: [minecraft:alt, minecraft:default, minecraft:illageralt, minecraft:uniform]
force_unicode_font: false        # the game option "Force Unicode Font"
japanese_glyph_variants: false   # the game option "Japanese Glyph Variants"
styles: [regular, bold, italic, bold_italic]
formats: [ttf]             # ttf, otf, woff2
output: output             # output folder

# Characters
# A font file holds at most 65535 glyphs, and the build stops without writing anything
# when a style needs more. The defaults leave out Unicode plane 1 (mostly historic scripts
# and placeholder boxes) but keep emoji and other symbols, which fits as much as possible.
# Set exclude_planes and keep_blocks to [] to start from every character.
characters:
  exclude_planes: [1]      # Unicode planes
  exclude_blocks: []       # Unicode block names, or patterns such as "CJK*"
  exclude_ranges: []       # e.g. ["U+E000..U+F8FF"]
  exclude_unassigned: false  # leave out Unifont's placeholder boxes for unassigned code points
  exclude_providers: []    # leave out everything a provider supplies, e.g. ["unifont.zip"]
  keep_blocks: [           # always kept, whatever the rules above say
    Miscellaneous Symbols and Pictographs, Emoticons, Transport and Map Symbols,
    Supplemental Symbols and Pictographs, Symbols and Pictographs Extended-A,
    Enclosed Alphanumeric Supplement, Enclosed Ideographic Supplement,
    Mathematical Alphanumeric Symbols, Playing Cards, Mahjong Tiles, Chess Symbols,
    Geometric Shapes Extended, Small Kana Extension]
  only_blocks: null        # if set, only these blocks
  only_ranges: null        # if set, only these ranges
characters_per_font: {}    # other rules for one font, e.g. {"minecraft:alt": {"exclude_planes": []}}

# Names
family_names: {}           # e.g. {"minecraft:default": "Mojangles"}, otherwise the built-in names
family_suffix: ""          # added to every family name, e.g. "Local"

# Metadata
# null means automatic, "" writes nothing. The other fields are written only when set.
copyright: null            # automatic: Copyright Mojang AB, plus the Unifont authors when Unifont glyphs are used
license: null              # automatic: a Unifont license note when Unifont glyphs are used
license_url: null          # automatic: the Unifont license address
font_version: null         # null: the game version, e.g. 26.3
designer: ""
designer_url: ""
manufacturer: ""
manufacturer_url: ""
description: ""

# Appearance
line_metrics: centered     # centered: text centered by Apps sits like on the game's buttons
                           # game: the line starts where the game starts drawing
                           # fit: the line covers every glyph (nothing clipped, taller lines)
center_glyphs: false       # move glyphs right by half a pixel so centered text is centered
                           # (then only 16 and 32 px are perfectly sharp)
italic_shift: 1            # move italics right by whole pixels so line starts are not clipped (0: as in the game)
em_pixels: 8               # 8: sharp at 8, 16, 24 and 32 px. 10: closer to the size of other fonts
shadow: false              # add the game's text shadow (the family name gets "Shadow")
shadow_format: colrv1      # colrv1: follows the text color, no extra glyphs. colrv0: one fixed color, extra glyphs
shadow_color: "#3F3F3F"    # colrv0 only
embedded_bitmaps: []       # bitmap sizes in pixels per em, e.g. [8, 16]

# Advanced
units_per_pixel: 120       # font units per game pixel (units per em = em_pixels x this)
render_profile: auto       # bold and italic rules: auto (by version), 1.13-1.21.1 or 1.21.2+
jobs: 0                    # styles built at the same time (0: automatic)
build_date: null           # YYYY-MM-DD written into the fonts (null: $SOURCE_DATE_EPOCH or today)
"""