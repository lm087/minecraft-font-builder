"""The app: a page and a JSON API, served on 127.0.0.1 by mcfont gui.

Every API call needs the random token from the address the browser opens, and requests
for any host but the local address are refused, so other web pages can't use the server.
"""

from __future__ import annotations

import json
import mimetypes
from io import BytesIO
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from .. import __version__
from .. import budget as budget_mod
from ..builder import (BUILTIN_FAMILIES, GlyphLimitError, build, count_glyphs, expand_fonts, family_name,
                       is_include, make_repo, open_game_files, render_profile, select_font, shadow_adds_glyphs)
from ..config import FORMATS, Config, dumps, from_dict, parse_text
from ..glyphs import STYLES, is_bold
from ..mojang import default_launcher_dir
from ..providers import FontLoader
from ..unicode_blocks import block_of

STATIC = Path(__file__).parent / "static"


def _plain(cfg: Config) -> dict:
    return json.loads(json.dumps(cfg.to_dict(), default=str))


class ApiError(Exception):
    def __init__(self, msg: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(msg)
        self.status = status


class BuildJob:
    def __init__(self) -> None:
        self.state = "idle"  # idle, running, done, too-many-glyphs or error
        self.log: list[str] = []
        self.error = ""
        self.report: Optional[dict] = None
        self.out_dir: Optional[Path] = None
        self.started = 0.0
        self.finished = 0.0
        self.lock = threading.Lock()

    def append(self, msg: str) -> None:
        with self.lock:
            self.log.append(msg)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            files = []
            if self.report:
                for f in self.report["fonts"]:
                    for st, info in f["styles"].items():
                        files += [{"family": f["family"], "style": st, "file": name} for name in info["files"]]
            return {
                "state": self.state, "log": self.log[since:], "log_total": len(self.log), "error": self.error,
                "out_dir": str(self.out_dir) if self.out_dir else None, "files": files,
                "elapsed": ((self.finished or time.time()) - self.started) if self.started else 0,
            }


class App:
    """What all requests share: the loaded game, cached counts and the build."""

    def __init__(self, workdir: Path, initial: Optional[Config] = None) -> None:
        self.token = secrets.token_urlsafe(24)
        self.workdir = workdir
        self.initial = initial
        self.lock = threading.Lock()
        self.games: dict = {}  # game key: (GameFiles, FontLoader, profile, warnings), only the latest
        self.font_sizes: dict = {}  # (game key, options): {font id: (characters, error)}
        self.full_counts: dict = {}  # counts over every character a font has, for the block table
        self.job = BuildJob()
    @staticmethod
    def _game_key(cfg: Config) -> str:
        return json.dumps([cfg.version, cfg.game_path, cfg.launcher_dir, cfg.cache_dir, cfg.offline,
                           cfg.resource_packs, cfg.render_profile])

    def game(self, cfg: Config):
        key = self._game_key(cfg)
        with self.lock:
            if key not in self.games:
                game = open_game_files(cfg, log=lambda m: None)
                profile, warnings = render_profile(cfg, game)
                self.games = {key: (game, FontLoader(game.rm), profile, warnings)}
                self.font_sizes, self.full_counts = {}, {}
            return key, self.games[key]

    def output_path(self, cfg: Config) -> Path:
        p = Path(cfg.output).expanduser()
        return p if p.is_absolute() else self.workdir / p

    def api_start(self, body: dict) -> dict:
        launcher = default_launcher_dir()
        return {
            "app_version": __version__, "workdir": str(self.workdir), "settings": _plain(self.initial or Config()),
            "styles": list(STYLES), "formats": list(FORMATS), "builtin_families": BUILTIN_FAMILIES,
            "defaults": _plain(Config()), "limit": budget_mod.GLYPH_LIMIT, "launcher_dir": str(launcher) if launcher else None,
        }

    def api_versions(self, body: dict) -> dict:
        repo = make_repo(from_dict(body.get("settings") or {}), lambda m: None)
        latest = repo.manifest()["latest"]
        versions = repo.versions(include_snapshots=bool(body.get("snapshots")))
        return {"latest": latest, "versions": [{"id": v.id, "type": v.type, "date": v.release_time[:10]} for v in versions]}

    def api_fonts(self, body: dict) -> dict:
        cfg = from_dict(body["settings"])
        key, (game, loader, profile, warnings) = self.game(cfg)
        sizes_key = (key, cfg.force_unicode_font, cfg.japanese_glyph_variants)
        if sizes_key not in self.font_sizes:  # names change often, the characters don't
            sizes = {}
            for fid in game.rm.font_ids():
                try:
                    sizes[fid] = (len(loader.load(fid, cfg.game_options).glyph_map()), "")
                except Exception as e:  # noqa: BLE001
                    sizes[fid] = (0, str(e))
            self.font_sizes[sizes_key] = sizes
        fonts = [{"id": fid, "include": is_include(fid), "codepoints": n, "family": family_name(cfg, fid), "error": err}
                 for fid, (n, err) in self.font_sizes[sizes_key].items()]
        return {"game": {"version": game.version_id, "release_time": game.release_time, "origin": game.origin},
                "profile": profile.name, "warnings": warnings, "fonts": fonts}

    def api_count(self, body: dict) -> dict:
        cfg = from_dict(body["settings"])
        key, (game, loader, profile, warnings) = self.game(cfg)
        bold = any(is_bold(s) for s in cfg.styles)
        out = []
        warnings = list(warnings)
        for fid in expand_fonts(cfg, game, warn=warnings.append):
            sel = select_font(cfg, loader, fid)
            count_glyphs(cfg, sel, profile)
            # the block table shows every character, so left out blocks show what they would cost
            full_key = (key, fid, cfg.force_unicode_font, cfg.japanese_glyph_variants, bold, shadow_adds_glyphs(cfg))
            full = self.full_counts.get(full_key)
            if full is None:
                full = budget_mod.compute(sel.available, "bold" if bold else "regular", profile.bold_extra_thickness,
                                          shadow_adds_glyphs(cfg))
                self.full_counts[full_key] = full
            selected: dict[str, int] = {}
            for cp in sel.glyphs:
                b = block_of(cp)
                selected[b] = selected.get(b, 0) + 1
            planes: dict[int, dict] = {}
            for b in full.blocks:
                p = planes.setdefault(b.first_cp >> 16, {"plane": b.first_cp >> 16, "codepoints": 0, "selected": 0})
                p["codepoints"] += b.codepoints
                p["selected"] += selected.get(b.block, 0)
            worst = max(sel.counts.values(), key=lambda b: b.glyphs) if sel.counts else None
            out.append({
                "font_id": fid, "family": family_name(cfg, fid), "available": len(sel.available),
                "selected": len(sel.glyphs),
                "styles": {st: {"glyphs": b.glyphs, "over": b.over} for st, b in sel.counts.items()},
                "planes": sorted(planes.values(), key=lambda p: p["plane"]),
                "blocks": [{"block": b.block, "first": f"U+{b.first_cp:04X}", "last": f"U+{b.last_cp:04X}",
                            "plane": b.first_cp >> 16, "codepoints": b.codepoints, "glyphs": b.glyphs,
                            "selected": selected.get(b.block, 0)} for b in full.blocks],
                "suggestions": budget_mod.suggest_cuts(worst, shadow_adds_glyphs(cfg)) if worst and not worst.fits else [],
            })
        return {"game_version": game.version_id, "limit": budget_mod.GLYPH_LIMIT, "fonts": out, "warnings": warnings}

    def api_build(self, body: dict) -> dict:
        cfg = from_dict(body["settings"])
        out_dir = self.output_path(cfg)
        cfg.output = str(out_dir)
        with self.lock:
            if self.job.state == "running":
                raise ApiError("a build is already running", HTTPStatus.CONFLICT)
            job = BuildJob()
            job.state, job.out_dir, job.started = "running", out_dir, time.time()
            self.job = job

        def work() -> None:
            try:
                job.report = build(cfg, log=job.append)
                job.state = "done"
            except GlyphLimitError as e:
                job.error, job.state = str(e), "too-many-glyphs"
            except Exception as e:  # noqa: BLE001
                job.error, job.state = f"{type(e).__name__}: {e}", "error"
                job.append(traceback.format_exc())
            finally:
                job.finished = time.time()

        threading.Thread(target=work, daemon=True).start()
        return {"started": True, "out_dir": str(out_dir)}

    def api_build_status(self, body: dict) -> dict:
        return self.job.snapshot(int(body.get("since", 0)))

    def api_open_folder(self, body: dict) -> dict:
        target = self.job.out_dir if self.job.out_dir and self.job.out_dir.is_dir() else None
        if target is None:
            target = self.output_path(from_dict(body.get("settings") or {}))
        if not target.is_dir():
            raise ApiError(f"the folder does not exist yet: {target}")
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return {"opened": str(target)}

    def api_settings_read(self, body: dict) -> dict:
        return {"settings": _plain(from_dict(parse_text(body.get("text") or ""), warn=lambda m: None))}

    def api_settings_write(self, body: dict) -> dict:
        return {"text": dumps(from_dict(body["settings"]))}


ROUTES = {
    "/api/start": "api_start", "/api/versions": "api_versions", "/api/fonts": "api_fonts",
    "/api/count": "api_count", "/api/build": "api_build", "/api/build/status": "api_build_status",
    "/api/open-folder": "api_open_folder", "/api/settings/read": "api_settings_read",
    "/api/settings/write": "api_settings_write",
}

FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2"}


def missing_font(path: Path, codepoints: list[int]) -> bytes:
    """A font that draws *codepoints* with the .notdef box of the font at *path*.

    The preview lists it after the built font, so characters that font lacks show its box
    instead of a system font.
    """
    src = TTFont(str(path))
    pen = TTGlyphPen(None)
    src.getGlyphSet()[".notdef"].draw(pen)
    advance, lsb = src["hmtx"][".notdef"]
    hhea, os2 = src["hhea"], src["OS/2"]
    fb = FontBuilder(src["head"].unitsPerEm, isTTF=True)
    fb.setupGlyphOrder([".notdef", "box"])
    fb.setupCharacterMap({cp: "box" for cp in codepoints})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "box": pen.glyph()})
    fb.setupHorizontalMetrics({".notdef": (advance, 0), "box": (advance, lsb)})
    fb.setupHorizontalHeader(ascent=hhea.ascent, descent=hhea.descent)
    fb.setupOS2(sTypoAscender=os2.sTypoAscender, sTypoDescender=os2.sTypoDescender, sTypoLineGap=os2.sTypoLineGap,
                usWinAscent=os2.usWinAscent, usWinDescent=os2.usWinDescent)
    fb.setupNameTable({"familyName": "Missing", "styleName": "Regular"})
    fb.setupPost()
    out = BytesIO()
    fb.font.save(out)
    return out.getvalue()


