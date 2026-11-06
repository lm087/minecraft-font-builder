"""The game's files: the client jar, the launcher's asset objects and resource packs.

Packs are stacked lowest priority first, the game itself at the bottom. ``read`` returns the
top copy of a file, ``read_stack`` every copy (font definitions from all packs are combined).
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol

from .mojang import MojangRepository


def parse_identifier(ident: str, default_ns: str = "minecraft") -> tuple[str, str]:
    """"minecraft:font/ascii.png" -> ("minecraft", "font/ascii.png")"""
    ns, sep, path = ident.partition(":")
    return (ns, path) if sep else (default_ns, ident)


def asset_path(ident: str, prefix: str = "", suffix: str = "") -> str:
    ns, path = parse_identifier(ident)
    return f"assets/{ns}/{prefix}{path}{suffix}"


class Pack(Protocol):
    pack_id: str

    def read(self, path: str) -> Optional[bytes]: ...

    def list(self, prefix: str) -> Iterable[str]: ...


class DirectoryPack:
    """A folder that contains assets/: a resource pack or an extracted client jar."""

    def __init__(self, root: Path, pack_id: Optional[str] = None) -> None:
        self.root = Path(root)
        if self.root.name == "assets" and not (self.root / "assets").is_dir():
            self.root = self.root.parent
        self.pack_id = pack_id or f"dir:{self.root.name}"

    def read(self, path: str) -> Optional[bytes]:
        p = self.root / path
        return p.read_bytes() if p.is_file() else None

    def list(self, prefix: str) -> Iterator[str]:
        base = self.root / prefix
        if base.is_dir():
            for p in base.rglob("*"):
                if p.is_file():
                    yield p.relative_to(self.root).as_posix()


class ZipPack:
    """A zipped resource pack, a mod jar or the client jar."""

    def __init__(self, path: Path, pack_id: Optional[str] = None) -> None:
        self.path = Path(path)
        self.zip = zipfile.ZipFile(self.path)
        self.pack_id = pack_id or f"zip:{self.path.name}"
        self._names = set(self.zip.namelist())

    def read(self, path: str) -> Optional[bytes]:
        return self.zip.read(path) if path in self._names else None

    def list(self, prefix: str) -> Iterator[str]:
        return (n for n in self._names if n.startswith(prefix) and not n.endswith("/"))


class AssetIndexPack:
    """Files from the launcher's asset index, where the Unifont archives are."""

    def __init__(self, index: dict, fetch: Callable[[str, Optional[int]], Path], pack_id: str = "game:assets") -> None:
        self.objects = index.get("objects", {})
        self.fetch = fetch
        self.pack_id = pack_id

    def read(self, path: str) -> Optional[bytes]:
        obj = self.objects.get(path[len("assets/"):]) if path.startswith("assets/") else None
        return None if obj is None else self.fetch(obj["hash"], obj.get("size")).read_bytes()

    def list(self, prefix: str) -> Iterator[str]:
        if prefix.startswith("assets/"):
            sub = prefix[len("assets/"):]
            yield from ("assets/" + k for k in self.objects if k.startswith(sub))


class CompositePack:
    """Several sources as one pack: the game is its asset index plus its client jar."""

    def __init__(self, parts: list, pack_id: str = "game") -> None:
        self.parts = parts
        self.pack_id = pack_id

    def read(self, path: str) -> Optional[bytes]:
        return next((b for part in self.parts if (b := part.read(path)) is not None), None)

    def list(self, prefix: str) -> Iterator[str]:
        seen: set[str] = set()
        for part in self.parts:
            for name in part.list(prefix):
                if name not in seen:
                    seen.add(name)
                    yield name


def open_pack(path: Path, pack_id: Optional[str] = None):
    path = Path(path).expanduser()
    if path.is_dir():
        return DirectoryPack(path, pack_id)
    if path.is_file():
        return ZipPack(path, pack_id)
    raise FileNotFoundError(f"not found: {path}")


class ResourceManager:
    def __init__(self, packs: list) -> None:
        self.packs = list(packs)  # lowest priority first

    def read(self, path: str) -> Optional[bytes]:
        return next((b for pack in reversed(self.packs) if (b := pack.read(path)) is not None), None)

    def read_required(self, path: str) -> bytes:
        b = self.read(path)
        if b is None:
            raise FileNotFoundError(f"missing from the game files and resource packs: {path}")
        return b

    def read_stack(self, path: str) -> list[tuple[str, bytes]]:
        return [(pack.pack_id, b) for pack in self.packs if (b := pack.read(path)) is not None]

    def list(self, prefix: str) -> list[str]:
        return sorted({name for pack in self.packs for name in pack.list(prefix)})

    def font_ids(self) -> list[str]:
        """Every font definition in all packs, as namespace:path."""
        ids = set()
        for path in self.list("assets/"):
            parts = path.split("/", 3)
            if len(parts) == 4 and parts[2] == "font" and parts[3].endswith(".json"):
                ids.add(f"{parts[1]}:{parts[3][:-5]}")
        return sorted(ids)

    def version_info(self) -> dict:
        """The client jar's version.json, or {} if there is none."""
        try:
            return json.loads(self.read("version.json") or b"{}")
        except ValueError:
            return {}


@dataclass
class GameFiles:
    rm: ResourceManager
    version_id: str
    release_time: str
    origin: str  # "mojang" or "local:<name>"


def open_game(
    version: str = "latest",
    game_path: Optional[str] = None,
    resource_packs: Optional[list[str]] = None,
    repo: Optional[MojangRepository] = None,
    log: Callable[[str], None] = lambda m: None,
) -> GameFiles:
    """The game files of *version* from Mojang, or from a local client jar or extracted folder."""
    if game_path:
        game = open_pack(Path(game_path), "game")
        if not any(True for _ in game.list("assets/")):
            raise FileNotFoundError(f"{game_path} has no assets/ folder: use a client.jar or an extracted copy of one")
        info = ResourceManager([game]).version_info()
        version_id = info.get("id") or "unknown"
        release_time = info.get("build_time") or info.get("release_time") or ""
        origin = f"local:{Path(game_path).name}"
    else:
        repo = repo or MojangRepository(log=log)
        entry = repo.resolve(version)
        log(f"Minecraft {entry.id} ({entry.type}, released {entry.release_time[:10]})")
        game = CompositePack([AssetIndexPack(repo.asset_index(entry), repo.asset_object),
                              ZipPack(repo.client_jar(entry), "game:jar")], "game")
        version_id, release_time, origin = entry.id, entry.release_time, "mojang"
    packs = [game] + [open_pack(Path(rp), f"pack{i}:{Path(rp).name}") for i, rp in enumerate(resource_packs or [])]
    return GameFiles(ResourceManager(packs), version_id, release_time, origin)