"""A build from start to end: game files, characters, glyph counts, font files and the report."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import budget as budget_mod
from .characters import format_range, select, to_ranges
from .config import Config, dump
from .fontfile import FontInfo, FontSettings, build_font, plan_glyphs, save
from .gamefiles import GameFiles, open_game
from .glyphs import STYLE_NAMES, Glyph, is_bold
from .mojang import MojangRepository, default_launcher_dir
from .parallel import auto_jobs
from .parallel import run as run_parallel
from .profiles import RenderProfile, select_profile
from .providers import FontLoader, ResolvedFont, UnihexProvider

Log = Callable[[str], None]

# format: (file extension, fontTools flavor, outlines)
FORMAT_FILES = {
    "ttf": ("ttf", None, "ttf"),
    "otf": ("otf", None, "otf"),
    "woff2": ("woff2", "woff2", "ttf"),
}

# "Mojangles" is the usual name of the game's font. The others share it as a prefix.
BUILTIN_FAMILIES = {
    "minecraft:default": "Mojangles",
    "minecraft:alt": "Mojangles Galactic",
    "minecraft:illageralt": "Mojangles Illageralt",
}


class GlyphLimitError(RuntimeError):
    """A style needs more than 65535 glyphs. Nothing was written."""


def log_stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def make_repo(cfg: Config, log: Log) -> MojangRepository:
    if cfg.launcher_dir == "auto":
        launcher = default_launcher_dir()
    elif cfg.launcher_dir in ("off", "none", "", None):
        launcher = None
    else:
        launcher = Path(cfg.launcher_dir).expanduser()
    return MojangRepository(cache_dir=Path(cfg.cache_dir).expanduser() if cfg.cache_dir else None,
                            offline=cfg.offline, launcher_dir=launcher, log=log)


def open_game_files(cfg: Config, log: Log = log_stderr, version: Optional[str] = None) -> GameFiles:
    """The game files of the settings, or of *version* from Mojang (for diff)."""
    return open_game(version=version or cfg.version, game_path=cfg.game_path if version is None else None,
                     resource_packs=cfg.resource_packs, repo=make_repo(cfg, log), log=log)


def render_profile(cfg: Config, game: GameFiles) -> tuple[RenderProfile, list[str]]:
    return select_profile(game.release_time, cfg.render_profile)


@dataclass
class FontSelection:
    font_id: str
    resolved: ResolvedFont
    available: dict[int, Glyph]  # every character the game has in this font
    glyphs: dict[int, Glyph]  # the characters that go in
    removed: dict[str, int]  # left out, per reason
    kept: dict[str, int] = field(default_factory=dict)  # saved by keep_blocks, per reason
    counts: dict[str, budget_mod.StyleBudget] = field(default_factory=dict)  # per style


def select_font(cfg: Config, loader: FontLoader, font_id: str) -> FontSelection:
    resolved = loader.load(font_id, cfg.game_options)
    available = resolved.glyph_map()
    kept: dict[str, int] = {}
    chosen, removed = select(available, cfg.rules_for(font_id), kept)
    return FontSelection(font_id, resolved, available, chosen, removed, kept)


def shadow_adds_glyphs(cfg: Config) -> bool:
    """Only the fixed-color colrv0 shadow needs a glyph of its own for every shape."""
    return cfg.shadow and cfg.shadow_format == "colrv0"


def count_glyphs(cfg: Config, sel: FontSelection, profile: RenderProfile) -> None:
    # italic leans every glyph the same way, so it has the count of the upright style
    by_weight: dict[bool, budget_mod.StyleBudget] = {}
    for st in cfg.styles:
        bold = is_bold(st)
        if bold not in by_weight:
            by_weight[bold] = budget_mod.compute(sel.glyphs, st, profile.bold_extra_thickness, shadow_adds_glyphs(cfg))
        sel.counts[st] = dataclasses.replace(by_weight[bold], style=st)


def font_title(font_id: str) -> str:
    """"minecraft:include/unifont" -> "Include Unifont"."""
    ns, _, path = font_id.partition(":")
    title = " ".join(w[:1].upper() + w[1:] for w in re.split(r"[/_\-.]+", path) if w)
    return title if ns == "minecraft" else f"{ns[:1].upper()}{ns[1:]} {title}"


def family_name(cfg: Config, font_id: str) -> str:
    base = cfg.family_names.get(font_id) or BUILTIN_FAMILIES.get(font_id) or f"Mojangles {font_title(font_id)}"
    tags = [tag for on, tag in ((cfg.force_unicode_font, "Unicode"), (cfg.japanese_glyph_variants, "JP"),
                                (cfg.shadow, "Shadow")) if on]
    return " ".join(p for p in (base, *tags, cfg.family_suffix) if p).strip()


def file_family(family: str) -> str:
    """The family as it appears in file and PostScript names: "Mojangles Galactic" -> "MojanglesGalactic"."""
    return re.sub(r"[^A-Za-z0-9]", "", family)


def file_stem(cfg: Config, font_id: str) -> str:
    """The start of a font's file names, from the id when the family has no Latin letters."""
    return file_family(family_name(cfg, font_id)) or file_family(f"Mojangles {font_title(font_id)}") or "Font"