def make_handler(app: App, port_ref: list):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mcfont"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}")

        def _send(self, status: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path.startswith("/files/"):
                if parse_qs(url.query).get("t", [""])[0] != app.token:
                    return self._send(403, b"forbidden", "text/plain")
                return self._font_file(url.path[len("/files/"):], parse_qs(url.query).get("missing", [None])[0])
            return self._send(404, b"not found", "text/plain")

        def _static(self, name: str):
            p = (STATIC / name).resolve()
            if STATIC.resolve() not in p.parents or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript") or ctype.endswith("svg+xml"):
                ctype += "; charset=utf-8"
            csp = "default-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; img-src 'self' data:"
            self._send(200, p.read_bytes(), ctype, {"Content-Security-Policy": csp})

        def _font_file(self, name: str, missing: Optional[str] = None):
            out = app.job.out_dir
            if not out or "/" in name or "\\" in name or name.startswith("."):
                return self._send(404, b"not found", "text/plain")
            p = out / name
            if p.suffix not in FONT_TYPES or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            if missing is None:
                return self._send(200, p.read_bytes(), FONT_TYPES[p.suffix])
            try:  # hex code points separated by dots
                cps = sorted({int(x, 16) for x in missing.split(".") if x})[:2000]
                if any(cp > 0x10FFFF for cp in cps):
                    raise ValueError
            except ValueError:
                return self._send(400, b"bad code points", "text/plain")
            self._send(200, missing_font(p, cps), "font/ttf")

        def do_POST(self):  # noqa: N802
            if not self._host_ok() or self.headers.get("X-Token") != app.token:
                return self._json(403, {"error": "forbidden"})
            method = ROUTES.get(urlparse(self.path).path)
            if method is None:
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                self._json(200, getattr(app, method)(body))
            except ApiError as e:
                self._json(int(e.status), {"error": str(e)})
            except (ValueError, KeyError, FileNotFoundError, OSError, RuntimeError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


# the browser keeps the language and theme per address, so the App tries the same port each time
DEFAULT_PORT = 8737


class _Server(ThreadingHTTPServer):
    # on Windows this option would let a second copy share a port that is already taken
    allow_reuse_address = sys.platform != "win32"


def _server(workdir: Path, initial: Optional[Config], port: Optional[int]):
    app = App(Path(workdir).resolve(), initial)
    port_ref = [0]
    handler = make_handler(app, port_ref)
    if port is None:  # the usual port, the next ones if it's taken, then any free one
        for candidate in [*range(DEFAULT_PORT, DEFAULT_PORT + 10), 0]:
            try:
                httpd = _Server(("127.0.0.1", candidate), handler)
                break
            except OSError:
                continue
    else:
        httpd = _Server(("127.0.0.1", port), handler)
    port_ref[0] = httpd.server_address[1]
    return httpd, app, port_ref[0]


def serve(port: Optional[int] = None, open_browser: bool = True, workdir: Optional[Path] = None,
          initial: Optional[Config] = None) -> None:
    httpd, app, port = _server(Path(workdir or os.getcwd()), initial, port)
    url = f"http://127.0.0.1:{port}/#t={app.token}"
    print(f"Minecraft Font Builder is running at {url}")
    print("Keep this window open while you use it. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        httpd.server_close()


def start_in_thread(workdir: Path, initial: Optional[Config] = None):
    """A server on a free port in a background thread, for the tests."""
    httpd, app, port = _server(workdir, initial, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, app, port