"""The production flow: transcribe (F2) -> confirm -> plan (F3) -> generate (F4) -> checks (F5).

Long steps run as background jobs in this process; every result is written to disk first,
so a crash or restart only loses the call in flight (C7). Calls never repeat for slots that
already have an image unless the user asks for a redo.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .config import load_config
from .copy_layout import compose_copy_panel, copy_block, copy_font
from .credentials import OPENAI_BASE_URL, safe_error
from .gen.generate import Blocked, BudgetExceeded, GenRequest, Generator
from .gen.ledger import Ledger
from .llm import LLM
from .plan import build_plan, load_templates, panel_copy, prompts_for_slot, validate
from .product_copy import apply_suggestions, checked_suggestions, sources_for, transfer_copy
from .qa import label_text
from .store import Store, now, plan_slots, read_json, write_json


def make_client(cfg: dict):
    if cfg.get("demo_mode"):
        from .fake import FakeOpenAI
        return FakeOpenAI()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("没有设置 OPENAI_API_KEY。请在终端 export 后重启，或在设置里打开演示模式")
    from openai import OpenAI
    return OpenAI(base_url=OPENAI_BASE_URL)


def load_img(p: Path) -> np.ndarray:
    im = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise ValueError(f"读不了图片 {p}")
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    if im.shape[2] == 4:
        a = im[..., 3:4] / 255.0
        im = (im[..., :3] * a + 255 * (1 - a)).astype(np.uint8)
    return im


# ---------------------------------------------------------------- jobs
class Jobs:
    def __init__(self, workers: int = 6):
        self.pool = ThreadPoolExecutor(max_workers=max(2, workers) + 2)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, sku_id: str, fn, total: int = 1, variant: str = "",
              slot: int | None = None) -> str:
        jid = uuid.uuid4().hex[:8]
        job = dict(id=jid, kind=kind, sku=sku_id, variant=variant, slot=slot, total=total, done=0,
                   state="running", error="", started=time.time())
        with self.lock:
            for other in self.jobs.values():
                if other["sku"] != sku_id or other["state"] != "running":
                    continue
                other_slot = other.get("slot")
                if slot is None or other_slot is None:
                    raise ValueError("这个商品已有任务正在运行，请等当前任务完成。")
                if other["variant"] == variant and other_slot == slot:
                    raise ValueError("这个图位正在重出，无需重复提交。")
            self.jobs[jid] = job

        def run():
            try:
                fn(job)
                if job["state"] == "running":
                    job["state"] = "done"
            except Exception as e:  # noqa: BLE001
                job["error"] = safe_error(e)
                job["state"] = "error"
        self.pool.submit(run)
        return jid

    def busy(self, sku_id: str) -> bool:
        with self.lock:
            return any(j["sku"] == sku_id and j["state"] == "running" for j in self.jobs.values())

    def recent(self, n: int = 30) -> list[dict]:
        with self.lock:
            ordered = sorted((j.copy() for j in self.jobs.values()), key=lambda j: -j["started"])
            return [j for i, j in enumerate(ordered) if i < n or j["state"] == "running"]

    def redo_for(self, sku_id: str, variant: str, slot: int) -> dict | None:
        with self.lock:
            return next((j.copy() for j in self.jobs.values() if j["sku"] == sku_id and
                         j.get("variant") == variant and j.get("slot") == slot and
                         j["state"] == "running"), None)


# ---------------------------------------------------------------- app services
class App:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.store = Store(self.cfg["workspace"] + "/")
        self.ledger = Ledger(Path(self.cfg["workspace"]) / "ledger.csv")
        self.jobs = Jobs(int(self.cfg.get("concurrency", 6)))
        self._client = None
        self.gen_pool = ThreadPoolExecutor(max_workers=int(self.cfg.get("concurrency", 6)))

    def reload(self, cfg: dict):
        self.cfg = cfg
        self.reset_client()
        self.gen_pool = ThreadPoolExecutor(max_workers=int(cfg.get("concurrency", 6)))

    def reset_client(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.cfg)
        return self._client

    @property
    def tpl(self):
        return load_templates(self.cfg["workspace"])

    def llm(self):
        return LLM(self.cfg, self.ledger, self.client)

    # ---- F2: label transcription ----------------------------------------
    def transcribe(self, sku_id: str) -> str:
        def run(job):
            srcs = self.store.sources(sku_id)[:4]
            out = self.llm().transcribe(sku_id, srcs)
            lines = [ln for ln in out.get("lines", []) if str(ln.get("text", "")).strip()]
            # second opinion from local OCR: lines it cannot find are highlighted for the user
            ocr = []
            if label_text.available():
                for p in srcs:
                    ocr += [t for t, _c, _b in label_text.ocr_lines(load_img(p), upscale=1.0)]
                sims = label_text.compare([ln["text"] for ln in lines], ocr) if ocr else []
                for ln, s in zip(lines, sims):
                    ln["ocr_sim"] = s["sim"]
                    ln["ocr_found"] = s["found"]
            out["lines"] = lines
            out["ocr_lines"] = ocr
            out["ocr_available"] = label_text.available()
            out["transcribed_at"] = now()
            write_json(self.store.sku_dir(sku_id) / "label_draft.json", out)
            job["done"] = 1
        return self.jobs.start("识别标签", sku_id, run)

    def confirm_label(self, sku_id: str, data: dict) -> None:
        data["confirmed_at"] = now()
        write_json(self.store.sku_dir(sku_id) / "label.json", data)
        self.store.update_sku(sku_id, category=data.get("category", ""))

    def analyze_product_copy(self, sku_id: str, source: str) -> str:
        label = self._confirmed_label(sku_id)
        if not source or len(source) > 6000:
            raise ValueError("请输入 1–6000 字的商品资料。")

        def run(job):
            path = self.store.sku_dir(sku_id) / "product_copy.json"
            draft = {"id": job["id"], "source": source, "items": [], "state": "pending"}
            write_json(path, draft)
            if self.ledger.spent_today() >= float(self.cfg["budget_usd_per_day"]):
                raise BudgetExceeded("今日预算已用完，已停止。")
            evidence = sources_for(label, source)
            result = self.llm().analyze_copy(sku_id, evidence)
            draft["items"] = checked_suggestions(
                result.get("items") if isinstance(result, dict) else None,
                evidence, self.tpl["banned"]["words"],
            )
            draft["state"] = "ready"
            write_json(path, draft)
            job["done"] = 1

        return self.jobs.start("卖点提炼", sku_id, run)

    def use_product_copy(self, sku_id: str, draft_id: str, indices: list[str]) -> list[int]:
        if self.jobs.busy(sku_id):
            raise ValueError("这个商品仍有任务在运行，结束后再采用文案。")
        before = self._confirmed_label(sku_id)
        sku_dir = self.store.sku_dir(sku_id)
        path = sku_dir / "product_copy.json"
        draft = read_json(path, {})
        if not draft_id or draft.get("id") != draft_id or draft.get("state") != "ready":
            raise ValueError("文案草稿已更新，请刷新后再选择。")
        selected = [row for i, row in enumerate(draft["items"]) if str(i) in indices]
        selected = checked_suggestions(selected, sources_for(before, draft["source"]), self.tpl["banned"]["words"])
        if not selected:
            raise ValueError("请勾选至少一条有依据的文案。")
        label = apply_suggestions(before, selected)
        tpl = self.tpl
        updates = {v: transfer_copy(self.store.plan(sku_id, v), label, selected, tpl)
                   for v in self.store.variants(sku_id)}
        write_json(sku_dir / "label.json", label)
        for v, plan in updates.items():
            write_json(self.store.vdir(sku_id, v) / "plan.json", plan)
        draft.update(state="applied", applied_slots=sorted({r["slot"] for r in selected}))
        write_json(path, draft)
        return draft["applied_slots"]

    # ---- F3: plan --------------------------------------------------------
    def _make_plan(self, sku_id: str, variant: str, label: dict) -> dict:
        tpl = self.tpl
        used = [self.store.plan(sku_id, v).get("style", {}) for v in self.store.variants(sku_id)
                if v != variant]
        faces = self.faces_for(label)
        out = self.llm().plan(sku_id, label, tpl["slots"]["slots"], used, faces)
        plan = build_plan(variant, label, tpl["slots"]["slots"], out)
        plan["issues"] = validate(plan, label, tpl["banned"]["words"])
        plan["faces"] = faces
        plan["created"] = now()
        write_json(self.store.vdir(sku_id, variant) / "plan.json", plan)
        return plan

    def make_plan(self, sku_id: str, variant: str) -> str:
        label = self._confirmed_label(sku_id)
        self.store.vdir(sku_id, variant)

        def run(job):
            self._make_plan(sku_id, variant, label)
            job["done"] = 1
        return self.jobs.start("做计划", sku_id, run, variant=variant)

    def _confirmed_label(self, sku_id: str) -> dict:
        sku = self.store.sku(sku_id)
        if not sku or sku.get("deleted_at"):
            raise ValueError("没有这个商品。")
        label = self.store.label(sku_id)
        if not label or not label.get("confirmed_at"):
            raise ValueError("请先确认商品标签。")
        return label

    def generate_suite(self, sku_id: str, variant: str) -> str:
        label = self._confirmed_label(sku_id)
        self.store.vdir(sku_id, variant)

        def run(job):
            plan = self.store.plan(sku_id, variant)
            if plan is None:
                if self.ledger.spent_today() >= float(self.cfg["budget_usd_per_day"]):
                    raise BudgetExceeded("今日预算已用完，已停止。")
                plan = self._make_plan(sku_id, variant, label)
            todo = self._pending_slots(sku_id, variant, plan)
            self._generate_slots(job, sku_id, variant, todo, {})

        return self.jobs.start("生成套图", sku_id, run, total=1, variant=variant)

    def save_plan(self, sku_id: str, variant: str, plan: dict) -> list[str]:
        plan["issues"] = validate(plan, self.store.label(sku_id), self.tpl["banned"]["words"])
        plan["edited"] = now()
        write_json(self.store.vdir(sku_id, variant) / "plan.json", plan)
        return plan["issues"]

    def faces_for(self, label: dict) -> bool:
        f = self.cfg.get("faces", {})
        if f.get("allow_full_face"):
            return True
        return (label or {}).get("category", "") in (f.get("categories") or [])

    def estimate(self, plan: dict) -> tuple[int, float]:
        calls = 0
        for sid in plan_slots(plan):
            calls += 3 if plan["slots"][str(sid)].get("steps") else 1
        return calls, round(calls * float(self.cfg.get("est_usd_per_image", 0.026)), 2)

    # ---- F4/F5: generation + checks -------------------------------------
    def generate(self, sku_id: str, variant: str, slots: list[int] | None = None,
                 notes: dict | None = None) -> str:
        plan = self.store.plan(sku_id, variant)
        todo = self._pending_slots(sku_id, variant, plan, slots)
        redo_slot = todo[0] if slots and len(todo) == 1 else None

        def run(job):
            if redo_slot is not None:
                self.store.set_review(sku_id, variant, redo_slot, status="redo",
                                      note=(notes or {}).get(redo_slot, ""))
            self._generate_slots(job, sku_id, variant, todo, notes or {})

        return self.jobs.start("重出" if redo_slot is not None else "出图", sku_id, run,
                               total=len(todo), variant=variant, slot=redo_slot)

    def _pending_slots(self, sku_id: str, variant: str, plan: dict | None,
                       slots: list[int] | None = None) -> list[int]:
        if not plan:
            raise ValueError("还没有计划")
        if slots and not set(slots).issubset(plan_slots(plan)):
            raise ValueError("这个图位未列入当前出图计划。")
        target = {"slots": {str(s): plan["slots"][str(s)] for s in slots}} if slots else plan
        issues = validate(target, self.store.label(sku_id) or {}, self.tpl["banned"]["words"])
        if issues:
            raise ValueError("计划里有文案问题，先改好再生成：" + "；".join(issues[:3]))
        return slots or [s for s in plan_slots(plan) if not self.store.attempts(sku_id, variant, s)]

    def _generate_slots(self, job: dict, sku_id: str, variant: str, todo: list[int], notes: dict):
        job["total"] = len(todo)
        if not todo:
            return
        gen = Generator(self.cfg, self.ledger, client=self.client)
        futs = [self.gen_pool.submit(self._one_slot, gen, sku_id, variant, s, notes.get(s, ""))
                for s in todo]
        errs = []
        for f in futs:
            try:
                f.result()
            except BudgetExceeded:
                errs.append("今日预算已用完（%s 美元），已停止" % self.cfg["budget_usd_per_day"])
            except Exception as e:  # noqa: BLE001
                errs.append(safe_error(e)[:200])
            job["done"] += 1
        if errs:
            job["error"] = "；".join(sorted(set(errs)))
            job["state"] = "error"

    def _one_slot(self, gen: Generator, sku_id: str, variant: str, slot: int, note: str):
        st = self.store
        plan, label, tpl = st.plan(sku_id, variant), st.label(sku_id), self.tpl
        n = (st.attempts(sku_id, variant, slot) or [0])[-1] + 1
        paths = st.attempt_paths(sku_id, variant, slot, n)
        prompts = prompts_for_slot(slot, plan, label, tpl, plan.get("faces", False), note)
        srcs = st.sources(sku_id)[:4]
        model = self.cfg["generation"]["model"]
        if self.cfg["generation"].get("route_label_heavy") and (
                len(label.get("lines", [])) >= 6 or label.get("suspected_typos")):
            model = self.cfg["generation"]["label_heavy_model"]
        common = dict(sku_id=sku_id, variant=variant, slot=slot, attempt=n, images=srcs, model=model,
                      quality=self.cfg["generation"]["quality"], size=self.cfg["generation"]["size"])
        t0 = time.time()
        try:
            if len(prompts) == 1:
                gen.run(GenRequest(prompt=prompts[0], out_path=paths["gen"], **common))
            else:   # how-to: one image per step, then composed with numbers and captions
                # Each photo is natively 1:3, matching its final panel. Generating
                # square photos first would require cropping away hands/products.
                common["size"] = "512x1536"
                parts = []
                for k, pr in enumerate(prompts, 1):
                    pp = paths["gen"].with_name(f"{slot}_{n}_step{k}.png")
                    if not pp.exists():          # resume without paying twice
                        framing = ("Native 1:3 tall portrait photograph for one full-height panel of a three-step guide. "
                                   "Compose the product and hand action vertically to fill this narrow frame. "
                                   "Keep the entire product and important hand action inside the frame; "
                                   "show exactly ONE product container, never duplicate the jar or bottle; "
                                   "no square inset, no multiple panels, no blank top/bottom bands. ")
                        gen.run(GenRequest(prompt=framing + pr, out_path=pp, **common))
                    parts.append(pp)
                caps = [x.get("caption", "") for x in plan["slots"][str(slot)].get("steps", [])]
                compose_steps(parts, caps, paths["gen"])
        except Blocked as e:
            write_json(paths["qa"], {"error": "内容审核拦截：" + safe_error(e)[:300], "prompt": prompts})
            raise
        qa = self.check(sku_id, variant, slot, n)
        planned_slot = plan["slots"][str(slot)]
        heading, rows = panel_copy(planned_slot)
        if heading or rows:
            for path in (paths["gen"], paths["pasted"]):
                if path.exists():
                    compose_copy_panel(path, heading, rows, white=planned_slot["copy_layout"] == "capacity")
            qa["rendered_copy"] = ([heading] if heading else []) + rows
        qa["prompt"] = prompts
        qa["model"] = model
        qa["sec"] = round(time.time() - t0, 1)
        write_json(paths["qa"], qa)
        st.set_review(sku_id, variant, slot, status="pending", pick=None)

    def source_ocr(self, sku_id: str) -> dict | None:
        """OCR of the main product photo, cached: boxes for the label paste, lines for its guard."""
        if not label_text.available():
            return None
        p = self.store.sku_dir(sku_id) / "source_ocr.json"
        d = read_json(p)
        if d is None:
            src = self.store.sources(sku_id)[0]
            res = label_text.ocr_lines(load_img(src), upscale=1.0)
            d = {"source": src.name, "lines": [t for t, _c, _b in res],
                 "boxes": [np.asarray(b).tolist() for _t, _c, b in res]}
            write_json(p, d)
        return d

    def check(self, sku_id: str, variant: str, slot: int, n: int) -> dict:
        """F5 checks that run locally. Soft by design: they flag, the user decides."""
        st = self.store
        paths = st.attempt_paths(sku_id, variant, slot, n)
        label = st.label(sku_id) or {}
        qa = {"flags": [], "checked_at": now()}
        gen = load_img(paths["gen"])
        final = gen
        plan = st.plan(sku_id, variant)
        is_steps = bool(plan["slots"][str(slot)].get("steps"))
        ocr = self.source_ocr(sku_id)
        if self.cfg.get("repair", {}).get("label_paste_always", True) and not is_steps and ocr:
            from .repair.label_paste import paste_label
            from .repair import paste_guard
            src = load_img(st.sources(sku_id)[0])
            r = paste_label(src, gen, text_boxes=ocr["boxes"])
            if r.ok:
                g = paste_guard.check(gen, r.image, ocr["lines"], zone=r.zone)
                if not g.keep:
                    r.ok, r.status, r.reason = False, "failed", g.reason
            qa["paste"] = {"status": r.status, "reason": r.reason, "coverage": round(r.coverage, 2)}
            if r.ok:
                cv2.imwrite(str(paths["pasted"]), r.image)
                final = r.image
            else:
                qa["flags"].append("原标签没贴回（%s）：图上的小字是模型画的，请看一眼" % r.reason)
        elif not ocr:
            qa["flags"].append("本地 OCR 未安装：没做原标签回贴和大字检查")
        # Q1 big text (local OCR; gross errors only, see 更新说明.md, v0.1)
        big = [b for b in label.get("big_text", []) if b.strip()]
        if big and label_text.available() and not is_steps:
            found = [t for t, _c, _b in label_text.ocr_lines(final)]
            res = label_text.compare(big, found)
            qa["big_text"] = res
            bad = [r["expected"] for r in res if r["sim"] < 0.8]
            if bad:
                qa["flags"].append("包装大字可能不对：" + " / ".join(bad))
        qa["level"] = "warn" if qa["flags"] else "ok"
        return qa


def compose_steps(parts: list[Path], captions: list[str], out: Path, size: int = 1024) -> None:
    """Three full-height portrait steps, never a fourth product-reference tile."""
    if len(parts) != 3:
        raise ValueError("A how-to image requires exactly three step photos")
    canvas = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(canvas, "RGBA")
    gap = 2 * max(1, round(size * 0.004))
    header = round(size * .08) if len(captions) == 3 and all(c.strip() for c in captions) else 0
    width = (size - gap * 2) // 3
    boxes = [(k * (width + gap), header, size if k == 2 else k * (width + gap) + width, size)
             for k in range(3)]
    if header:
        d.text((size // 2, header // 2), "HOW TO USE", fill=(28, 44, 55, 255),
               font=copy_font(round(size * .042)), anchor="mm")

    for tile, (p, (x0, y0, x1, y1)) in enumerate(zip(parts, boxes), 1):
        with Image.open(p) as src:
            im = ImageOps.exif_transpose(src).convert("RGBA")
            bg = Image.new("RGBA", im.size, "white")
            bg.alpha_composite(im)
            im = ImageOps.fit(bg.convert("RGB"), (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
        canvas.paste(im, (x0 + (x1 - x0 - im.width) // 2, y0 + (y1 - y0 - im.height) // 2))
        pad = max(8, round(size * 0.02))
        radius = max(14, round(size * 0.025))
        cx, cy = x0 + pad + radius, y0 + pad + radius
        d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 255, 255, 238))
        d.text((cx, cy), str(tile), fill=(28, 44, 55, 255), font=copy_font(round(size * 0.03)), anchor="mm")
        cap = captions[tile - 1].strip() if tile - 1 < len(captions) else ""
        if not cap:
            continue
        # Measure the confirmed copy and wrap before reducing its font size.
        for fs in range(max(12, round(size * 0.026)), 0, -1):
            block = copy_block(cap, fs, x1 - x0 - pad * 2)
            if block.height <= (y1 - y0) * 0.30:
                break
        height = block.height + pad
        d.rectangle((x0, y1 - height, x1, y1), fill=(16, 28, 39, 200))
        block.draw(d, x0 + pad, y1 - height + pad // 2, "white")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
