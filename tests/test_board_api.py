"""v1.7: JSON API for the single-page batch workbench, and filing approved images in 成品/.
Demo mode only: no network, no cost."""
import io
import json
import time
from datetime import date

from fastapi.testclient import TestClient
from PIL import Image

from app import create_app
from imggen.config import load_config


def _png():
    fp = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 120, 60)).save(fp, "PNG")
    return fp.getvalue()


def _client(tmp_path):
    cfg = load_config()
    cfg.update(workspace=str(tmp_path / "ws"), demo_mode=True)
    app = create_app(cfg)
    return TestClient(app), app.state.core


def _settle(core, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if not any(j["state"] == "running" for j in core.jobs.recent(200)):
            return
        time.sleep(0.1)
    raise AssertionError("jobs did not finish")


LABEL = {"lines": [{"text": "Demo Scrub", "size": "big"}, {"text": "Coffee & Sea Salt", "size": "small"}],
         "product_name": "Demo Scrub", "category": "body", "capacity": "8 OZ / 226G",
         "ingredient_names": ["Coffee", "Sea Salt"], "allowed_claims": ["Gentle Exfoliation"],
         "texture_caption": "Fine coffee grains", "usage_steps": []}


def test_paste_intake_confirm_and_start_then_proof_and_archive(tmp_path):
    c, core = _client(tmp_path)
    r = c.post("/api/intake", files=[("files", ("image.png", _png(), "image/png"))]).json()
    sku = r["sku"]
    assert sku.startswith("新商品") and r["job_id"]                     # pasted image: generic name
    _settle(core)
    board = c.get("/api/board").json()
    row = next(p for p in board["products"] if p["id"] == sku)
    assert row["stage"] == "confirm" and board["counts"]["confirm"] == 1
    assert board["capacity"]["images_per_minute"] == 150 and board["capacity"]["max_parallel_sets"] == 10

    r = c.post(f"/api/sku/{sku}/confirm", json=dict(LABEL, start=True)).json()
    assert r["ok"] and r["job_id"]
    _settle(core)
    detail = c.get(f"/api/sku/{sku}").json()
    assert detail["row"]["stage"] == "proof"
    slots = {s["slot"]: s for s in detail["variants"]["A"]["slots"]}
    assert slots[3]["copy"][0] == {"role": "headline", "text": "KEY INGREDIENTS"}
    assert slots[2]["current"]["file"].startswith("/files/") and slots[2]["versions"][0]["n"] == 1

    # edit one slot's copy: only confirmed words pass (C4)
    bad = c.post(f"/api/sku/{sku}/slot-copy", json={"variant": "A", "slot": 2, "bullets": ["Made Up Claim"]})
    assert bad.status_code == 400 and "不在已确认" in bad.json()["error"]
    ok = c.post(f"/api/sku/{sku}/slot-copy", json={"variant": "A", "slot": 2, "bullets": []}).json()
    assert ok["ok"]

    # approve files the image in 成品/<today>/<sku>/ with a manifest; a second version is added, not replaced
    assert c.post("/api/review", json={"sku": sku, "variant": "A", "slot": 2, "action": "approve"}).json()["ok"]
    folder = tmp_path / "ws" / "成品" / date.today().isoformat() / sku
    assert (folder / f"{sku}_A_02_v1.jpg").exists()
    job = c.post("/api/review", json={"sku": sku, "variant": "A", "slot": 2, "action": "redo"}).json()["job_id"]
    _settle(core)
    c.post("/api/review", json={"sku": sku, "variant": "A", "slot": 2, "action": "approve"})
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["variants"]["A"]["2"]
    assert entry["current"] == f"{sku}_A_02_v2.jpg" and len(entry["versions"]) == 2
    assert (folder / f"{sku}_A_02_v1.jpg").exists() and job


def test_start_conflicts_and_unknown_product(tmp_path):
    c, core = _client(tmp_path)
    assert c.get("/api/sku/nope").status_code == 404
    sku = c.post("/api/intake", data={"name": "Second Product", "read_label": "0"},
                 files=[("files", ("a.png", _png(), "image/png"))]).json()["sku"]
    assert sku == "Second-Product"
    row = next(p for p in c.get("/api/board").json()["products"] if p["id"] == sku)
    assert row["stage"] == "new"
    assert c.post(f"/api/sku/{sku}/start", json={}).status_code == 409          # label not confirmed
