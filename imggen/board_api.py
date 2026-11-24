"""JSON API for the single-page batch workbench (v1.7).

The new front end (designed by Codex, see docs/决策记录与设计需求.md) uses only these
endpoints plus the existing /api/review, /api/jobs, /api/slot, /files/... routes.
Nothing here calls OpenAI by itself except intake (starts label reading) and
confirm/start (starts a set), exactly like the old pages.

  GET  /api/board                      every product with its stage, progress and the capacity bar
  GET  /api/sku/{sku}                  one product: photos, label draft/confirmed, slots, all versions
  POST /api/intake                     one product (files: images; name optional) -> reads the label
  POST /api/sku/{sku}/confirm          JSON label (+ "start": true) -> confirmed, set queued
  POST /api/sku/{sku}/start            {"variant": "A"} -> set queued
  POST /api/sku/{sku}/slot-copy        {"variant","slot", fields...} -> edit one slot's copy (validated)
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from .archive import latest_folder
from .config import api_key_present
from .plan import check_text, text_lines, validate
from .store import now, plan_slots, read_json, write_json

STAGES = {
    "new": "待识别", "reading": "识别中", "confirm": "待确认标签", "ready": "待开始",
    "queued": "排队中", "generating": "生成中", "proof": "待校样", "done": "已完成", "error": "有问题",
}
GEN_KINDS = {"生成套图", "出图", "重出"}
EDITABLE = {"headline": str, "bullets": list, "ingredient_labels": list, "texture_caption": str,
            "scene_caption": str, "capacity": str}


def register(app, core, furl):
    st = core.store

    def running_jobs(sku_id: str) -> list[dict]:
        return [j for j in core.jobs.recent(200) if j["sku"] == sku_id and j["state"] == "running"]

    def slot_names() -> dict[int, str]:
        return {int(t["id"]): t["name"] for t in core.tpl["slots"]["slots"]}

    def variant_summary(sku_id: str, v: str) -> dict:
        plan = st.plan(sku_id, v)
        rev = st.review(sku_id, v)
        slots = plan_slots(plan)
        made = approved = flagged = 0
        for s in slots:
            cur = st.current(sku_id, v, s)
            if cur:
                made += 1
                if cur["qa"].get("flags") or cur["qa"].get("error"):
                    flagged += 1
            if rev.get(str(s), {}).get("status") == "approved":
                approved += 1
        return {"variant": v, "slots": len(slots), "made": made, "approved": approved, "flagged": flagged}

    def stage_of(sku_id: str, jobs: list[dict], variants: list[dict]) -> str:
        kinds = {j["kind"] for j in jobs}
        if "识别标签" in kinds:
            return "reading"
        gen = [j for j in jobs if j["kind"] in GEN_KINDS]
        if gen:
            return "queued" if all(j.get("phase") == "queued" for j in gen) else "generating"
        if st.label(sku_id) is None:
            return "confirm" if st.label_draft(sku_id) else "new"
        if not variants or any(x["made"] < x["slots"] for x in variants):
            return "ready"
        if all(x["approved"] == x["slots"] for x in variants):
            return "done"
        return "proof"

    def product_row(sku_id: str) -> dict:
        sku = st.sku(sku_id)
        srcs = st.sources(sku_id)
        jobs = running_jobs(sku_id)
        variants = [variant_summary(sku_id, v) for v in st.variants(sku_id)]
        last = next((j for j in core.jobs.recent(200) if j["sku"] == sku_id), None)
        stage = stage_of(sku_id, jobs, variants)
        progress = None
        gen = [j for j in jobs if j["kind"] in GEN_KINDS]
        if gen:
            progress = {"done": sum(j["done"] for j in gen), "total": sum(j["total"] for j in gen)}
        return {
            "id": sku_id, "name": sku.get("name", sku_id), "category": sku.get("category", ""),
            "created": sku.get("created", ""), "thumb": furl(srcs[0]) if srcs else "",
            "stage": stage, "stage_label": STAGES[stage], "progress": progress,
            "variants": variants,
            "jobs": [{k: j.get(k) for k in ("id", "kind", "variant", "slot", "done", "total", "phase")} for j in jobs],
            "last_error": last["error"] if last and last["state"] in {"error", "interrupted"} else "",
            "archived_to": str(latest_folder(core.cfg["workspace"], sku_id) or ""),
        }

    def capacity() -> dict:
        t = core.cfg.get("throughput") or {}
        sets = [j for j in core.jobs.recent(200) if j["state"] == "running" and j["kind"] == "生成套图"]
        return {
            "images_per_minute": t.get("images_per_minute", 150), "use_ratio": t.get("use_ratio", 0.8),
            "max_parallel_sets": core.max_sets,
            "running_sets": sum(1 for j in sets if j.get("phase") != "queued"),
            "queued_sets": sum(1 for j in sets if j.get("phase") == "queued"),
            "images_last_minute": core.gate.used(),
            "budget_usd_per_day": float(core.cfg.get("budget_usd_per_day", 10)),
            "spent_today_usd": core.ledger.spent_today(),
            "est_usd_per_image": float(core.cfg.get("est_usd_per_image", 0.026)),
        }

    @app.get("/api/board")
    def api_board():
        rows = [product_row(s) for s in st.list_skus() if not st.sku(s).get("deleted_at")]
        counts = {k: 0 for k in STAGES}
        for r in rows:
            counts[r["stage"]] += 1
        mode = "demo" if core.cfg.get("demo_mode") else "live" if api_key_present() else "unconfigured"
        return JSONResponse({"products": rows, "counts": counts, "stages": STAGES, "capacity": capacity(),
                             "generation_mode": mode,
                             "time": now()})

    @app.get("/api/sku/{sku_id}")
    def api_sku(sku_id: str):
        sku = st.sku(sku_id)
        if not sku or sku.get("deleted_at"):
            return JSONResponse({"error": "没有这个商品"}, status_code=404)
        names = slot_names()
        variants = {}
        for v in st.variants(sku_id):
            plan = st.plan(sku_id, v)
            rev = st.review(sku_id, v)
            slots = []
            for sid, s in sorted(plan.get("slots", {}).items(), key=lambda kv: int(kv[0])):
                slot = int(sid)
                r = rev.get(sid, {})
                busy = core.jobs.redo_for(sku_id, v, slot)
                cur = None if s.get("skip") else st.current(sku_id, v, slot)
                versions = []
                for n in st.attempts(sku_id, v, slot):
                    p = st.attempt_paths(sku_id, v, slot, n)
                    qa = read_json(p["qa"], {})
                    gen = p["gen_final"] if p["gen_final"].exists() else p["gen"]
                    pasted = (p["pasted_final"] if p["pasted_final"].exists() else p["pasted"]) if p["pasted"].exists() else None
                    versions.append({"n": n, "generated": furl(gen), "pasted": furl(pasted) if pasted else "",
                                     "flags": qa.get("flags", []), "error": qa.get("error", ""),
                                     "legacy_strip": bool(qa.get("rendered_copy")),
                                     "added_text": qa.get("added_text"), "sec": qa.get("sec")})
                slots.append({
                    "slot": slot, "name": names.get(slot, s.get("name", "")),
                    "skip": bool(s.get("skip")), "skip_reason": s.get("skip_reason", ""),
                    "copy": [{"role": role, "text": t} for role, t in text_lines(s)] if not s.get("steps") else
                            [{"role": "step", "text": x.get("caption", "")} for x in s.get("steps", [])],
                    "status": r.get("status", "") or ("pending" if cur else "empty"),
                    "note": r.get("note", ""), "archived": r.get("archived", ""),
                    "busy": bool(busy), "busy_phase": (busy or {}).get("phase", ""),
                    "current": None if cur is None else {
                        "n": cur["n"], "file": furl(cur["file"]),
                        "which": "pasted" if cur["file"] == cur["pasted"] else "generated",
                        "flags": cur["qa"].get("flags", []), "version": cur["version"]},
                    "versions": versions,
                })
            variants[v] = {"slots": slots, "issues": plan.get("issues", []), "style": plan.get("style", {})}
        return JSONResponse({
            "id": sku_id, "name": sku.get("name", sku_id), "category": sku.get("category", ""),
            "sources": [furl(p) for p in st.sources(sku_id)],
            "label_draft": st.label_draft(sku_id), "label": st.label(sku_id),
            "product_copy": read_json(st.sku_dir(sku_id) / "product_copy.json"),
            "variants": variants, "row": product_row(sku_id),
        })

    @app.post("/api/intake")
    async def api_intake(request: Request):
        """One product per call: dropped, picked or pasted images (several angles allowed)."""
        f = await request.form()
        items = []
        for up in f.getlist("files"):
            data = await up.read()
            if data:
                items.append((up.filename or "image.png", data))
        if not items:
            return JSONResponse({"error": "没有收到图片"}, status_code=400)
        name = str(f.get("name") or "").strip()
        if not name:
            stem = Path(items[0][0]).stem
            name = stem if stem and not re.fullmatch(r"(?i)image|clipboard|paste|未命名.*", stem) \
                else "新商品 " + now()[5:16].replace("T", " ")
        try:
            sku_id = st.create_sku(name, sorted(items))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        job = ""
        if str(f.get("read_label", "1")) != "0":
            try:
                job = core.transcribe(sku_id)
            except Exception as e:  # noqa: BLE001 - product is saved; reading can be retried
                return JSONResponse({"sku": sku_id, "job_id": "", "warning": str(e)})
        return JSONResponse({"sku": sku_id, "job_id": job})

    @app.post("/api/sku/{sku_id}/read-label")
    def api_read_label(sku_id: str):
        try:
            return JSONResponse({"job_id": core.transcribe(sku_id)})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.post("/api/sku/{sku_id}/confirm")
    async def api_confirm(request: Request, sku_id: str):
        """Body: label fields (same keys as label.json) and optional "start": true, "variant"."""
        d = await request.json()
        if core.jobs.busy(sku_id):
            return JSONResponse({"error": "这个商品正在处理，请稍后再确认。"}, status_code=409)
        lines = [{"text": str(x.get("text", "")).strip(), "size": x.get("size", "small")}
                 for x in d.get("lines", []) if str(x.get("text", "")).strip()]
        clean = lambda xs: list(dict.fromkeys(str(x).strip() for x in xs or [] if str(x).strip()))  # noqa: E731
        draft = st.label_draft(sku_id) or {}
        data = {"lines": lines, "big_text": [x["text"] for x in lines if x["size"] == "big"],
                "allowed_claims": clean(d.get("allowed_claims")),
                "product_name": str(d.get("product_name", "")).strip(),
                "capacity": str(d.get("capacity", "")).strip(),
                "pieces": int(d.get("pieces") or 1), "pieces_text": str(d.get("pieces_text", "")).strip(),
                "category": str(d.get("category", "")), "key_ingredient": str(d.get("key_ingredient", "")).strip(),
                "ingredient_names": clean(d.get("ingredient_names")),
                "texture": str(d.get("texture", "")).strip(),
                "texture_caption": str(d.get("texture_caption", "")).strip(),
                "scene_caption": str(d.get("scene_caption", "")).strip(),
                "usage_steps": clean(d.get("usage_steps"))[:3],
                "copy_sources": (st.label(sku_id) or {}).get("copy_sources", []),
                "suspected_typos": draft.get("suspected_typos", [])}
        if not lines:
            return JSONResponse({"error": "标签文字不能为空"}, status_code=400)
        warnings = []
        for c in data["allowed_claims"]:
            warnings += check_text(c, data, core.tpl["banned"]["words"], "bullet")
        core.confirm_label(sku_id, data)
        out = {"ok": True, "warnings": warnings}
        if d.get("start"):
            try:
                out["job_id"] = core.generate_suite(sku_id, str(d.get("variant") or "A"))
            except ValueError as e:
                out["start_error"] = str(e)
        return JSONResponse(out)

    @app.post("/api/sku/{sku_id}/start")
    async def api_start(request: Request, sku_id: str):
        d = await request.json() if (await request.body()) else {}
        try:
            return JSONResponse({"job_id": core.generate_suite(sku_id, str(d.get("variant") or "A"))})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.post("/api/sku/{sku_id}/slot-copy")
    async def api_slot_copy(request: Request, sku_id: str):
        """Edit the confirmed copy of one slot before a redo. Validated against the label (C4)."""
        d = await request.json()
        v, slot = str(d.get("variant") or "A"), str(int(d["slot"]))
        plan = st.plan(sku_id, v)
        if not plan or slot not in plan.get("slots", {}):
            return JSONResponse({"error": "没有这个图位"}, status_code=404)
        s = dict(plan["slots"][slot])
        for k, typ in EDITABLE.items():
            if k in d:
                s[k] = [str(x).strip() for x in d[k] if str(x).strip()] if typ is list else str(d[k]).strip()
        if "steps" in d and s.get("steps"):
            for i, cap in enumerate(d["steps"][:len(s["steps"])]):
                s["steps"][i] = dict(s["steps"][i], caption=str(cap).strip())
        issues = validate({"slots": {slot: s}}, st.label(sku_id) or {}, core.tpl["banned"]["words"])
        if issues:
            return JSONResponse({"error": "；".join(issues[:4]), "issues": issues}, status_code=400)
        plan["slots"][slot] = s
        plan["edited"] = now()
        write_json(st.vdir(sku_id, v) / "plan.json", plan)
        return JSONResponse({"ok": True, "copy": [{"role": r, "text": t} for r, t in text_lines(s)]})
