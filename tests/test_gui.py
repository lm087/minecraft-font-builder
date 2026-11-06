import io
import json
import time
import urllib.error
import urllib.request

import pytest
from fontTools.ttLib import TTFont

from mcfont import config as config_mod
from mcfont.gui.server import start_in_thread


@pytest.fixture
def gui(game_dir, tmp_path):
    cfg = config_mod.from_dict({"game_path": str(game_dir), "output": str(tmp_path / "out"),
                                "build_date": "2026-01-02", "fonts": ["all"]})
    httpd, app, port = start_in_thread(tmp_path, cfg)
    yield app, port
    httpd.shutdown()
    httpd.server_close()


def call(port, path, body=None, token=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body or {}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Token", token)
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_requires_token_and_local_host(gui):
    app, port = gui
    with pytest.raises(urllib.error.HTTPError) as e:
        call(port, "/api/start")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        call(port, "/api/start", token=app.token, host="example.com:80")
    assert e.value.code == 403
    start = call(port, "/api/start", token=app.token)
    assert start["limit"] == 65535 and "examples" not in start
    assert start["defaults"]["fonts"] == ["minecraft:alt", "minecraft:default", "minecraft:illageralt", "minecraft:uniform"]
    assert start["defaults"]["characters"]["exclude_planes"] == [1]


def test_static_files(gui):
    app, port = gui
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
        html = r.read().decode()
        assert "app.js" in html and "default-src 'self'" in r.headers["Content-Security-Policy"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/i18n.js") as r:
        js = r.read().decode()
        assert '"zh-TW"' in js and "en:" in js
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/theme.js") as r:
        assert "data-theme" in r.read().decode()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/mark.svg") as r:
        assert r.headers["Content-Type"].startswith("image/svg+xml")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/static/../server.py")


def test_fonts_count_build_and_preview(gui):
    app, port = gui
    tok = app.token
    settings = call(port, "/api/start", token=tok)["settings"]
    fonts = {f["id"]: f for f in call(port, "/api/fonts", {"settings": settings}, tok)["fonts"]}
    assert fonts["minecraft:default"]["family"] == "Mojangles" and fonts["minecraft:include/space"]["include"]

    counts = call(port, "/api/count", {"settings": settings}, tok)
    default = next(f for f in counts["fonts"] if f["font_id"] == "minecraft:default")
    assert set(default["styles"]) == {"regular", "bold", "italic", "bold_italic"}
    assert any(b["block"] == "Emoticons" and b["selected"] == 1 for b in default["blocks"])
    assert {p["plane"] for p in default["planes"]} == {0, 1}

    assert "Emoticons" in settings["characters"]["keep_blocks"]  # kept from plane 1 by default
    settings["characters"]["keep_blocks"] = []
    d2 = next(f for f in call(port, "/api/count", {"settings": settings}, tok)["fonts"] if f["font_id"] == "minecraft:default")
    assert d2["selected"] == default["selected"] - 1
    assert next(p for p in d2["planes"] if p["plane"] == 1)["selected"] == 0

    settings["styles"] = ["regular"]
    assert call(port, "/api/build", {"settings": settings}, tok)["started"]
    for _ in range(300):
        job = call(port, "/api/build/status", {}, tok)
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done", job
    assert "Mojangles-Regular.ttf" in {f["file"] for f in job["files"]}
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/files/Mojangles-Regular.ttf?t={tok}") as r:
        assert r.headers["Content-Type"] == "font/ttf" and len(r.read()) > 1000
    # the preview's companion font draws the given characters as the font's box
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/files/Mojangles-Regular.ttf?t={tok}&missing=41.4e00.1f600") as r:
        box = TTFont(io.BytesIO(r.read()))
        assert set(box.getBestCmap()) == {0x41, 0x4E00, 0x1F600} and set(box.getBestCmap().values()) == {"box"}
    for bad in ("Mojangles-Regular.ttf?t=wrong", f"build-config.yaml?t={tok}", f"Mojangles-Regular.ttf?t={tok}&missing=zz"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/files/{bad}")


def test_settings_round_trip(gui):
    app, port = gui
    tok = app.token
    settings = call(port, "/api/start", token=tok)["settings"]
    text = call(port, "/api/settings/write", {"settings": settings}, tok)["text"]
    assert call(port, "/api/settings/read", {"text": text}, tok)["settings"] == settings
    with pytest.raises(urllib.error.HTTPError) as e:
        call(port, "/api/settings/read", {"text": "styles: [heavy]"}, tok)
    assert e.value.code == 400