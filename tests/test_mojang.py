import hashlib
import json

import pytest

from mcfont.mojang import IntegrityError, MojangRepository
from mcfont import mojang


def setup_server(tmp_path, monkeypatch, corrupt=False):
    srv = tmp_path / "srv"
    srv.mkdir()
    jar = srv / "client.jar"
    jar.write_bytes(b"jar-bytes")
    index = srv / "index.json"
    index.write_text(json.dumps({"objects": {"minecraft/font/x.zip": {"hash": hashlib.sha1(b"obj").hexdigest(), "size": 3}}}))
    h = hashlib.sha1(b"obj").hexdigest()
    (srv / h[:2]).mkdir()
    (srv / h[:2] / h).write_bytes(b"obj")
    sha = lambda p: hashlib.sha1(p.read_bytes()).hexdigest()  # noqa: E731
    vj = srv / "v.json"
    vj.write_text(json.dumps({
        "downloads": {"client": {"url": jar.as_uri(), "sha1": "0" * 40 if corrupt else sha(jar), "size": jar.stat().st_size}},
        "assetIndex": {"id": "1", "url": index.as_uri(), "sha1": sha(index), "size": index.stat().st_size},
    }))
    manifest = srv / "manifest.json"
    manifest.write_text(json.dumps({"latest": {"release": "1.0", "snapshot": "1.1-pre"}, "versions": [
        {"id": "1.1-pre", "type": "snapshot", "url": vj.as_uri(), "sha1": sha(vj), "releaseTime": "2026-02-01T00:00:00+00:00"},
        {"id": "1.0", "type": "release", "url": vj.as_uri(), "sha1": sha(vj), "releaseTime": "2026-01-01T00:00:00+00:00"},
    ]}))
    monkeypatch.setattr(mojang, "MANIFEST_URL", manifest.as_uri())
    monkeypatch.setattr(mojang, "RESOURCES_URL", srv.as_uri())
    return srv


def test_download_cache_and_offline(tmp_path, monkeypatch):
    setup_server(tmp_path, monkeypatch)
    repo = MojangRepository(cache_dir=tmp_path / "cache", log=lambda m: None, retries=0)
    e = repo.resolve("latest")
    assert e.id == "1.0"
    assert repo.resolve("latest-snapshot").id == "1.1-pre"
    assert [v.id for v in repo.versions(include_snapshots=True)] == ["1.1-pre", "1.0"]
    jar = repo.client_jar(e)
    assert jar.read_bytes() == b"jar-bytes"
    idx = repo.asset_index(e)
    obj = repo.asset_object(idx["objects"]["minecraft/font/x.zip"]["hash"], 3)
    assert obj.read_bytes() == b"obj"
    # offline, the checked cache is used again
    off = MojangRepository(cache_dir=tmp_path / "cache", offline=True, log=lambda m: None)
    assert off.client_jar(off.resolve("1.0")).read_bytes() == b"jar-bytes"


def test_sha1_mismatch_rejected(tmp_path, monkeypatch):
    setup_server(tmp_path, monkeypatch, corrupt=True)
    repo = MojangRepository(cache_dir=tmp_path / "cache", log=lambda m: None, retries=0)
    with pytest.raises(IntegrityError):
        repo.client_jar(repo.resolve("1.0"))
    assert not (tmp_path / "cache" / "versions" / "1.0" / "1.0.jar").exists()


def test_corrupt_cache_is_redownloaded(tmp_path, monkeypatch):
    setup_server(tmp_path, monkeypatch)
    repo = MojangRepository(cache_dir=tmp_path / "cache", log=lambda m: None, retries=0)
    jar = repo.client_jar(repo.resolve("1.0"))
    jar.write_bytes(b"tampered")
    jar2 = repo.client_jar(repo.resolve("1.0"))
    assert jar2.read_bytes() == b"jar-bytes"