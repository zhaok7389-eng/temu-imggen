"""End-to-end in demo mode (fake OpenAI): import -> transcribe -> confirm -> plan -> generate
-> review -> export. No network, no cost."""
import io
import time
import zipfile

import cv2
import numpy as np
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import create_app
from imggen.config import load_config
from imggen.plan import check_text


def _product_png():
    img = np.full((900, 900, 3), 255, np.uint8)
    cv2.rectangle(img, (300, 150), (600, 780), (60, 120, 230), -1)
    rng = np.random.default_rng(1)
    for _ in range(300):
        x, y = rng.integers(310, 590), rng.integers(160, 770)
        cv2.circle(img, (int(x), int(y)), int(rng.integers(2, 6)), tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    cv2.putText(img, "Turmeric", (320, 260), cv2.FONT_HERSHEY_DUPLEX, 1.4, (255, 255, 255), 3)
    cv2.putText(img, "30ML / 1.0 FL.OZ", (320, 720), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return cv2.imencode(".png", img)[1].tobytes()


def _wait(c, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        js = c.get("/api/jobs").json()
        if js and all(j["state"] != "running" for j in js):
            return js
        time.sleep(0.3)
    raise AssertionError("job did not finish")


def test_full_flow_demo(tmp_path):
    cfg = load_config()
    cfg.update(demo_mode=True, workspace=str(tmp_path / "ws"), concurrency=4)
    app = create_app(cfg)
    c = TestClient(app)
    assert c.get("/").status_code == 200

    r = c.post("/sku", data={"name": "01 Turmeric Serum"},
               files=[("files", ("main.png", _product_png(), "image/png"))], follow_redirects=False)
    assert r.status_code == 303
    sku = r.headers["location"].split("/sku/")[1].split("?")[0]

    c.post(f"/sku/{sku}/transcribe")
    js = _wait(c)
    assert js[0]["state"] == "done", js
    assert "确认标签文字" in c.get(f"/sku/{sku}").text

    form = {"n": "3", "t0": "Turmeric", "s0": "big", "big0": "on", "t1": "30ML / 1.0 FL.OZ", "s1": "small",
            "t2": "", "product_name": "Turmeric Serum", "capacity": "30ML / 1.0 FL.OZ", "pieces": "1",
            "category": "serum", "key_ingredient": "", "texture": "orange serum drops",
            "claim": ["Moisturizing", "Smooth Skin"], "extra_claims": "Even Skin Tone",
            "usage_steps": "Cleanse face\nApply a few drops\nMassage gently"}
    c.post(f"/sku/{sku}/label", data=form)
    lab = app.state.core.store.label(sku)
    assert lab["big_text"] == ["Turmeric"] and "Even Skin Tone" in lab["allowed_claims"]

    c.post(f"/sku/{sku}/plan/A")
    _wait(c)
    plan = app.state.core.store.plan(sku, "A")
    assert plan["issues"] == [], plan["issues"]
    assert plan["slots"]["2"]["bullets"] and plan["slots"]["7"]["capacity"] == "30ML / 1.0 FL.OZ"

    c.post(f"/sku/{sku}/generate/A")
    js = _wait(c)
    assert js[0]["state"] == "done", js
    st = app.state.core.store
    for slot in range(1, 9):
        assert st.current(sku, "A", slot) is not None, slot
    assert st.attempts(sku, "A", 5) == [1]          # how-to composed from 3 step images
    assert (st.vdir(sku, "A") / "attempts" / "5_1_step3.png").exists()

    # resume: generating again does not call anything for slots that have images
    c.post(f"/sku/{sku}/generate/A")
    _wait(c)
    assert st.attempts(sku, "A", 1) == [1]

    # review: redo one slot, approve the set
    assert c.post("/api/review", json={"sku": sku, "variant": "A", "slot": 3, "action": "redo", "note": "less props"}).json()["ok"]
    _wait(c)
    assert st.attempts(sku, "A", 3) == [1, 2]
    assert "Fix this: less props" in st.current(sku, "A", 3)["qa"]["prompt"][0]
    c.post("/api/review_all", json={"sku": sku, "variant": "A"})
    assert "审核墙" in c.get("/review?show=all").text

    r = c.post("/export", data={"item": f"{sku}|A"}, follow_redirects=False)
    assert r.status_code == 303
    zips = list((tmp_path / "ws" / "exports").glob("*.zip"))
    assert len(zips) == 1
    names = zipfile.ZipFile(zips[0]).namelist()
    assert f"{sku}_A_01.jpg" in names and f"{sku}_A_08.jpg" in names and "erp_import.xlsx" in names
    wb = load_workbook(io.BytesIO(zipfile.ZipFile(zips[0]).read("erp_import.xlsx")))
    row = [x.value for x in wb.active[2]]
    assert row[0] == sku and row[2] == f"{sku}_A_01.jpg" and row[10] == "完整"
    for page in ("/", "/review", "/export", "/settings", "/ledger", f"/sku/{sku}"):
        assert c.get(page).status_code == 200, page


def test_copy_rules():
    lab = {"allowed_claims": ["Smooth Skin"], "capacity": "1.7 FL OZ / 50ML", "product_name": "Retinol Cream"}
    banned = ["cure", "clinically proven"]
    assert check_text("Smooth Skin", lab, banned, "bullet") == []
    assert check_text("1.7 FL OZ / 50ML", lab, banned, "") == []
    assert check_text("Smoother Skin", lab, banned, "bullet")            # not confirmed
    lab2 = dict(lab, allowed_claims=["Reduces 90% Wrinkles", "Cures Acne", "Clinically Proven"])
    assert any("数字" in x for x in check_text("Reduces 90% Wrinkles", lab2, banned, "bullet"))
    assert any("禁用词" in x for x in check_text("Clinically Proven", lab2, banned, "bullet"))
