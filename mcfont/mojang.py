"""Downloads from Mojang, kept in a cache with the same layout as the launcher's folder.

Every file is checked against the SHA-1 Mojang publishes, when it is downloaded and when a
cached or launcher copy is reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RESOURCES_URL = "https://resources.download.minecraft.net"
USER_AGENT = "minecraft-font-builder"
APP_FOLDER = "minecraft-font-builder"
MANIFEST_MAX_AGE = 600  # seconds before the version list is downloaded again


class DownloadError(RuntimeError):
    pass


class IntegrityError(DownloadError):
    pass


def _cache_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def default_cache_dir() -> Path:
    if os.environ.get("MCFONT_CACHE"):
        return Path(os.environ["MCFONT_CACHE"])
    base = _cache_base()
    path = base / APP_FOLDER
    old = base / ("mcfont/cache" if sys.platform == "win32" else "mcfont")  # used by 0.1
    if not path.exists() and old.is_dir():
        try:
            old.rename(path)
        except OSError:
            return old
    return path


def default_launcher_dir() -> Optional[Path]:
    """The official launcher's .minecraft folder, if there is one."""
    if sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", "")) / ".minecraft"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "minecraft"
    else:
        p = Path.home() / ".minecraft"
    return p if p.is_dir() else None


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class VersionEntry:
    id: str
    type: str
    url: str
    sha1: Optional[str]
    release_time: str

    @classmethod
    def from_json(cls, d: dict) -> "VersionEntry":
        return cls(d["id"], d.get("type", "?"), d["url"], d.get("sha1"), d.get("releaseTime", ""))


class MojangRepository:
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        offline: bool = False,
        launcher_dir: Optional[Path] = None,
        log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
        retries: int = 3,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.offline = offline
        self.launcher_dir = Path(launcher_dir).expanduser() if launcher_dir else None
        self.log = log
        self.retries = retries
        self._manifest: Optional[dict] = None

    def _download(self, url: str, dest: Path, sha1: Optional[str] = None, size: Optional[int] = None) -> Path:
        if self.offline:
            raise DownloadError(f"offline: {dest.name} is not downloaded yet ({url})")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"downloading {url}")
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
            try:
                with os.fdopen(fd, "wb") as out:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        shutil.copyfileobj(resp, out, 1 << 20)
                got_size = os.path.getsize(tmp)
                if size is not None and got_size != size:
                    raise IntegrityError(f"{url}: expected {size} bytes, got {got_size}")
                if sha1 is not None and (got := sha1_file(Path(tmp))) != sha1:
                    raise IntegrityError(f"{url}: SHA-1 should be {sha1}, got {got}")
                os.replace(tmp, dest)
                return dest
            except urllib.error.HTTPError as e:
                if e.code < 500:  # a missing file does not appear on a second try
                    raise DownloadError(f"failed to download {url}: {e}") from e
                last = e
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = e
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            if attempt < self.retries:
                time.sleep(2 ** attempt)
        raise DownloadError(f"failed to download {url}: {last}")

    def _cached(self, dest: Path, url: str, sha1: Optional[str], size: Optional[int] = None,
                alternatives: tuple[Path, ...] = ()) -> Path:
        """A checked local copy: the cache, the launcher's copy, or a new download."""
        for cand in (dest, *alternatives):
            if cand.is_file():
                if sha1 is None or sha1_file(cand) == sha1:
                    return cand
                self.log(f"warning: {cand} does not match its SHA-1, ignoring it")
        return self._download(url, dest, sha1, size)

    def _launcher_copy(self, *parts: str) -> tuple[Path, ...]:
        return (self.launcher_dir.joinpath(*parts),) if self.launcher_dir else ()

    def manifest(self, refresh: bool = False) -> dict:
        if self._manifest is not None and not refresh:
            return self._manifest
        path = self.cache_dir / "version_manifest_v2.json"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < MANIFEST_MAX_AGE
        if not self.offline and (refresh or not fresh):
            try:
                self._download(MANIFEST_URL, path)
            except DownloadError as e:
                if not path.is_file():
                    raise
                self.log(f"warning: could not update the version list ({e}), using the saved one")
        if not path.is_file():
            raise DownloadError("the version list is not downloaded yet and offline is on")
        self._manifest = json.loads(path.read_text("utf-8"))
        return self._manifest

    def versions(self, include_snapshots: bool = False) -> list[VersionEntry]:
        types = ("release", "snapshot") if include_snapshots else ("release",)
        return [VersionEntry.from_json(d) for d in self.manifest()["versions"] if d.get("type") in types]

    def resolve(self, spec: str) -> VersionEntry:
        spec = spec.strip()
        try:
            m = self.manifest()
        except DownloadError:
            saved = self.cache_dir / "versions" / spec / f"{spec}.json"
            if self.offline and saved.is_file():
                d = json.loads(saved.read_text("utf-8"))
                return VersionEntry(spec, d.get("type", "?"), "", None, d.get("releaseTime", ""))
            raise
        spec = {"latest": "release", "latest-release": "release", "release": "release",
                "latest-snapshot": "snapshot", "snapshot": "snapshot"}.get(spec, spec)
        if spec in ("release", "snapshot"):
            spec = m["latest"][spec]
        for d in m["versions"]:
            if d["id"] == spec:
                return VersionEntry.from_json(d)
        raise DownloadError(f"unknown version {spec!r} (see mcfont versions --snapshots)")

    def version_json(self, entry: VersionEntry) -> dict:
        dest = self.cache_dir / "versions" / entry.id / f"{entry.id}.json"
        if not entry.url:  # resolved offline from the saved copy
            if dest.is_file():
                return json.loads(dest.read_text("utf-8"))
            raise DownloadError(f"the files of {entry.id} are not downloaded yet")
        alts = self._launcher_copy("versions", entry.id, f"{entry.id}.json")
        return json.loads(self._cached(dest, entry.url, entry.sha1, alternatives=alts).read_text("utf-8"))

    def client_jar(self, entry: VersionEntry) -> Path:
        dl = self.version_json(entry)["downloads"]["client"]
        dest = self.cache_dir / "versions" / entry.id / f"{entry.id}.jar"
        alts = self._launcher_copy("versions", entry.id, f"{entry.id}.jar")
        return self._cached(dest, dl["url"], dl["sha1"], dl.get("size"), alternatives=alts)

    def asset_index(self, entry: VersionEntry) -> dict:
        ai = self.version_json(entry)["assetIndex"]
        dest = self.cache_dir / "assets" / "indexes" / f"{ai['id']}.json"
        alts = self._launcher_copy("assets", "indexes", f"{ai['id']}.json")
        return json.loads(self._cached(dest, ai["url"], ai["sha1"], ai.get("size"), alternatives=alts).read_text("utf-8"))

    def asset_object(self, sha1: str, size: Optional[int] = None) -> Path:
        rel = ("assets", "objects", sha1[:2], sha1)
        return self._cached(self.cache_dir.joinpath(*rel), f"{RESOURCES_URL}/{sha1[:2]}/{sha1}", sha1, size,
                            alternatives=self._launcher_copy(*rel))