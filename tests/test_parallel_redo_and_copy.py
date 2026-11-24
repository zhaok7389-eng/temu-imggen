import io
import json
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from app import create_app
from imggen.config import load_config
from imggen.plan import panel_copy
from imggen.store import write_json


def workbench(tmp_path):
    cfg = load_config()
    cfg.update(workspace=str(tmp_path), demo_mode=True, concurrency=2)
    app = create_app(cfg)
    core = app.state.core
    fp = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(fp, "PNG")
    sku = core.store.create_sku("Demo Scrub", [("source.png", fp.getvalue())])
    core.confirm_label(sku, {"product_name": "Demo Scrub", "category": "body",
                             "lines": [{"text": "Coffee", "size": "small"}], "allowed_claims": []})
    write_json(core.store.vdir(sku, "A") / "plan.json", {"variant": "A", "style": {"light": "soft light"},
        "slots": {"2": {"name": "卖点图"}, "3": {"name": "成分图"}, "4": {"name": "质地特写"}},
        "issues": []})
    return TestClient(app), core, sku


def test_review_submits_two_slots_in_parallel_without_duplicate(tmp_path, monkeypatch):
    client, core, sku = workbench(tmp_path)
    release = Event()
    started = {slot: Event() for slot in (2, 3)}
    finished = {slot: Event() for slot in (2, 3)}

    def one_slot(gen, sku_id, variant, slot, note):
        started[slot].set()
        if not release.wait(8):
            raise TimeoutError("test did not release workers")
        core.store.set_review(sku_id, variant, slot, status="pending", note=note)
        finished[slot].set()

    monkeypatch.setattr(core, "_one_slot", one_slot)
    def redo(slot):
        return client.post("/api/review", json={
            "sku": sku, "variant": "A", "slot": slot, "action": "redo", "note": f"revise {slot}",
        }).json()

    try:
        one, two = redo(2), redo(3)
        assert one["job_id"] != two["job_id"]
        assert started[2].wait(2) and started[3].wait(2)
        assert "重复提交" in redo(2)["error"]
        running = [j for j in client.get("/api/jobs", params={"sku": sku}).json() if j["state"] == "running"]
        assert {j["slot"] for j in running} == {2, 3}
        blocked = client.post("/api/review", json={
            "sku": sku, "variant": "A", "slot": 2, "action": "approve",
        }).json()
        assert "正在重出" in blocked["error"]
    finally:
        release.set()
    assert finished[2].wait(2) and finished[3].wait(2)
    assert {r["note"] for r in core.store.review(sku, "A").values()} == {"revise 2", "revise 3"}


def test_gpt_copy_analysis_and_approval_update_only_selected_slots(tmp_path, monkeypatch):
    client, core, sku = workbench(tmp_path)
    event = Event()
    source = "温和去角质。棕色颗粒质地。"
    draft_path = core.store.sku_dir(sku) / "product_copy.json"
    original_confirmed = core.store.label(sku)["confirmed_at"]

    def analyze(sid, sources):
        assert sid == sku and sources == [source, "Coffee"]
        event.set()
        return {"items": [
            {"kind": "benefit", "text": "Gentle Exfoliation", "source_quote": "温和去角质"},
            {"kind": "ingredient", "text": "Coffee", "source_quote": "Coffee"},
            {"kind": "texture", "text": "Brown scrub granules", "source_quote": "棕色颗粒质地"},
            {"kind": "benefit", "text": "For Sensitive Skin", "source_quote": "适合敏感肌"},
        ]}

    monkeypatch.setattr(core, "llm", lambda: SimpleNamespace(analyze_copy=analyze))
    response = client.post(f"/sku/{sku}/product-copy", data={"source": source}, follow_redirects=False)
    jid = parse_qs(urlsplit(response.headers["location"]).query)["job"][0]
    assert event.wait(2)
    # Wait for the actual background job to finish, rather than approving the fixture directly.
    for _ in range(50):
        jobs = client.get("/api/jobs", params={"sku": sku}).json()
        if next(j for j in jobs if j["id"] == jid)["state"] == "done":
            break
        Event().wait(.02)
    draft = json.loads(draft_path.read_text())
    assert [row["slot"] for row in draft["items"]] == [2, 3, 4]
    page = client.get(f"/sku/{sku}").text
    assert "Gentle Exfoliation" in page and "温和去角质" in page and "For Sensitive Skin" not in page
    assert core.store.label(sku)["allowed_claims"] == []
    response = client.post(f"/sku/{sku}/product-copy/apply", data={"draft_id": jid, "item": ["0", "2"]})
    assert "已采用勾选的文案" in response.text
    label = core.store.label(sku)
    assert label["confirmed_at"] == original_confirmed and label["lines"][0]["text"] == "Coffee"
    plan = core.store.plan(sku, "A")
    assert plan["style"] == {"light": "soft light"} and plan["slots"]["3"] == {"name": "成分图"}
    assert panel_copy(plan["slots"]["2"]) == ("Demo Scrub", ["Gentle Exfoliation"])
    assert panel_copy(plan["slots"]["4"]) == ("Brown scrub granules", [])
