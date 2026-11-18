"""The production flow: transcribe (F2) -> confirm -> plan (F3) -> generate (F4) -> checks (F5).

Long steps run as background jobs in this process; every result is written to disk first,
so a crash or restart only loses the call in flight (C7). Calls never repeat for slots that
already have an image unless the user asks for a redo.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import load_config
from .gen.generate import Blocked, BudgetExceeded, GenRequest, Generator
from .gen.ledger import Ledger
from .llm import LLM
from .plan import build_plan, load_templates, prompts_for_slot, validate
from .qa import label_text
from .store import Store, now, plan_slots, read_json, write_json


def make_client(cfg: dict):
    if cfg.get("demo_mode"):
        from .fake import FakeOpenAI
        return FakeOpenAI()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("没有设置 OPENAI_API_KEY。请在终端 export 后重启，或在设置里打开演示模式")
    from openai import OpenAI
    return OpenAI()


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

    def start(self, kind: str, sku_id: str, fn, total: int = 1, variant: str = "") -> str:
        jid = uuid.uuid4().hex[:8]
        job = dict(id=jid, kind=kind, sku=sku_id, variant=variant, total=total, done=0,
                   state="running", error="", started=time.time())
        with self.lock:
            self.jobs[jid] = job

        def run():
            try:
                fn(job)
                if job["state"] == "running":
                    job["state"] = "done"
            except Exception as e:  # noqa: BLE001
                job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"
                traceback.print_exc()
        self.pool.submit(run)
        return jid

    def busy(self, sku_id: str) -> bool:
        return any(j["sku"] == sku_id and j["state"] == "running" for j in self.jobs.values())

    def recent(self, n: int = 30) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: -j["started"])[:n]


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
        self._client = None
        self.gen_pool = ThreadPoolExecutor(max_workers=int(cfg.get("concurrency", 6)))

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

    # ---- F3: plan --------------------------------------------------------
    def make_plan(self, sku_id: str, variant: str) -> str:
        def run(job):
            label = self.store.label(sku_id)
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
            job["done"] = 1
        return self.jobs.start("做计划", sku_id, run, variant=variant)

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
        if not plan:
            raise ValueError("还没有计划")
        if plan.get("issues"):
            raise ValueError("计划里有文案问题，先改好再生成：" + "；".join(plan["issues"][:3]))
        todo = slots or [s for s in plan_slots(plan) if not self.store.attempts(sku_id, variant, s)]
        notes = notes or {}

        def run(job):
            job["total"] = len(todo)
            gen = Generator(self.cfg, self.ledger, client=self.client)
            futs = [self.gen_pool.submit(self._one_slot, gen, sku_id, variant, s, notes.get(s, ""))
                    for s in todo]
            errs = []
            for f in futs:
                try:
                    f.result()
                except BudgetExceeded as e:
                    errs.append("今日预算已用完（%s 美元），已停止" % self.cfg["budget_usd_per_day"])
                    del e
                except Exception as e:  # noqa: BLE001
                    errs.append(str(e)[:200])
                job["done"] += 1
            if errs:
                job["state"], job["error"] = "error", "；".join(sorted(set(errs)))
        return self.jobs.start("出图", sku_id, run, total=len(todo), variant=variant)

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
                parts = []
                for k, pr in enumerate(prompts, 1):
                    pp = paths["gen"].with_name(f"{slot}_{n}_step{k}.png")
                    if not pp.exists():          # resume without paying twice
                        gen.run(GenRequest(prompt=pr, out_path=pp, **common))
                    parts.append(pp)
                caps = [x.get("caption", "") for x in plan["slots"][str(slot)].get("steps", [])]
                compose_steps(parts, caps, paths["gen"])
        except Blocked as e:
            write_json(paths["qa"], {"error": "内容审核拦截：" + str(e)[:300], "prompt": prompts})
            raise
        qa = self.check(sku_id, variant, slot, n)
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
        # Q1 big text (local OCR; gross errors only, see CHANGELOG v0.1)
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
    """How-to slot: three step photos side by side, numbered, with confirmed captions (Pillow)."""
    canvas = Image.new("RGB", (size, size), (247, 243, 238))
    d = ImageDraw.Draw(canvas)
    n = len(parts)
    gap = 24
    w = (size - gap * (n + 1)) // n
    top = (size - w) // 2 - 40

    def font(sz):
        for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"):
            try:
                return ImageFont.truetype(f, sz)
            except OSError:
                pass
        return ImageFont.load_default()
    fnum, fcap = font(34), font(30)
    for k, p in enumerate(parts):
        im = Image.open(p).convert("RGB").resize((w, w))
        x = gap + k * (w + gap)
        canvas.paste(im, (x, top))
        cx, cy, r = x + 34, top + 34, 24
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255))
        d.text((cx, cy), str(k + 1), fill=(40, 40, 40), font=fnum, anchor="mm")
        cap = captions[k] if k < len(captions) else ""
        lines, cur = [], ""
        for word in cap.split():           # wrap to the panel width
            t = (cur + " " + word).strip()
            if d.textlength(t, font=fcap) <= w - 10 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        for i, ln in enumerate(lines[:3]):
            d.text((x + w // 2, top + w + 40 + i * 36), ln, fill=(40, 40, 40), font=fcap, anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
