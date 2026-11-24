"""v1.5–1.6: the image model designs the text (phrases kept whole), photos are kept, jobs survive a
restart, progress counts slots as they finish, sets queue under the per-minute image limit.
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


def test_text_is_designed_by_the_model_not_drawn_on_a_strip(tmp_path):
    client, core, sku = bench(tmp_path)
    job = _wait(core, core.generate(sku, "A", slots=[3]))
    assert job["state"] == "done", job
    st = core.store
    paths = st.attempt_paths(sku, "A", 3, 1)
    cur = st.current(sku, "A", 3)
    assert not paths["gen_final"].exists() and not paths["pasted_final"].exists()
    assert cur["file"] in (paths["gen"], paths["pasted"])          # the model's image is the artwork
    prompt = cur["qa"]["prompt"][0]
    assert 'Headline: "KEY INGREDIENTS"' in prompt
    assert 'Ingredient name: "Hyaluronic Acid"' in prompt
    assert '"Hyaluronic Acid"' in prompt.split("phrases together on one line")[1]
    assert "No added text" not in prompt and "central 72 percent" not in prompt
    card = client.get("/api/slot", params={"sku": sku, "variant": "A", "slot": 3}).json()
    assert 'data-slot="3"' in card["html"] and "重新排版" not in card["html"]
    assert card["tally"] == "通过 0 / 3"
    versions = client.get("/api/slot_versions", params={"sku": sku}).json()
    assert versions["A:3"] and versions["A:1"] == ""


def test_step_labels_and_captions_are_in_each_panel_prompt(tmp_path):
    from imggen.plan import load_templates, prompts_for_slot
    plan = {"style": {}, "slots": {"5": {"name": "使用方法", "steps": [
        {"caption": "Cleanse face", "action": "a"}, {"caption": "Apply a few drops", "action": "b"},
        {"caption": "Massage gently", "action": "c"}]}}}
    prompts = prompts_for_slot(5, plan, {}, load_templates(tmp_path), False)
    assert len(prompts) == 3
    assert 'Step label: "STEP 2"' in prompts[1] and 'Caption: "Apply a few drops"' in prompts[1]


def test_user_template_copy_still_gets_new_text_rules(tmp_path):
    from imggen.plan import load_templates
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "slots.yaml").write_text("no_faces: \"No faces.\"\n", encoding="utf-8")
    tpl = load_templates(tmp_path)["slots"]
    assert tpl["no_faces"] == "No faces." and "{lines}" in tpl["text_block"] and tpl["slots"]


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


def test_rate_gate_keeps_a_share_for_redos():
    from imggen.gen.rate import RateGate
    gate = RateGate(images_per_minute=10, use_ratio=0.8)
    assert sum(gate.try_acquire(False, now=0.0) for _ in range(20)) == 8    # full sets: 80%
    assert sum(gate.try_acquire(True, now=1.0) for _ in range(20)) == 2     # redos: the rest
    assert gate.try_acquire(False, now=61.0)                                 # next minute


def test_sets_beyond_the_limit_wait_in_queue(tmp_path):
    client, core, sku = bench(tmp_path, throughput={"images_per_minute": 150, "max_parallel_sets": 1})
    other = core.store.create_sku("Second", [("source.png", _png())])
    core.confirm_label(other, core.store.label(sku))
    write_json(core.store.vdir(other, "A") / "plan.json", core.store.plan(sku, "A"))
    gate = Event()
    core._generate_slots = lambda job, *a, **k: gate.wait(5)
    first = core.generate_suite(sku, "A")
    second = core.generate_suite(other, "A")
    t = time.time()
    while time.time() - t < 3:
        jobs = {j["id"]: j for j in core.jobs.recent()}
        if jobs[first].get("phase") == "running" and jobs[second].get("phase") == "queued":
            break
        time.sleep(0.02)
    assert jobs[first]["phase"] == "running" and jobs[second]["phase"] == "queued"
    gate.set()
    assert _wait(core, second)["state"] == "done"
