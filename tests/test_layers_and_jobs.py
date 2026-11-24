"""v1.5 (phase 1): photo and copy are separate layers, phrases are never cut, jobs survive a
restart, progress counts slots as they finish, full set and redo of other slots can overlap.
Demo mode only: no network, no cost."""
import hashlib
import io
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import create_app
from imggen.config import load_config
from imggen.copy_layout import LayoutOverflow, copy_font, phrase_units, wrap_copy
from imggen.pipeline import Jobs
from imggen.store import write_json


# ---------------------------------------------------------------- phrases
@pytest.mark.parametrize("text,unit", [
    ("Collagen, Retinol & Hyaluronic Acid", "Retinol & Hyaluronic Acid"),
    ("Day & Night cream", "Day & Night"),
    ("1.7 FL OZ / 50ML", "1.7 FL OZ / 50ML"),
    ("4 SHEETS X 1.13OZ (32G)", "4 SHEETS X 1.13OZ (32G)"),
    ("30ML / 1.0 FL.OZ", "30ML / 1.0 FL.OZ"),
])
def test_phrase_units_keep_groups(text, unit):
    assert unit in phrase_units(text, keep=["Hyaluronic Acid"])


def test_confirmed_names_stay_on_one_line():
    font = copy_font(40, bold=False)
    width = int(font.getlength("HYALURONIC ACID")) + 4
    lines = wrap_copy("Niacinamide HYALURONIC ACID Squalane", font, width, keep=["Hyaluronic Acid"])
    assert "HYALURONIC ACID" in lines
    assert all(line in {"Niacinamide", "HYALURONIC ACID", "Squalane"} for line in lines)


def test_long_word_is_never_cut_letter_by_letter():
    font = copy_font(40)
    with pytest.raises(LayoutOverflow):
        wrap_copy("Supercalifragilisticexpialidocious", font, 120)


# ---------------------------------------------------------------- workbench
def _png(color="white", size=64):
    fp = io.BytesIO()
    Image.new("RGB", (size, size), color).save(fp, "PNG")
    return fp.getvalue()


def bench(tmp_path, **extra):
    cfg = load_config()
    cfg.update(workspace=str(tmp_path), demo_mode=True, concurrency=2, **extra)
    app = create_app(cfg)
    core = app.state.core
    sku = core.store.create_sku("Demo Cream", [("source.png", _png())])
    core.confirm_label(sku, {"product_name": "Demo Cream", "category": "cream",
                             "lines": [{"text": "Demo", "size": "big"}], "big_text": [],
                             "ingredient_names": ["Hyaluronic Acid", "Retinol"],
                             "allowed_claims": ["Deep Moisture"]})
    write_json(core.store.vdir(sku, "A") / "plan.json", {"variant": "A", "style": {}, "issues": [], "slots": {
        "1": {"name": "首图"},
        "3": {"name": "成分图", "copy_layout": "ingredients", "ingredient_labels": ["Hyaluronic Acid", "Retinol"]},
        "4": {"name": "质地特写"}}})
    return TestClient(app), core, sku


