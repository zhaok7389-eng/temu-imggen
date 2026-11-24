"""The source archive serves the entire reviewed UI on one origin, without a workspace."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as web
from app import create_app, LOCAL_CLIPS
from imggen.config import load_config


def test_landing_and_packaged_assets(tmp_path):
    cfg = load_config()
    cfg.update(demo_mode=True, workspace=str(tmp_path / "ws"))
    client = TestClient(create_app(cfg))
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307 and home.headers["location"] == "/library"
    studio = client.get("/studio")
    assert studio.status_code == 200 and "珂学家电商套图工作台" in studio.text
    assert studio.headers["cache-control"] == "no-store"
    for asset in ("studio.css", "header.css", "label.css", "proof.css", "studio.js", "data.js", "label.js",
                  "proof.js", "assets/proof-source.webp"):
        assert client.get(f"/studio/{asset}").status_code == 200
    landing = client.get("/showcase")
    assert landing.status_code == 200
    assert 'href="/library#drop-area"' in landing.text
    assert 'href="/plan"' in landing.text
    assert 'data-local-clips=""' in landing.text
    assert not any(f":{port}" in landing.text for port in (8766, 8767))
    for route in ("library", "plan", "review", "export", "settings", "ledger"):
        response = client.get("/"+route)
        assert response.status_code == 200 and "sidebar" in response.text
        assert 'aria-current="page"' in response.text
        assert 'href="/static/studio.css?v=' in response.text
        assert 'href="/studio">新工作台</a>' in response.text
        assert response.headers['cache-control'] == 'no-store'
    for f in ("style.css", "hd.css", "motion.css", "ai-water.css", "ai-orbit-glow.css", "ai-orbit.js", "studio.css",
              "assets/mark.png", "assets/hd/hero.webp", "assets/hd/examples-field.webp",
              "assets/hd/scenes.webp", "assets/hd/details.webp"):
        assert client.get("/static/"+f).status_code == 200
    for k in ("window", "water", "bottle", "metal", "box", "sand", "pool", "silk", "macro", "package-landscape"):
        assert client.get(f"/static/assets/motion/{k}.webp").status_code == 200
        response = client.get(f"/static/assets/motion/orbit/{k}.mp4", headers={"Range":"bytes=0-63"})
        assert response.status_code == 206 and response.content[4:8] in (b"ftyp", b"moov", b"free")
    assert client.get("/showcase-media/window-tight.mp4").status_code == 404
    assert client.get("/showcase-media/ledger.csv").status_code == 404
    local = tmp_path/"ws"/"landing-media"
    local.mkdir()
    (local/(LOCAL_CLIPS[0]+"-tight.mp4")).write_bytes(b"sample")
    assert 'data-local-clips="window"' in client.get("/showcase").text
    assert client.get("/showcase-media/window-tight.mp4").content == b"sample"


def test_browser_opens_workbench_only_after_server_is_ready(monkeypatch):
    server = SimpleNamespace(started=False)
    opened = []

    def become_ready(_seconds):
        assert not opened
        server.started = True

    monkeypatch.setattr(web.time, "sleep", become_ready)
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    workbench = "http://127.0.0.1:8765/studio"
    web.open_workbench_when_ready(server, workbench)
    assert opened == [workbench]