def is_include(font_id: str) -> bool:
    return font_id.partition(":")[2].startswith("include/")


def expand_fonts(cfg: Config, game: GameFiles, warn: Optional[Log] = None) -> list[str]:
    """The font ids to build: "all" means every font but the include/ fragments.

    Fonts the version doesn't have are skipped, older versions lack some of the defaults.
    """
    available = game.rm.font_ids()
    out: list[str] = []
    missing: list[str] = []
    for f in cfg.fonts:
        if f == "all":
            ids = [x for x in available if not is_include(x)]
        else:
            fid = f if ":" in f else f"minecraft:{f}"
            if fid not in available:
                missing.append(fid)
                continue
            ids = [fid]
        out.extend(x for x in ids if x not in out)
    if missing and warn:
        warn(f"not in Minecraft {game.version_id}, skipped: {', '.join(missing)}")
    if not out:
        raise ValueError(f"no fonts to build: none of {', '.join(cfg.fonts) or 'the fonts'} are in "
                         f"Minecraft {game.version_id} (see mcfont fonts)")
    return out


def build_date(cfg: Config) -> datetime:
    if cfg.build_date:
        return datetime.strptime(cfg.build_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return datetime.fromtimestamp(int(os.environ["SOURCE_DATE_EPOCH"]), tz=timezone.utc)
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


@dataclass
class UnifontNotice:
    license_text: str  # the license file from the game's Unifont archive
    license_url: str


def _used_providers(sel: FontSelection) -> list:
    used = {g.provider for g in sel.glyphs.values()}
    return [c.provider for c in sel.resolved.providers if c.provider.label in used]


def unifont_notice(sel: FontSelection) -> Optional[UnifontNotice]:
    """The Unifont license, when some of the glyphs come from Unifont."""
    unifont = [p for p in _used_providers(sel) if isinstance(p, UnihexProvider)]
    if not unifont:
        return None
    text = next((t for p in unifont for t in p.license_texts.values()), "")
    m = re.search(r"https?://scripts\.sil\.org/OFL\S*", text)
    return UnifontNotice(text, m.group(0).rstrip('".,)') if m else "")


def font_info(cfg: Config, sel: FontSelection, game: GameFiles, date: datetime,
              notice: Optional[UnifontNotice]) -> FontInfo:
    family = family_name(cfg, sel.font_id)
    auto_copyright = []
    if any(p.kind == "bitmap" for p in _used_providers(sel)):
        auto_copyright.append("Copyright Mojang AB")  # as on the game's title screen
    if notice:
        auto_copyright.append("Unifont glyphs copyright Roman Czyborra, Paul Hardy et al.")
    auto_license = "Unifont glyphs: SIL OFL 1.1, or GNU GPL v2+ with the font embedding exception." if notice else ""
    auto_url = notice.license_url if notice and cfg.license is None else ""
    return FontInfo(
        family=family,
        ps_family=file_stem(cfg, sel.font_id),
        game_version=game.version_id,
        build_date=date,
        copyright=". ".join(auto_copyright) if cfg.copyright is None else cfg.copyright,
        license=auto_license if cfg.license is None else cfg.license,
        license_url=auto_url if cfg.license_url is None else cfg.license_url,
        version=cfg.font_version or "",
        designer=cfg.designer,
        designer_url=cfg.designer_url,
        manufacturer=cfg.manufacturer,
        manufacturer_url=cfg.manufacturer_url,
        description=cfg.description,
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(cfg: Config, log: Log = log_stderr) -> dict:
    """Write every font, style and format of the settings, and return the build report."""
    game = open_game_files(cfg, log)
    profile, warnings = render_profile(cfg, game)
    for w in warnings:
        log(f"warning: {w}")
    loader = FontLoader(game.rm, warn=lambda m: log(f"warning: {m}"))
    out_dir = Path(cfg.output).expanduser()
    date = build_date(cfg)
    settings = FontSettings(
        upp=cfg.units_per_pixel, em_pixels=cfg.em_pixels, italic_shift=cfg.italic_shift,
        x_offset=0.5 if cfg.center_glyphs else 0.0, line_metrics=cfg.line_metrics,
        shadow=cfg.shadow, shadow_format=cfg.shadow_format, shadow_color=cfg.shadow_color,
        shadow_factor=profile.shadow_color_factor, embedded_bitmaps=list(cfg.embedded_bitmaps),
    )

    selections, over = [], []
    for fid in expand_fonts(cfg, game, warn=lambda m: log(f"warning: {m}")):
        sel = select_font(cfg, loader, fid)
        count_glyphs(cfg, sel, profile)
        selections.append(sel)
        for st, b in sel.counts.items():
            log(f"{family_name(cfg, fid)} {STYLE_NAMES[st]}: {b.glyphs} / {budget_mod.GLYPH_LIMIT} glyphs")
            if not b.fits:
                over.append((sel, st, b))
    stems: dict[str, str] = {}
    for sel in selections:
        stem = file_stem(cfg, sel.font_id)
        if stem in stems:
            raise ValueError(f"{stems[stem]} and {sel.font_id} would both be written as {stem}, "
                             "give one of them another family name")
        stems[stem] = sel.font_id
    if over:
        lines = ["Too many glyphs. Nothing was written."]
        for sel, st, b in over:
            lines.append(f"  {family_name(cfg, sel.font_id)} {STYLE_NAMES[st]}: {b.glyphs} glyphs, "
                         f"{b.over} over the limit of {budget_mod.GLYPH_LIMIT}")
            for i, s in enumerate(budget_mod.suggest_cuts(b, shadow_adds_glyphs(cfg)), 1):
                lines.append(f"    suggestion {i}: {budget_mod.format_suggestion(s, 12)}")
        lines.append("Run `mcfont count` for details, then change `characters` in your settings.")
        raise GlyphLimitError("\n".join(lines))

    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "game_version": game.version_id,
        "release_time": game.release_time,
        "source": game.origin,
        "render_profile": {"name": profile.name, "provenance": profile.provenance, "verified": profile.verified},
        "build_date": date.strftime("%Y-%m-%d"),
        "units_per_em": settings.upm,
        "fonts": [],
        "warnings": list(dict.fromkeys(loader.warnings + warnings)),
    }
    tasks: list[dict] = []
    for sel in selections:
        notice = unifont_notice(sel)
        if notice and notice.license_text:
            (out_dir / "LICENSE-Unifont.txt").write_text(notice.license_text, "utf-8")
        info = font_info(cfg, sel, game, date, notice)
        sources = {path: hashlib.sha1(data).hexdigest()
                   for p in _used_providers(sel) for path in p.files() if (data := game.rm.read(path)) is not None}
        report["fonts"].append({
            "font_id": sel.font_id, "family": info.family, "codepoints": len(sel.glyphs),
            "removed": sel.removed, "kept": sel.kept, "game_files_sha1": sources,
            "coverage": [format_range(a, b) for a, b in to_ranges(sel.glyphs)], "styles": {},
        })
        for st in cfg.styles:
            tasks.append({
                "key": (len(report["fonts"]) - 1, st), "size": len(sel.glyphs),
                "glyphs": sel.glyphs, "style": st, "profile": profile, "settings": settings, "info": info,
                "formats": [FORMAT_FILES[f] for f in cfg.formats], "out_dir": str(out_dir),
                "stem": f"{info.ps_family}-{STYLE_NAMES[st].replace(' ', '')}",
                "label": f"{info.family} {STYLE_NAMES[st]}",
            })

    tasks.sort(key=lambda t: -t["size"])  # every style is independent, the largest go first
    jobs = auto_jobs(len(tasks), cfg.jobs, big=any(t["size"] > 5000 for t in tasks))
    log(f"writing {len(tasks)} style(s) with {jobs} parallel job(s)")
    finished = [0]

    def on_done(i: int, result: dict) -> None:
        finished[0] += 1
        log(f"[{finished[0]}/{len(tasks)}] {tasks[i]['label']}: {', '.join(result['files'])}")

    for task, result in zip(tasks, run_parallel(build_style_task, tasks, jobs, on_done)):
        i, st = task["key"]
        report["fonts"][i]["styles"][st] = result
    for entry in report["fonts"]:
        entry["styles"] = {st: entry["styles"][st] for st in cfg.styles}
    dump(cfg, out_dir / "build-config.yaml")
    (out_dir / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return report


def build_style_task(task: dict) -> dict:
    """Every format of one font style. Runs in a worker process."""
    started = time.monotonic()
    plan = plan_glyphs(task["glyphs"], task["style"], task["profile"], task["settings"])
    built: dict = {}
    files: dict[str, str] = {}
    for ext, flavor, outlines in task["formats"]:
        if outlines not in built:
            records, cmap, layers = plan
            # the COLR builder changes the layers it is given, so every font gets its own copy
            built[outlines] = build_font(task["glyphs"], task["style"], task["profile"], task["settings"],
                                         task["info"], outlines, plan=(records, cmap, copy.deepcopy(layers)))
        path = save(built[outlines], Path(task["out_dir"]) / f"{task['stem']}.{ext}", flavor)
        files[path.name] = sha256(path)
    return {"glyphs": len(plan[0]), "files": files, "seconds": round(time.monotonic() - started, 1)}