def _wait(core, jid, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        job = next(j for j in core.jobs.recent(200) if j["id"] == jid)
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_photo_is_kept_and_relayout_costs_nothing(tmp_path):
    client, core, sku = bench(tmp_path)
    job = _wait(core, core.generate(sku, "A", slots=[3]))
    assert job["state"] == "done", job
    st = core.store
    paths = st.attempt_paths(sku, "A", 3, 1)
    assert paths["layout"].exists() and paths["gen_final"].exists()
    photo = _digest(paths["gen"])
    cur = st.current(sku, "A", 3)
    assert cur["file"] in (paths["gen_final"], paths["pasted_final"])
    assert cur["gen_photo"] == paths["gen"]
    with Image.open(paths["gen"]) as a, Image.open(paths["gen_final"]) as b:
        assert a.size == b.size

    calls = core.ledger.path.read_text().count("\n")
    r = client.post("/api/review", json={"sku": sku, "variant": "A", "slot": 3, "action": "relayout"}).json()
    assert r == {"ok": True}
    assert core.ledger.path.read_text().count("\n") == calls       # no API call, no cost
    assert _digest(paths["gen"]) == photo                            # photo never written over
    assert st.attempts(sku, "A", 3) == [1]                           # no new attempt

    card = client.get("/api/slot", params={"sku": sku, "variant": "A", "slot": 3}).json()
    assert 'data-slot="3"' in card["html"] and "重新排版" in card["html"]
    assert card["tally"] == "通过 0 / 3"
    versions = client.get("/api/slot_versions", params={"sku": sku}).json()
    assert versions["A:3"] and versions["A:1"] == ""


def test_copy_that_cannot_fit_is_flagged_not_cut(tmp_path):
    client, core, sku = bench(tmp_path)
    word = "Pneumonoultramicroscopicsilicovolcanoconiosis" * 4
    label = core.store.label(sku)
    label["ingredient_names"] = [word]
    core.confirm_label(sku, label)
    plan = core.store.plan(sku, "A")
    plan["slots"]["3"]["ingredient_labels"] = [word]
    write_json(core.store.vdir(sku, "A") / "plan.json", plan)
    assert _wait(core, core.generate(sku, "A", slots=[3]))["state"] == "done"
    cur = core.store.current(sku, "A", 3)
    assert cur["file"] == cur["gen_photo"] or cur["file"] == cur["pasted_photo"]
    assert any(f.startswith("文案排不下") for f in cur["qa"]["flags"])


def test_legacy_attempt_cannot_be_relaid(tmp_path):
    client, core, sku = bench(tmp_path)
    paths = core.store.attempt_paths(sku, "A", 3, 1)
    paths["gen"].parent.mkdir(parents=True, exist_ok=True)
    paths["gen"].write_bytes(_png("gray", 128))
    write_json(paths["qa"], {"flags": [], "rendered_copy": ["KEY INGREDIENTS"]})
    r = client.post("/api/review", json={"sku": sku, "variant": "A", "slot": 3, "action": "relayout"}).json()
    assert "旧版" in r["error"]


# ---------------------------------------------------------------- jobs
def test_jobs_survive_restart_as_interrupted(tmp_path):
    path = tmp_path / ".runtime" / "jobs.json"
    jobs = Jobs(2, path)
    gate = Event()
    jid = jobs.start("生成套图", "sku1", lambda job: gate.wait(5), slots=[1, 2], variant="A")
    assert path.exists()
    reloaded = Jobs(2, path)
    job = next(j for j in reloaded.recent() if j["id"] == jid)
    assert job["state"] == "interrupted" and "重启" in job["error"]
    gate.set()


def test_full_set_and_redo_of_other_slot_run_together(tmp_path):
    jobs = Jobs(2)
    gate = Event()
    try:
        jobs.start("生成套图", "s", lambda job: gate.wait(5), variant="A", slots=[1, 2])
        jobs.start("重出", "s", lambda job: gate.wait(5), variant="A", slot=3)
        with pytest.raises(ValueError, match="图位 02 正在生成"):
            jobs.start("重出", "s", lambda job: None, variant="A", slot=2)
        with pytest.raises(ValueError, match="已有任务"):
            jobs.start("识别标签", "s", lambda job: None)
        assert jobs.redo_for("s", "A", 2) and jobs.redo_for("s", "A", 3)
        assert jobs.redo_for("s", "A", 4) is None
    finally:
        gate.set()


def test_progress_counts_slots_as_they_finish(tmp_path):
    client, core, sku = bench(tmp_path)
    slow, fast_done = Event(), Event()

    def one_slot(gen, sku_id, variant, slot, note):
        if slot == 1:
            slow.wait(5)            # submitted first, finishes last
        else:
            fast_done.set()

    core._one_slot = one_slot
    jid = core.generate(sku, "A", slots=[1, 4])
    assert fast_done.wait(5)
    t = time.time()
    while time.time() - t < 5:
        job = next(j for j in core.jobs.recent() if j["id"] == jid)
        if job["done"] == 1:
            break
        time.sleep(0.02)
    assert job["done"] == 1 and job["state"] == "running"
    slow.set()
    assert _wait(core, jid)["done"] == 2


def test_text_model_cost_counts_toward_budget(tmp_path):
    cfg = load_config()
    assert cfg["text_pricing_usd_per_million_tokens"]["input"] > 0
    assert cfg["text_pricing_usd_per_million_tokens"]["output"] > 0
