"""Local web tool. Run:  python app.py   then the browser opens http://127.0.0.1:8765

Only listens on this computer. The OpenAI key is read from the OPENAI_API_KEY environment
variable and is never shown, stored or logged.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import secrets
import threading
import urllib.parse
import webbrowser
from datetime import date
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from imggen.config import ROOT, api_key_present, load_config
from imggen.credentials import keychain_available, load_keychain_key, safe_error, save_keychain_key, validate_api_key
from imggen.pipeline import App
from imggen.plan import ingredient_names, panel_copy, validate
from imggen.product_copy import COPY_TYPES
from imggen.store import plan_slots, read_json

E = lambda s: html.escape(str(s if s is not None else ""))  # noqa: E731
CATS = ["serum", "cream", "mask", "cleanser", "toner", "lip", "eye", "body", "other"]
SIZE_CN = {"big": "大", "medium": "中", "small": "小"}

WEB = Path(__file__).resolve().parent / "web"
LOCAL_CLIPS = ("window", "box", "sand", "silk")


def create_app(cfg: dict | None = None) -> FastAPI:
    core = App(cfg)
    app = FastAPI(title="temu-imggen")
    ws = Path(core.cfg["workspace"]).resolve()
    key_form_token = secrets.token_urlsafe(32)
    key_save_lock = threading.Lock()

    def furl(p: Path | None) -> str:
        if not p:
            return ""
        rel = Path(p).resolve().relative_to(ws)
        v = int(Path(p).stat().st_mtime) if Path(p).exists() else 0
        return "/files/" + urllib.parse.quote(str(rel)) + f"?v={v}"

    def spent() -> float:
        return core.ledger.spent_today()


    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def landing():
        local_clips = " ".join(k for k in LOCAL_CLIPS if (ws / "landing-media" / f"{k}-tight.mp4").is_file())
        return HTMLResponse((WEB / "landing.html").read_text(encoding="utf-8").replace("{{LOCAL_CLIPS}}", local_clips))

    @app.get("/showcase-media/{asset}")
    def showcase_media(asset: str):
        if asset not in {f"{k}-tight.mp4" for k in LOCAL_CLIPS}:
            return HTMLResponse("not found", 404)
        f = ws / "landing-media" / asset
        if not f.is_file():
            return HTMLResponse("not found", 404)
        return FileResponse(f, media_type="video/mp4")

    def page(title: str, body: str, sku: str | None = None, *, active: str = "",
             desc: str = "", actions: str = "", crumb: str = "") -> HTMLResponse:
        routes = [("library", "/library", "产品库"), ("plan", "/plan", "套图计划"),
                  ("review", "/review", "审核"), ("export", "/export", "导出")]
        if not active:
            active = {"审核墙": "review", "导出": "export", "设置": "settings", "账本": "ledger"}.get(
                title, "plan" if sku else "library")
        if sku:
            routes[1] = ("plan", f"/sku/{urllib.parse.quote(sku)}", "套图计划")
        def item(key, url, label):
            current = ' aria-current="page"' if key == active else ""
            return f'<a href="{url}"{current}>{label}</a>'
        nav = "".join(item(*route) for route in routes)
        settings = item("settings", "/settings", "设置")
        ledger = item("ledger", "/ledger", "账本")
        if core.cfg.get("demo_mode"):
            mode = '<span class="tag warn">演示模式</span>'
        else:
            mode = ('<span class="connection ready">密钥已配置</span>' if api_key_present() else
                    '<a class="connection" href="/settings#openai-api">配置生成服务</a>')
        trail = f'<nav class="crumb" aria-label="当前位置">{crumb}</nav>' if crumb else ""
        sub = f'<p class="page-desc">{E(desc)}</p>' if desc else ""
        css_version = (WEB / "static" / "studio.css").stat().st_mtime_ns
        js_version = (WEB / "static" / "studio.js").stat().st_mtime_ns
        return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{E(title)} · Temu 主图工作台</title>
<link rel="stylesheet" href="/static/studio.css?v={css_version}">
<script src="/static/studio.js?v={js_version}" defer></script></head><body data-sku="{E(sku)}">
<div class="shell"><aside class="sidebar"><a class="brand" href="/library"><img src="/static/assets/mark.png" alt="">主图工作台</a>
<nav class="snav" aria-label="制作流程">{nav}</nav>
<div class="sidebar-foot"><nav class="snav" aria-label="工作台管理">{settings}{ledger}</nav><div class="service-status">{mode}</div></div></aside>
<main id="main"><header class="pagehead"><div>{trail}<h1>{E(title)}</h1>{sub}</div>{actions}</header>
<section id="production-progress" class="production-progress" aria-label="任务进度" hidden></section>
{body}<details class="jobline"><summary>任务记录</summary><ul id="jobs"></ul></details></main></div>
<dialog id="modal" aria-labelledby="compare-title"><header><h2 id="compare-title">图片对照</h2><form method="dialog"><button>关闭</button></form></header><div class="imgs"></div></dialog>
</body></html>""", headers={"Cache-Control": "no-store"})

    def back(url: str, msg: str = "") -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + urllib.parse.quote(msg)
        return RedirectResponse(url, status_code=303)

    def flash(request: Request) -> str:
        m = request.query_params.get("msg")
        if not m or request.query_params.get("job"):
            return ""
        if re.search(r"\bgpt[-\w.]*|Error code:|Traceback", m, re.I):
            m = "处理失败，请检查设置后重试。"
        return f'<div class="flash" role="status">{E(m)}</div>'

    def sku_cost(sku_id: str) -> float:
        tot = 0.0
        with core.ledger.path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["sku_id"] == sku_id and r["cost_usd"]:
                    tot += float(r["cost_usd"])
        return tot

    # ------------------------------------------------------------ files
    @app.get("/files/{path:path}")
    def files(path: str):
        p = (ws / path).resolve()
        if ws not in p.parents or not p.is_file():
            return HTMLResponse("not found", 404)
        return FileResponse(p)

    # ------------------------------------------------------------ F1 product library
    @app.get("/library", response_class=HTMLResponse)
    def index(request: Request, status: str = "", q: str = ""):
        st = core.store
        rows = []
        counts: dict[str, int] = {}
        for sid in st.list_skus():
            s = st.sku(sid)
            busy = core.jobs.busy(sid)
            stat = st.status(sid, busy)
            counts[stat] = counts.get(stat, 0) + 1
            if (status and stat != status) or (q.strip().casefold() not in f'{s.get("name", "")} {sid}'.casefold()):
                continue
            srcs = st.sources(sid)

            thumb = f'<img class="table-thumb" src="{furl(srcs[0])}" alt="">' if srcs else ""
            cls = {"已导出": "ok", "待审核": "warn", "有问题": "bad", "生成中": "warn", "待确认": "warn"}.get(stat, "")
            rows.append(f'<tr><td><a class="lib-pair" href="/sku/{E(sid)}">{thumb}<span><b>{E(s.get("name"))}</b>'
                        f'<span class="mute" style="display:block">{E(sid)}</span></span></a></td>'
                        f'<td><span class="tag {cls}">{E(stat)}</span></td><td>{E(s.get("category") or "—")}</td>'
                        f'<td>${sku_cost(sid):.3f}</td><td><div class="product-actions"><a class="btn" href="/sku/{E(sid)}">打开</a>'
                        f'<form method="post" action="/sku/{E(sid)}/delete"><button class="danger" {"disabled" if busy else ""}'
                        f' aria-label="删除商品 {E(s.get("name"))}">删除</button></form></div></td></tr>')
        def filter_link(key, label):
            url = "/library" + ("?" + urllib.parse.urlencode({k:v for k,v in {"status":key,"q":q}.items() if v}) if key or q else "")
            return f'<a class="btn" href="{E(url)}" aria-current="{"true" if key == status else "false"}">{label}</a>'
        filt = filter_link("", f"全部 {sum(counts.values())}") + " ".join(filter_link(k,f"{E(k)} {v}") for k,v in counts.items())
        deleted = request.query_params.get("deleted", "")
        undo = ""
        if deleted:
            try:
                removed = st.sku(deleted)
            except ValueError:
                removed = {}
            if removed.get("deleted_at"):
                undo = (f'<div class="card row" role="status">已删除商品「{E(removed.get("name"))}」。'
                        f'<form method="post" action="/sku/{E(deleted)}/restore"><button>撤销删除</button></form></div>')
        body = f"""{flash(request)}{undo}
<div class="library-workspace"><section class="card collection"><div class="row section-top"><h2>商品列表</h2>
<form action="/library" method="get" class="row"><input type="hidden" name="status" value="{E(status)}">
<input type="search" name="q" class="field-search" aria-label="搜索商品" placeholder="搜索商品名或 SKU" value="{E(q)}"><button>搜索</button></form></div>
<div class="row library-filter" style="margin-bottom:13px">{filt}</div>
<div class="table-scroll"><table class="library-table compact"><thead><tr><th>商品</th><th>状态</th><th>类目</th><th>已用费用</th><th>操作</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan=5 class=empty>没有符合条件的商品。</td></tr>'}</tbody></table></div></section>
<section class="card" id="drop-area"><h2>导入商品</h2>
<div id="drop" class="drop"><b>把商品图片或文件夹拖到这里</b>
<p class="mute">支持 JPG、PNG、WebP。一个文件夹对应一个商品，原图不会被改动。</p>
<label class="btn" tabindex="0" onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();this.querySelector('input').click()}}">选图片<input type="file" id="pickFiles" multiple accept="image/*" hidden></label>
<label class="btn" tabindex="0" onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();this.querySelector('input').click()}}">选文件夹<input type="file" id="pickDir" webkitdirectory multiple hidden></label></div>
<div id="pick"></div></section></div>"""
        return page("产品库", body)

    @app.post("/sku/{sku_id}/delete")
    def delete_product(sku_id: str):
        if core.jobs.busy(sku_id):
            return back("/library", "商品还有任务进行中，请完成后再删除")
        try:
            core.store.delete_sku(sku_id)
        except (ValueError, FileNotFoundError):
            return HTMLResponse("没有这个商品", status_code=404)
        return back("/library?" + urllib.parse.urlencode({"deleted": sku_id}))

    @app.post("/sku/{sku_id}/restore")
    def restore_product(sku_id: str):
        try:
            core.store.restore_sku(sku_id)
        except (ValueError, FileNotFoundError):
            return HTMLResponse("没有这个商品", status_code=404)
        return back("/library", "商品已恢复")

    @app.post("/sku")
    async def new_sku(name: str = Form(...), files: list[UploadFile] = File(...)):
        data = [(f.filename or "x.jpg", await f.read()) for f in files]
        try:
            sid = core.store.create_sku(name, data)
        except ValueError as e:
            return back("/library", str(e))
        return back(f"/sku/{sid}", "已导入，下一步：识别标签")

    @app.post("/upload")
    async def upload(request: Request):
        f = await request.form()
        files, paths = f.getlist("files"), f.getlist("paths")
        mode = str(f.get("mode") or "one")
        groups: dict[str, list] = {}
        loose = []
        for i, up in enumerate(files):
            path = str(paths[i]) if i < len(paths) else up.filename
            parts = [x for x in path.split("/") if x]
            item = (up.filename or parts[-1], await up.read())
            if len(parts) > 1:
                groups.setdefault(parts[-2], []).append(item)
            else:
                loose.append(item)
        if loose:
            if mode == "each":
                for name, data in loose:
                    groups.setdefault(Path(name).stem, []).append((name, data))
            else:
                groups.setdefault(Path(loose[0][0]).stem, []).extend(loose)
        made = []
        for name, items in groups.items():
            try:
                made.append(core.store.create_sku(name, sorted(items)))
            except ValueError:
                pass
        if not made:
            return JSONResponse({"error": "没有可用的图片（支持 jpg / png / webp）"})
        if len(made) == 1:
            return JSONResponse({"go": f"/sku/{urllib.parse.quote(made[0])}?msg=" + urllib.parse.quote("已导入。产品名可以在下面改；下一步：识别标签")})
        return JSONResponse({"go": "/library?msg=" + urllib.parse.quote(f"导入了 {len(made)} 个产品")})

    @app.post("/sku/{sku_id}/rename")
    def rename(sku_id: str, name: str = Form(...)):
        if name.strip():
            core.store.update_sku(sku_id, name=name.strip())
        return back(f"/sku/{sku_id}", "已改名")

    @app.post("/import")
    def import_folder(folder: str = Form(...)):
        try:
            made = core.store.import_folder(folder.strip().strip("'\""))
        except ValueError as e:
            return back("/library", str(e))
        return back("/library", f"导入了 {len(made)} 个 SKU：" + "、".join(made))


    # ------------------------------------------------------------ SKU page
    @app.get("/plan", response_class=HTMLResponse)
    def plan_index(request: Request):
        st = core.store
        rows = []
        for sid in st.list_skus():
            src = st.sources(sid)
            thumb = f'<img class="table-thumb" src="{furl(src[0])}" alt="">' if src else ''
            name = st.sku(sid).get("name",sid)
            count = len(st.variants(sid))
            rows.append(f'<tr><td><a class="lib-pair" href="/sku/{E(sid)}">{thumb}<b>{E(name)}</b></a></td>'
                        f'<td>{count} 套</td><td>{E(st.status(sid,core.jobs.busy(sid)))}</td>'
                        f'<td><a class="btn" href="/sku/{E(sid)}">打开计划</a></td></tr>')
        body = f"""{flash(request)}<section class="card collection"><div class="row section-top"><h2>选择商品</h2><a href="/library">去产品库</a></div>
<div class="table-scroll"><table class="compact"><thead><tr><th>商品</th><th>当前计划</th><th>状态</th><th>操作</th></tr></thead><tbody>
{''.join(rows) or '<tr><td colspan=4 class=empty>还没有商品，请先从产品库导入。</td></tr>'}</tbody></table></div></section>"""
        return page("套图计划", body, active="plan")

    @app.get("/sku/{sku_id}", response_class=HTMLResponse)
    def sku_page(request: Request, sku_id: str, edit: int = 0):
        st = core.store
        s = st.sku(sku_id)
        if not s or s.get("deleted_at"):
            return back("/library", "没有这个 SKU")
        sources = st.sources(sku_id)
        busy = core.jobs.busy(sku_id)
        label, draft = st.label(sku_id), st.label_draft(sku_id)
        picked = st.variants(sku_id)
        requested_variant = request.query_params.get("variant", "")
        active = requested_variant if requested_variant in picked else (picked[0] if picked else "")
        name = s.get("name") or sku_id
        main_pic = (f'<a class="suite-ref-photo" href="{E(furl(sources[0]))}" target="_blank" rel="noopener">'
                    f'<img src="{E(furl(sources[0]))}" alt="{E(name)} 商品参考原图"></a>' if sources else
                    '<div class="suite-ref-photo suite-ref-empty">尚未上传参考图</div>')
        other_pics = "".join(f'<a href="{E(furl(p))}" target="_blank" rel="noopener">'
                             f'<img src="{E(furl(p))}" alt="参考图 {i}"></a>' for i,p in enumerate(sources[1:], 2))
        label_status = "包装文字已确认" if label else ("包装文字待确认" if draft else "尚未识别包装文字")
        summary = "".join(f'<li>{E(x)}</li>' for x in (label or {}).get("allowed_claims", []))
        req = (f'<div class="suite-claims"><span>已确认的卖点</span><ul>{summary}</ul></div>' if summary else "")
        copy_link = '<a href="#product-copy">编辑商品卖点</a>' if label and not edit else ""
        reference = f"""<aside class="suite-reference" aria-label="商品资料"><h2>商品原图</h2>
{main_pic}<div class="suite-ref-thumbs">{other_pics}</div>
<p class="suite-meta">{E(sku_id)}</p>
<div class="suite-meta">{label_status} · ${sku_cost(sku_id):.3f}</div>
<details class="suite-details"><summary>修改商品名称</summary>
<form method="post" action="/sku/{E(sku_id)}/rename" class="row">
<input type="text" name="name" aria-label="商品名称" value="{E(name)}" required><button>改名</button>
</form></details>
<div class="suite-requirements">{copy_link}{req}</div></aside>"""
        if label and not edit:
            right = variants_block(sku_id, busy, active)
            right += product_copy_form(sku_id, busy)
            right += '<details class="card suite-details suite-label-detail"><summary>已确认标签</summary>' + label_summary(sku_id, label) + '</details>'
        elif draft or (label and edit):
            right = label_form(sku_id, label if (label and edit) else draft, from_label=bool(label and edit))
        elif not (core.cfg.get("demo_mode") or api_key_present()):
            right = '<div class="suite-empty"><h2>请先配置生成服务</h2><a class="btn p" href="/settings#openai-api">去设置</a></div>'
        else:
            right = f"""<div class="suite-empty"><h2>先识别商品标签</h2>
<p>从原图识别包装文字。核对确认后，就可以开始制作套图。</p>
<form method="post" action="/sku/{E(sku_id)}/transcribe"><button class="p" {'disabled' if busy else ''}>识别标签</button></form>
</div>"""
        body = f"""{flash(request)}<div class="suite-page">
<div class="suite-layout">{reference}<section class="suite-main" aria-label="套图计划">{right}</section></div>
</div>"""
        return page(name, body, sku_id, active="plan", crumb='<a href="/library">产品库</a><span aria-hidden="true">/</span><a href="/plan">套图计划</a>')

    def product_copy_form(sku_id: str, busy: bool) -> str:
        draft = read_json(core.store.sku_dir(sku_id) / "product_copy.json", {})
        items = draft.get("items", [])
        locked = " disabled" if busy else ""
        suggestions = ""
        if draft.get("state") == "ready" and items:
            rows = "".join(
                f'<tr><td><label><input type="checkbox" name="item" value="{i}"'
                f' aria-label="采用 {E(r["text"])}"> {E(r["text"])}</label></td>'
                f'<td>{r["slot"]:02} · {E(COPY_TYPES[r["kind"]][1])}</td>'
                f'<td>{E(r["source_quote"])}</td></tr>'
                for i, r in sorted(enumerate(items), key=lambda pair: pair[1]["slot"])
            )
            suggestions = f"""<form method="post" action="/sku/{E(sku_id)}/product-copy/apply" class="product-copy-result">
<input type="hidden" name="draft_id" value="{E(draft['id'])}">
<p class="mute">功能最多选三条，成分或材质最多五条，用法选齐三步，其它图位各选一条。</p>
<div class="table-scroll"><table><thead><tr><th>建议英文</th><th>图位</th><th>资料原句</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<button class="p"{locked}>采用勾选文案</button></form>"""
        elif draft.get("state") == "ready":
            suggestions = '<p class="mute">没有找到有来源且符合规则的短句，可补充商品资料后再提炼。</p>'
        elif draft.get("state") == "applied":
            slots = "、".join(f"{int(slot):02}" for slot in draft.get("applied_slots", []))
            suggestions = f'<p class="mute">已采用文案并更新图位 {E(slots)}。可在审核墙按图位重出。</p>'
        opened = " open" if draft.get("state") == "ready" else ""
        return f"""<details class="card product-copy" id="product-copy"{opened}><summary>商品资料与卖点</summary>
<form method="post" action="/sku/{E(sku_id)}/product-copy">
<label for="copy-source">粘贴产品说明、核心卖点、成分或材质、用法和规格</label>
<textarea id="copy-source" name="source" maxlength="6000" required
 placeholder="支持中英文。只填写可核实的商品资料。">{E(draft.get('source', ''))}</textarea>
<div class="row"><button class="p"{locked}>提炼卖点并分配图位</button></div>
</form>{suggestions}</details>"""

    def label_summary(sku_id: str, lab: dict) -> str:
        lines = "".join(f"<li>{E(ln['text'])} <span class=mute>{SIZE_CN.get(ln.get('size'), '')}</span></li>"
                        for ln in lab.get("lines", []))
        return f"""<div class="card"><h2>标签（已确认 {E(lab.get('confirmed_at', ''))}）</h2><div class="cols2">
<div><b>包装文字</b><ol>{lines}</ol></div><div>
<p><b>大字</b>：{E(' / '.join(lab.get('big_text', [])))}</p>
<p><b>品名</b>：{E(lab.get('product_name'))}　<b>容量</b>：{E(lab.get('capacity'))}　<b>件数</b>：{E(lab.get('pieces'))} {E(lab.get('pieces_text', ''))}</p>
<p><b>类目</b>：{E(lab.get('category'))}　<b>主成分</b>：{E(lab.get('key_ingredient'))}　<b>质地</b>：{E(lab.get('texture'))}</p>
<p><b>可用卖点</b>：{E(' / '.join(lab.get('allowed_claims', [])) or '未选择')}</p>
<p><b>成分或材质短名</b>：{E(' / '.join(ingredient_names(lab)))}</p>
<p><b>上图质地短句</b>：{E(lab.get('texture_caption'))}</p>
<p><b>上图场景短句</b>：{E(lab.get('scene_caption'))}</p>
<p><b>用法</b>：{E(' / '.join(lab.get('usage_steps', [])))}</p>
<a class="btn" href="/sku/{E(sku_id)}?edit=1">修改标签</a>
<form method="post" action="/sku/{E(sku_id)}/transcribe" style="display:inline"><button>重新识别</button></form>
</div></div></div>"""

    def label_form(sku_id: str, d: dict, from_label: bool) -> str:
        typos = set(d.get("suspected_typos", []))
        rows = []
        lines = list(d.get("lines", [])) + [{"text": "", "size": "small"}] * 4
        for i, ln in enumerate(lines):
            sim = ln.get("ocr_sim")
            yellow = (sim is not None and sim < 0.85) or ln.get("text") in typos or ln.get("hidden")
            note = []
            if ln.get("text") in typos:
                note.append("疑似错字：按原样保留，不要改正")
            if sim is not None and sim < 0.85:
                note.append(f"本地 OCR 读到的是「{ln.get('ocr_found', '')}」，请对照原图核对")
            if ln.get("hidden"):
                note.append("被遮挡，只抄了可见部分")
            sel = "".join(f'<option value="{k}" {"selected" if ln.get("size") == k else ""}>{v}</option>'
                          for k, v in SIZE_CN.items())
            rows.append(f'<tr class="{"yellow" if yellow else ""}"><td style="width:60%"><input type="text" name="t{i}" value="{E(ln.get("text"))}">'
                        f'<div class="mute">{E("；".join(note))}</div></td><td><select name="s{i}">{sel}</select></td>'
                        f'<td><label><input type="checkbox" name="big{i}" {"checked" if ln.get("size") == "big" else ""}> 大字</label></td></tr>')
        draft = core.store.label_draft(sku_id) or {}
        cands = list(dict.fromkeys(list(d.get("allowed_claims", [])) + list(draft.get("claim_candidates", []))))
        checked = set(d.get("allowed_claims", [])) if from_label else set()
        claims = "".join(f'<label style="display:block"><input type="checkbox" name="claim" value="{E(c)}" {"checked" if c in checked else ""}> {E(c)}</label>'
                         for c in cands)
        cat = "".join(f'<option {"selected" if d.get("category") == c else ""}>{c}</option>' for c in CATS)
        ocr_note = "" if d.get("ocr_available", True) else '<p class="mute">本地 OCR 未安装，没有第二路核对，请逐行对照原图。</p>'
        return f"""<div class="card label-confirmation"><h2>确认标签文字</h2>
<p>逐行对照原图。改的是<b>识别错误</b>；包装上本来的错字（比如 “Replenis h”）<b>要原样保留</b>。黄色行请重点看。删掉一行：把文字清空。</p>{ocr_note}
<form method="post" action="/sku/{E(sku_id)}/label"><input type="hidden" name="n" value="{len(lines)}">
<fieldset class="label-claims"><legend>选择要印在副图上的卖点</legend>
{claims or '<p class="mute">如有可核实的卖点，可在下方补充。</p>'}
<label>其他已核实卖点，每行一条 <textarea name="extra_claims"></textarea></label>
<p class="mute">只勾选包装或产品资料支持的卖点。未勾选的候选语不会上图。</p></fieldset>
<div class="label-form-body" style="max-height:420px;max-height:min(46vh,420px);overflow-y:auto" tabindex="0" role="region" aria-label="标签识别结果">
<details class="label-section" open><summary>包装文字 · {len(d.get('lines', []))} 行</summary>
<div class="label-lines-scroll" style="max-height:280px;max-height:min(30vh,280px);overflow-y:auto" tabindex="0" role="region" aria-label="包装文字逐行核对">
<table><thead><tr><th>文字（原样）</th><th>字号</th><th>验收大字</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>
<details class="label-section"><summary>商品信息、卖点与用法</summary>
<div class="cols2" style="margin-top:12px"><div>
<p>品名（原样）<input type="text" name="product_name" value="{E(d.get('product_name'))}"></p>
<p>容量（原样，可空；空则不出尺寸图）<input type="text" name="capacity" value="{E(d.get('capacity'))}"></p>
<p>件数 <input type="number" name="pieces" value="{E(d.get('pieces') or 1)}" min="1">
件数文案（可空，如 4 Sheets）<input type="text" name="pieces_text" value="{E(d.get('pieces_text', ''))}"></p>
<p>类目 <select name="category">{cat}</select></p>
<p>主成分 <input type="text" name="key_ingredient" value="{E(d.get('key_ingredient'))}"></p>
<p>成分或材质短名（每行一项，可用于成分副图）<textarea name="ingredient_names">{E(chr(10).join(d.get('ingredient_names') or []))}</textarea></p>
<p>质地描述（供画面参考）<input type="text" name="texture" value="{E(d.get('texture'))}"></p>
</div><div>
<p>质地副图文案（确认后上图，可空）<input type="text" name="texture_caption" value="{E(d.get('texture_caption'))}"></p>
<p>场景副图文案（确认后上图，可空）<input type="text" name="scene_caption" value="{E(d.get('scene_caption'))}"></p>
<p>用法（填 3 步已核实英文说明才出三联图，每行一步）<textarea name="usage_steps">{E(chr(10).join(d.get('usage_steps', [])))}</textarea></p>
</div></div></details></div><div class="label-form-actions"><button class="p">确认标签</button></div></form></div>"""

    def variants_block(sku_id: str, busy: bool, active: str = "") -> str:
        st = core.store
        vs = st.variants(sku_id)
        nxt = chr(ord(vs[-1]) + 1) if vs else "A"
        action = f'<form method="post" action="/sku/{E(sku_id)}/plan/{nxt}"><button class="{"" if vs else "p"}" {"disabled" if busy else ""}>{"新增变体 " + nxt if vs else "制定计划"}</button></form>'
        copy_tip = "" if (st.label(sku_id) or {}).get("allowed_claims") else (
            f'<p class="suite-meta">还没选副图卖点。<a href="/sku/{E(sku_id)}?edit=1">选择卖点</a></p>')
        if not vs:
            return f"""<div class="suite-empty"><h2>还没有套图计划</h2>
<p>包装标签已确认，系统会按套图规则自动制定计划并出图。</p>
{copy_tip}
<form method="post" action="/sku/{E(sku_id)}/suite/A"><button class="p" {'disabled' if busy else ''}>一键生成套图</button></form>
<details class="suite-details"><summary>先查看或调整计划</summary>{action}</details></div>"""
        tabs = "".join(f'<a {"aria-current=page" if v == active else ""} href="/sku/{E(sku_id)}?variant={E(v)}">变体 {E(v)}</a>' for v in vs)
        toolbar = f'<div class="suite-variants"><nav aria-label="套图变体">{tabs}</nav>{action}</div>'
        return toolbar + copy_tip + plan_card(sku_id, active, busy)

    def plan_card(sku_id: str, v: str, busy: bool) -> str:
        st = core.store
        plan = st.plan(sku_id, v)
        calls, usd = core.estimate(plan)
        style = plan.get("style", {})
        srows = "".join(f'<label>{lab}<input type="text" name="style_{k}" value="{E(style.get(k))}"></label>'
                        for k, lab in (("backdrop", "背景"), ("palette", "配色"), ("light", "光线"), ("props", "道具")))
        tpl_names = {int(t["id"]): t["name"] for t in core.tpl["slots"]["slots"]}
        sources = st.sources(sku_id)
        fallback = furl(sources[0]) if sources else ""
        slot_rows = []
        for sid in sorted(plan.get("slots", {}), key=int):
            s = plan["slots"][sid]
            slot_name = tpl_names.get(int(sid), s.get("name")) or f"图位 {sid}"
            fields = []
            if "headline" in s:
                fields.append(f'<label>标题<input type="text" name="h{sid}" value="{E(s["headline"])}"></label>')
            if "bullets" in s:
                fields.append(f'<label>要点（每行一个）<textarea name="b{sid}">{E(chr(10).join(s["bullets"]))}</textarea></label>')
            if "texture_caption" in s:
                fields.append(f'<label>质地短句<input type="text" name="tc{sid}" value="{E(s["texture_caption"])}"></label>')
            if "scene_caption" in s:
                fields.append(f'<label>场景短句<input type="text" name="scene_c{sid}" value="{E(s["scene_caption"])}"></label>')
            if "capacity" in s:
                fields.append(f'<label>容量文字<input type="text" name="c{sid}" value="{E(s["capacity"])}"></label>')
            if "steps" in s:
                for k, stp in enumerate(s["steps"]):
                    fields.append(f'<label>第 {k + 1} 步说明<input type="text" name="sc{sid}_{k}" value="{E(stp.get("caption"))}"></label>'
                                  f'<label>画面<input type="text" name="sa{sid}_{k}" value="{E(stp.get("action"))}"></label>')
            if "scene" in s:
                fields.append(f'<label>画面（不含文字）<input type="text" name="sn{sid}" value="{E(s["scene"])}"></label>')
            cur = st.current(sku_id, v, int(sid))
            preview = furl(cur["file"]) if cur else fallback
            preview_type = "结果" if cur else "原图参考"
            pic = (f'<a class="suite-shot" href="{E(preview)}" target="_blank" rel="noopener"><img src="{E(preview)}" alt="{E(slot_name)} · {preview_type}" loading="lazy"><small>{preview_type}</small></a>'
                   if preview else '<span class="suite-shot suite-shot-empty">无预览</span>')
            copy_title, copy_rows = panel_copy(s)
            copy_preview = " · ".join(([copy_title] if copy_title else []) + copy_rows)
            if s.get("steps"):
                copy_preview = " · ".join(step["caption"] for step in s["steps"] if step.get("caption"))
            copy_note = (f'<small class="suite-meta">{E(copy_preview)}</small>' if copy_preview else "")
            approved = st.review(sku_id, v).get(sid, {}).get("status") == "approved"
            status = "已跳过" if s.get("skip") else "已通过" if approved and cur else "待审核" if cur else "待出图"
            variant = "skip" if s.get("skip") else "ok" if approved and cur else "todo"
            note = f'<small>{E(s.get("skip_reason") or "未列入本次出图")}</small>' if s.get("skip") else ""
            editor = ('<details class="suite-details suite-row-edit"><summary>画面与文案设置</summary>' + "".join(fields) + '</details>') if fields else ""
            slot_rows.append(f'<div class="suite-plan-row"><div class="suite-slot">{pic}<div><b>{E(slot_name)}</b><small>图位 {E(sid)}{note}</small></div></div>'
                             f'<div class="suite-direction">{copy_note}{editor}</div>'
                             f'<div class="suite-col-state"><span class="suite-state {variant}">{status}</span></div></div>')
        current_issues = validate(plan, st.label(sku_id) or {}, core.tpl["banned"]["words"])
        issues = "".join(f'<li>{E(x)}</li>' for x in current_issues)
        done = sum(1 for s in plan_slots(plan) if st.attempts(sku_id, v, s))
        total = len(plan_slots(plan))
        form_id = "suite-save-" + E(v)
        generate_name = "一键生成套图" if not done else "补齐未出图位"
        result_extra = f" · 预计 ${usd:.2f}" if calls else ""
        return f"""<section class="suite-plan">
<header class="suite-plan-top"><div class="suite-plan-title"><h2>制作清单</h2><span>已出 {done} / {total} 张</span></div>
<div class="suite-actionbar">
<a class="btn" href="/review?sku={E(sku_id)}">去审核</a>
<form method="post" action="/sku/{E(sku_id)}/suite/{E(v)}"><button class="p" {'disabled' if busy or issues or not total or done >= total else ''}>{generate_name}</button></form>
</div></header>
<div class="suite-check"><span>{'文案有待修正' if issues else '文案校验已通过'}{E(result_extra)}</span><span>{'允许人脸' if plan.get('faces') else '不出现完整人脸'}</span></div>
{'<ul class="suite-issues">'+issues+'</ul>' if issues else ''}
<form id="{form_id}" method="post" action="/sku/{E(sku_id)}/plan/{E(v)}/save">
<div class="suite-plan-table"><div class="suite-table-head"><span>图位</span><span>画面与文案</span><span>状态</span></div>
{''.join(slot_rows)}</div>
<details class="suite-details suite-style"><summary>风格设置 <span class="suite-meta">背景 · 配色 · 光线 · 道具</span></summary><div class="suite-style-grid">{srows}</div></details>
</form>
<div class="suite-plan-foot">
<p class="suite-meta">出图使用已保存的计划。</p>
<div class="row">
<form method="post" action="/sku/{E(sku_id)}/plan/{E(v)}" onsubmit="return confirm('重新做计划会覆盖上面的修改，已出的图保留。继续？')"><button {'disabled' if busy else ''}>重新做计划</button></form>
<button type="submit" form="{form_id}">保存草稿</button>
</div></div></section>"""

    @app.post("/sku/{sku_id}/transcribe")
    def do_transcribe(sku_id: str):
        if not (core.cfg.get("demo_mode") or api_key_present()):
            return back("/settings#openai-api")
        try:
            jid = core.transcribe(sku_id)
        except Exception as e:  # noqa: BLE001
            return back(f"/sku/{sku_id}", safe_error(e))
        return back(f"/sku/{sku_id}?job={jid}", "正在识别标签，完成后页面会自动刷新")

    @app.post("/sku/{sku_id}/label")
    async def save_label(request: Request, sku_id: str):
        f = await request.form()
        if core.jobs.busy(sku_id):
            return back(f"/sku/{sku_id}?edit=1", "正在处理这个商品，请在当前任务结束后保存。")
        lines, big = [], []
        for i in range(int(f.get("n", 0))):
            t = str(f.get(f"t{i}", "")).strip()
            if not t:
                continue
            size = "big" if f.get(f"big{i}") else str(f.get(f"s{i}", "small"))
            lines.append({"text": t, "size": size})
            if size == "big":
                big.append(t)
        claims = [str(c).strip() for c in f.getlist("claim")]
        claims += [c.strip() for c in str(f.get("extra_claims", "")).splitlines() if c.strip()]
        ingredients = [c.strip() for c in str(f.get("ingredient_names", "")).splitlines() if c.strip()]
        steps = [c.strip() for c in str(f.get("usage_steps", "")).splitlines() if c.strip()][:3]
        data = {"lines": lines, "big_text": big, "allowed_claims": list(dict.fromkeys(claims)),
                "product_name": str(f.get("product_name", "")).strip(),
                "capacity": str(f.get("capacity", "")).strip(),
                "pieces": int(f.get("pieces") or 1), "pieces_text": str(f.get("pieces_text", "")).strip(),
                "category": str(f.get("category", "")), "key_ingredient": str(f.get("key_ingredient", "")).strip(),
                "ingredient_names": list(dict.fromkeys(ingredients)),
                "texture": str(f.get("texture", "")).strip(),
                "texture_caption": str(f.get("texture_caption", "")).strip(),
                "scene_caption": str(f.get("scene_caption", "")).strip(), "usage_steps": steps,
                "copy_sources": (core.store.label(sku_id) or {}).get("copy_sources", []),
                "suspected_typos": (core.store.label_draft(sku_id) or {}).get("suspected_typos", [])}
        problems = []
        for c in data["allowed_claims"]:
            from imggen.plan import check_text
            problems += check_text(c, data, core.tpl["banned"]["words"], "bullet")
        core.confirm_label(sku_id, data)
        msg = "标签已确认，可以一键生成套图。" + ("请留意：" + "；".join(problems[:4]) if problems else "")
        return back(f"/sku/{sku_id}", msg)

    @app.post("/sku/{sku_id}/product-copy")
    def analyze_product_copy(request: Request, sku_id: str, source: str = Form("")):
        origin = request.headers.get("origin")
        if origin and (origin != str(request.base_url).rstrip("/") or
                       request.url.hostname not in {"localhost", "127.0.0.1", "::1"}):
            return HTMLResponse("请在本机工作台发起分析。", status_code=403)
        if not (core.cfg.get("demo_mode") or api_key_present()):
            return back("/settings#openai-api")
        try:
            jid = core.analyze_product_copy(sku_id, source.strip())
        except (ValueError, RuntimeError) as e:
            return back(f"/sku/{sku_id}", safe_error(e))
        return back(f"/sku/{sku_id}?job={jid}", "正在提炼商品卖点和分配图位。")

    @app.post("/sku/{sku_id}/product-copy/apply")
    async def use_product_copy(request: Request, sku_id: str):
        f = await request.form()
        try:
            core.use_product_copy(sku_id, str(f.get("draft_id", "")), f.getlist("item"))
        except (ValueError, RuntimeError) as e:
            return back(f"/sku/{sku_id}", safe_error(e))
        return back(f"/sku/{sku_id}", "已采用勾选的文案，后续出图将自动使用。")

    @app.post("/sku/{sku_id}/plan/{v}")
    def do_plan(sku_id: str, v: str):
        try:
            jid = core.make_plan(sku_id, v)
        except Exception as e:  # noqa: BLE001
            return back(f"/sku/{sku_id}", str(e))
        return back(f"/sku/{sku_id}?variant={v}&job={jid}", f"正在制定变体 {v} 的套图计划。")

    @app.post("/sku/{sku_id}/suite/{v}")
    def do_generate_suite(request: Request, sku_id: str, v: str):
        origin = request.headers.get("origin")
        if origin and (origin != str(request.base_url).rstrip("/") or
                       request.url.hostname not in {"localhost", "127.0.0.1", "::1"}):
            return HTMLResponse("请在本机工作台发起出图。", status_code=403)
        if not (core.cfg.get("demo_mode") or api_key_present()):
            return back("/settings#openai-api")
        try:
            jid = core.generate_suite(sku_id, v)
        except (ValueError, RuntimeError) as e:
            return back(f"/sku/{sku_id}", safe_error(e))
        return back(f"/sku/{sku_id}?variant={v}&job={jid}", "正在按套图规则制作，完成后页面会自动刷新。")

    @app.post("/sku/{sku_id}/plan/{v}/save")
    async def save_plan(request: Request, sku_id: str, v: str):
        f = await request.form()
        if core.jobs.busy(sku_id):
            return back(f"/sku/{sku_id}", "正在处理这个商品，请在当前任务结束后保存。")
        plan = core.store.plan(sku_id, v)
        for k in ("backdrop", "palette", "light", "props"):
            plan.setdefault("style", {})[k] = str(f.get(f"style_{k}", "")).strip()
        for sid, s in plan["slots"].items():
            if f"h{sid}" in f:
                s["headline"] = str(f[f"h{sid}"]).strip()
            if f"b{sid}" in f:
                s["bullets"] = [x.strip() for x in str(f[f"b{sid}"]).splitlines() if x.strip()]
            if f"tc{sid}" in f:
                s["texture_caption"] = str(f[f"tc{sid}"]).strip()
            if f"scene_c{sid}" in f:
                s["scene_caption"] = str(f[f"scene_c{sid}"]).strip()
            if f"c{sid}" in f:
                s["capacity"] = str(f[f"c{sid}"]).strip()
                s["skip"] = not s["capacity"]
            if f"sn{sid}" in f:
                s["scene"] = str(f[f"sn{sid}"]).strip()
            for k, stp in enumerate(s.get("steps", [])):
                stp["caption"] = str(f.get(f"sc{sid}_{k}", stp.get("caption", ""))).strip()
                stp["action"] = str(f.get(f"sa{sid}_{k}", stp.get("action", ""))).strip()
        issues = core.save_plan(sku_id, v, plan)
        return back(f"/sku/{sku_id}", "已保存" + ("，但还有文案问题" if issues else ""))

    @app.post("/sku/{sku_id}/generate/{v}")
    def do_generate(sku_id: str, v: str):
        try:
            core.generate(sku_id, v)
        except Exception as e:  # noqa: BLE001
            return back(f"/sku/{sku_id}", str(e))
        return back(f"/sku/{sku_id}", "开始出图。每张约 30–60 秒，可以先去做别的 SKU")

    # ------------------------------------------------------------ F7 review wall
    def review_cell(sid: str, v: str, slot: int, rev: dict, tpl_names: dict) -> str:
        """One slot card on the review wall. Also served alone by /api/slot for in-place updates."""
        st = core.store
        cur = st.current(sid, v, slot)
        r = rev.get(str(slot), {})
        stt = r.get("status", "")
        redo = core.jobs.redo_for(sid, v, slot)
        redo_data = f' data-redo-id="{E(redo["id"])}"' if redo else ''
        if cur is None:
            busy = ('<div class="review-feedback mute" role="status">正在生成</div>'
                    '<div class="progress-track" role="progressbar" aria-label="生成进度"><span></span></div>') if redo else ''
            return (f'<div class="cell review-missing" data-sku="{E(sid)}" data-v="{v}" data-slot="{slot}" data-version=""{redo_data}>'
                    f'<span class="eyebrow">图位 {slot:02}</span><div class="t">{E(tpl_names.get(slot))}</div>'
                    f'<span class="tag warn">{"生成中" if redo else "待出图"}</span>{busy}</div>')
        qa = cur["qa"]
        flags = qa.get("flags", []) + ([qa["error"]] if qa.get("error") else [])
        cls = "approved" if stt == "approved" else "redo" if stt == "redo" else "warn" if flags else ""
        urls = [furl(st.sources(sid)[0]), furl(cur["gen"])] + ([furl(cur["pasted"])] if cur["pasted"] else [])
        caps = ["产品原图", "生成版", "标签回贴版"][:len(urls)]
        which = "回贴版" if cur["file"] == cur["pasted"] else "生成原图"
        status_tag = {"approved": '<span class="tag ok">已通过</span>', "redo": '<span class="tag bad">待重出</span>'}.get(stt, '<span class="tag warn">待审核</span>')
        compare = E("showBig(" + json.dumps(urls) + "," + json.dumps(caps, ensure_ascii=False) + ")")
        disabled = " disabled" if redo else ""
        tog = f'<button data-review-control{disabled} onclick="act(this,\'toggle\')" title="L">{"改用生成原图" if which == "回贴版" else "改用回贴版"}</button>' if cur["pasted"] else ""
        relayout = (f'<button data-review-control{disabled} onclick="act(this,\'relayout\')" title="按计划里的文案重新排版，不重新出图、不花钱">重新排版</button>'
                    if cur.get("layout") else "")
        return (
            f'<article class="cell {cls}" tabindex="0" data-sku="{E(sid)}" data-v="{v}" data-slot="{slot}" data-version="{E(cur["version"])}"{redo_data}>'
            f'<button type="button" class="review-photo" onclick="{compare}" aria-label="对照原图：{E(tpl_names.get(slot))}">'
            f'<img loading="lazy" src="{furl(cur["file"])}" alt=""></button>'
            f'<div class="row section-top"><div class="t">{slot:02} · {E(tpl_names.get(slot))}</div>{status_tag}</div>'
            f'<p class="mute">第 {cur["attempts"]} 版 · {which}</p>'
            + "".join(f'<div class="f">{E(x)}</div>' for x in flags) +
            f'<div class="row review-controls"><button data-review-control{disabled} onclick="act(this,\'approve\')" title="A">通过</button>'
            f'<button data-review-control{disabled} onclick="act(this,\'redo\')" title="R">重出</button>{tog}{relayout}</div>'
            '<form class="redo-editor" hidden><label>调整说明（选填）'
            '<textarea name="note" placeholder="例如：包装正面朝向镜头"></textarea></label>'
            '<div class="row"><button type="button" onclick="closeRedo(this)">取消</button>'
            '<button class="p">确认重出</button></div></form>'
            f'<div class="review-feedback mute" role="status">{"正在生成" if redo else ""}</div>'
            f'<div class="progress-track" role="progressbar" aria-label="重出进度" {"" if redo else "hidden"}><span></span></div></article>')

    def review_tally(sid: str, v: str) -> str:
        plan = core.store.plan(sid, v)
        rev = core.store.review(sid, v)
        slots = plan_slots(plan)
        ok = sum(1 for s in slots if rev.get(str(s), {}).get("status") == "approved")
        return f"通过 {ok} / {len(slots)}"

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, sku: str = "", show: str = "todo"):
        st = core.store
        blocks = []
        tpl_names = {int(t["id"]): t["name"] for t in core.tpl["slots"]["slots"]}
        skus = [sku] if sku else st.list_skus()
        for sid in skus:
            for v in st.variants(sid):
                plan = st.plan(sid, v)
                rev = st.review(sid, v)
                cells = []
                n_ok = 0
                for slot in plan_slots(plan):
                    if rev.get(str(slot), {}).get("status") == "approved":
                        n_ok += 1
                    cells.append(review_cell(sid, v, slot, rev, tpl_names))
                if show == "todo" and n_ok == len(plan_slots(plan)):
                    continue
                name = st.sku(sid).get("name", sid)
                blocks.append(f'<section class="review-group" data-review-group><header class="row review-group-head"><div class="row"><h2>{E(name)}</h2><span class="mute">变体 {v}</span>'
                              f'<span class="tag" data-review-count>通过 {n_ok} / {len(plan_slots(plan))}</span></div><div class="row">'
                              f'<button onclick="post(\'/api/review_all\',{{sku:\'{E(sid)}\',variant:\'{v}\'}}).then(()=>location.reload())">整套通过</button>'
                              f'<a class="btn" href="/sku/{E(sid)}">套图计划</a></div></header>'
                              f'<div class="grid">{"".join(cells)}</div></section>')
        choices = [("", "所有商品")] + [(sid, st.sku(sid).get("name", sid)) for sid in st.list_skus()]
        options = "".join(f'<option value="{E(sid)}" {"selected" if sid == sku else ""}>{E(name)}</option>' for sid,name in choices)
        suffix = '&amp;sku='+urllib.parse.quote(sku) if sku else ''
        body = f"""{flash(request)}<div data-review-wall class="row review-toolbar">
<form action="/review" method="get" class="row"><input type="hidden" name="show" value="{E(show)}"><select name="sku" aria-label="筛选商品" style="width:auto">{options}</select><button>筛选</button></form>
<div class="row"><a class="btn {'p' if show != 'all' else ''}" href="/review?show=todo{suffix}">待处理</a><a class="btn {'p' if show == 'all' else ''}" href="/review?show=all{suffix}">全部</a></div></div>
<p class="mute">点击图片对照原图。聚焦图卡后按 A 通过、R 重出、L 切换版本。</p>
{''.join(blocks) or '<div class="card empty">没有要审核的图。可回到套图计划开始出图。</div>'}"""
        action = '<button onclick="if(confirm(\'把所有无提示的图设为通过？\'))post(\'/api/review_clean\').then(()=>location.reload())">批量通过无提示图</button>' if blocks and not sku else ''
        return page("审核墙", body, actions=action)

    @app.post("/api/review")
    async def api_review(request: Request):
        d = await request.json()
        sid, v, slot, action = d["sku"], d["variant"], int(d["slot"]), d["action"]
        st = core.store
        if action in {"approve", "toggle", "relayout"} and core.jobs.redo_for(sid, v, slot):
            return JSONResponse({"error": "这个图位正在重出，请等新图完成后审核。"})
        if action == "approve":
            st.set_review(sid, v, slot, status="approved")
        elif action == "toggle":
            cur = st.current(sid, v, slot)
            pick = "gen" if cur and cur["file"] == cur["pasted"] else "pasted"
            st.set_review(sid, v, slot, pick=pick)
        elif action == "relayout":
            try:
                core.rerender(sid, v, slot)
            except ValueError as e:
                return JSONResponse({"error": str(e)})
        elif action == "redo":
            n = len(st.attempts(sid, v, slot))
            if n >= int(core.cfg.get("repair", {}).get("max_attempts_per_slot", 3)) + 3:
                return JSONResponse({"error": f"这个图位已经出了 {n} 版，建议改计划里的文案/画面描述再试"})
            try:
                jid = core.generate(sid, v, slots=[slot], notes={slot: d.get("note", "")})
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"error": safe_error(e)})
            return JSONResponse({"ok": True, "job_id": jid})
        return JSONResponse({"ok": True})

    @app.post("/api/review_all")
    async def api_review_all(request: Request):
        d = await request.json()
        plan = core.store.plan(d["sku"], d["variant"])
        for slot in plan_slots(plan):
            if (not core.jobs.redo_for(d["sku"], d["variant"], slot) and
                    core.store.current(d["sku"], d["variant"], slot)):
                core.store.set_review(d["sku"], d["variant"], slot, status="approved")
        return JSONResponse({"ok": True})

    @app.post("/api/review_clean")
    def api_review_clean():
        n = 0
        st = core.store
        for sid in st.list_skus():
            for v in st.variants(sid):
                rev = st.review(sid, v)
                for slot in plan_slots(st.plan(sid, v)):
                    cur = st.current(sid, v, slot)
                    if not core.jobs.redo_for(sid, v, slot) and cur and not cur["qa"].get("flags") and not cur["qa"].get("error") \
                            and rev.get(str(slot), {}).get("status") != "approved":
                        st.set_review(sid, v, slot, status="approved")
                        n += 1
        return JSONResponse({"n": n})

    @app.get("/api/jobs")
    def api_jobs(sku: str = ""):
        js = [j for j in core.jobs.recent() if not sku or j["sku"] == sku]
        return JSONResponse([{k: j.get(k) for k in ("id", "kind", "sku", "variant", "slot", "total", "done", "state", "error")}
                             for j in js])

    @app.get("/api/slot")
    def api_slot(sku: str, variant: str, slot: int):
        """One review card as HTML, for replacing it in place without reloading the page."""
        st = core.store
        if not st.sku(sku) or variant not in st.variants(sku):
            return JSONResponse({"error": "没有这个图位"}, status_code=404)
        tpl_names = {int(t["id"]): t["name"] for t in core.tpl["slots"]["slots"]}
        return JSONResponse({"html": review_cell(sku, variant, slot, st.review(sku, variant), tpl_names),
                             "tally": review_tally(sku, variant)})

    @app.get("/api/slot_versions")
    def api_slot_versions(sku: str):
        """Version key per slot, so the page can tell which cards changed."""
        st = core.store
        out = {}
        if st.sku(sku):
            for v in st.variants(sku):
                for slot in plan_slots(st.plan(sku, v)):
                    cur = st.current(sku, v, slot)
                    out[f"{v}:{slot}"] = cur["version"] if cur else ""
        return JSONResponse(out)

    # ------------------------------------------------------------ F8 export
    @app.get("/export", response_class=HTMLResponse)
    def export_page(request: Request):
        st = core.store
        rows = []
        for sid in st.list_skus():
            for v in st.variants(sid):
                plan = st.plan(sid, v)
                slots = plan_slots(plan)
                rev = st.review(sid, v)
                ok = sum(1 for s in slots if rev.get(str(s), {}).get("status") == "approved")
                rows.append(f'<tr><td><input type="checkbox" name="item" value="{E(sid)}|{v}" {"checked" if ok == len(slots) and slots else ""}></td>'
                            f'<td>{E(st.sku(sid).get("name"))}</td><td>{v}</td><td><span class="tag {"ok" if slots and ok == len(slots) else "warn"}">{ok} / {len(slots)}</span></td>'
                            f'<td>{E(st.sku(sid).get("exported", ""))}</td></tr>')
        exp = Path(core.cfg["workspace"]) / "exports"
        zips = sorted(exp.glob("*.zip"), reverse=True)[:10] if exp.exists() else []
        links = "".join(f'<li><a href="{furl(z)}">{E(z.name)}</a></li>' for z in zips)
        body = f"""{flash(request)}<div class="card flat"><h2>选择要打包的套图</h2>
<form method="post" action="/export"><div class="table-scroll"><table><tr><th></th><th>产品</th><th>变体</th><th>已通过</th><th>上次导出</th></tr>{''.join(rows) or '<tr><td colspan=5 class=empty>还没有可导出的套图。</td></tr>'}</table></div>
<p><label><input type="checkbox" name="all"> 没通过的图也导出（默认只导出已通过的）</label></p>
<p class="mute">输出：{{sku}}_{{变体}}_{{图位两位数}}.jpg（质量 92）、ERP 导入表 erp_import.xlsx、每套一张联系表，打包成 zip。
</p><button class="p" type="submit" {"disabled" if not rows else ""}>生成 ZIP</button></form></div>
<div class="card"><h2>最近导出</h2><ul>{links or '<li class=mute>还没有</li>'}</ul>
<p class="mute">文件夹位置：{E(exp)}</p></div>"""
        return page("导出", body, desc="默认只打包通过审核的图，包含 JPG、联系表和 ERP 导入表。")

    @app.post("/export")
    async def do_export(request: Request):
        from imggen.export import export
        f = await request.form()
        items = [tuple(x.split("|", 1)) for x in f.getlist("item")]
        if not items:
            return back("/export", "先勾选要导出的套图")
        with core.ledger.path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        z = export(core.store, items, rows, core.cfg, approved_only=not f.get("all"))
        return back("/export", f"已导出：{z.name}（在最近导出里点击下载）")

    # ------------------------------------------------------------ F9 settings
    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, models: int = 0):
        c = core.cfg
        g = c["generation"]
        opts = ""
        if models:
            try:
                from imggen.llm import list_text_models
                opts = "<p class=mute>账号可用的文本模型：" + E("、".join(list_text_models(core.client)[:30])) + "</p>"
            except Exception as e:  # noqa: BLE001
                opts = f"<p class=err>{E(safe_error(e))}</p>"
        tp = c.get("text_pricing_usd_per_million_tokens", {})
        pr = c["pricing_usd_per_million_tokens"]
        f = c.get("faces", {})
        api = ('<span class="tag ok">已配置</span>' if api_key_present() else '<span class="tag warn">未配置</span>')
        key_form = f"""<form method="post" action="/settings/openai-key">
<label class="field" for="openai-key">OpenAI API 密钥</label>
<div class="row api-key-row">
<input id="openai-key" name="key" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 API key" maxlength="512" required>
<input type="hidden" name="token" value="{E(key_form_token)}">
<button class="p">验证并保存</button></div>
<p class="mute">验证通过后保存到这台 Mac 的钥匙串，立即生效。</p></form>""" if keychain_available() else '<p class="mute">在本机环境变量中设置 OPENAI_API_KEY。</p>'
        mode_copy = "打开后使用演示结果，不调用模型。"
        model_link = '<a href="/settings?models=1">查看账号可用模型</a>'
        body = f"""{flash(request)}<section id="openai-api" class="card settings-section"><div class="row"><h2>生成服务</h2>{api}</div>
{key_form}</section><form method="post" action="/settings" class="settings-form"><div class="card settings-section"><h2>使用模式</h2>
<p><label><input type="checkbox" name="demo_mode" {'checked' if c.get('demo_mode') else ''}> 演示模式</label> <span class="mute">{mode_copy}</span></p></div>
<div class="cols2"><section class="card settings-section"><h2>出图与预算</h2>
<p>每日预算（美元）<input type="number" step="0.5" name="budget" value="{E(c['budget_usd_per_day'])}"></p>
<p>并发（同时出几张）<input type="number" name="concurrency" value="{E(c.get('concurrency', 6))}"></p>
<p>画质 <select name="quality">{''.join(f'<option {"selected" if g["quality"] == q else ""}>{q}</option>' for q in ("low", "medium", "high"))}</select></p>
</section><section class="card settings-section"><h2>场景规则</h2>
<p><label><input type="checkbox" name="faces_all" {'checked' if f.get('allow_full_face') else ''}> 所有类目都允许出现完整人脸</label></p>
<p>仅以下类目允许人脸（逗号分隔）<input type="text" name="faces_cats" value="{E(','.join(f.get('categories') or []))}">
<span class="mute">人脸仅用于场景图，首图以产品为主。</span></p>
</section></div>
<details class="card settings-section" {"open" if models else ""}><summary>高级设置</summary>
<div class="cols2"><div>
<p>出图模型 <input type="text" name="model" value="{E(g['model'])}"></p>
<p><label><input type="checkbox" name="route" {'checked' if g.get('route_label_heavy') else ''}> 标签小字多（≥6 行）或有疑似错字时改用</label>
<input type="text" name="heavy_model" value="{E(g['label_heavy_model'])}"><span class=mute>（第二轮实测：默认 flare 即可，sunburst 只在修复时用，所以默认关闭）</span></p>
<p>标签识别与计划模型<input type="text" name="text_model" value="{E(c.get('text_model', 'auto'))}"> {model_link}</p>{opts}
</div><div>
<p>单价（美元/百万 token）：出图 文字入 <input type="number" step="0.01" name="p_ti" value="{pr['text_input']}">
图片入 <input type="number" step="0.01" name="p_ii" value="{pr['image_input']}"> 输出 <input type="number" step="0.01" name="p_io" value="{pr['image_output']}"></p>
<p>文本模型 输入 <input type="number" step="0.01" name="t_in" value="{tp.get('input', 0)}"> 输出 <input type="number" step="0.01" name="t_out" value="{tp.get('output', 0)}">
<span class=mute>（0 = 不计入预算；按 OpenAI 价格页填）</span></p>
</div></div></details><button class="p">保存设置</button></form>
<section class="card settings-section" style="margin-top:18px"><h2>副图文案规则</h2>
<p>卖点 02、成分或材质 03、质地 04、用法 05、场景 06、规格 07 按商品资料分配文案。系统使用勾选采用的英文短句排版。</p>
<p>到商品页「商品资料与卖点」粘贴资料。每条建议附有原文依据和对应图位，用法和容量缺资料时会跳过。</p></section>"""
        return page("设置", body)

    @app.post("/settings/openai-key")
    def update_openai_key(request: Request, key: str = Form(""), token: str = Form("")):
        if (request.url.hostname not in {"localhost", "127.0.0.1", "::1"} or
                request.headers.get("origin") != str(request.base_url).rstrip("/") or
                not secrets.compare_digest(token.encode(), key_form_token.encode())):
            return HTMLResponse("请在本机设置页面提交。", status_code=403)
        if not keychain_available():
            return back("/settings", "这台设备无法使用 Mac 钥匙串。")
        if not key_save_lock.acquire(blocking=False):
            return back("/settings", "正在验证密钥，请稍等。")
        try:
            key = key.strip()
            validate_api_key(key)
            if any(j["state"] == "running" for j in core.jobs.recent()):
                return back("/settings", "有任务正在运行，结束后再保存密钥。")
            save_keychain_key(key)
            os.environ["OPENAI_API_KEY"] = key
            core.reset_client()
        except ValueError as e:
            return back("/settings", safe_error(e))
        finally:
            key_save_lock.release()
        return back("/settings", "API 密钥已验证并保存，可以开始识别商品标签。")

    @app.post("/settings")
    async def save_settings(request: Request):
        f = await request.form()
        p = ROOT / "config.yaml"
        cur = yaml.safe_load((p if p.exists() else ROOT / "config.example.yaml").read_text(encoding="utf-8"))
        num = lambda k, d: float(f.get(k) or d)  # noqa: E731
        cur["demo_mode"] = bool(f.get("demo_mode"))
        cur["budget_usd_per_day"] = num("budget", 10)
        cur["concurrency"] = int(num("concurrency", 6))
        g = cur.setdefault("generation", {})
        g["model"] = str(f.get("model") or g.get("model")).strip()
        g["quality"] = str(f.get("quality") or "medium")
        g["route_label_heavy"] = bool(f.get("route"))
        g["label_heavy_model"] = str(f.get("heavy_model") or g.get("label_heavy_model")).strip()
        cur["text_model"] = str(f.get("text_model") or "auto").strip()
        cur["faces"] = {"allow_full_face": bool(f.get("faces_all")),
                        "categories": [x.strip() for x in str(f.get("faces_cats", "")).split(",") if x.strip()]}
        cur["pricing_usd_per_million_tokens"] = {"text_input": num("p_ti", 5), "image_input": num("p_ii", 8),
                                                 "image_output": num("p_io", 30)}
        cur["text_pricing_usd_per_million_tokens"] = {"input": num("t_in", 0), "output": num("t_out", 0)}
        p.write_text(yaml.safe_dump(cur, allow_unicode=True, sort_keys=False), encoding="utf-8")
        core.reload(load_config())
        return back("/settings", "已保存")

    # ------------------------------------------------------------ ledger
    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page():
        with core.ledger.path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        today = date.today().isoformat()
        tr = "".join("<tr>" + "".join(f"<td>{E(r.get(k))}</td>" for k in
                     ("time", "sku_id", "variant", "slot", "attempt", "quality", "cost_usd", "latency_s", "status")) + "</tr>"
                     for r in reversed(rows[-200:]))
        n_today = sum(1 for r in rows if r["time"].startswith(today) and r["status"] == "ok")
        body = f"""<div class="card"><h2>最近 200 条</h2><p>今日用量 {n_today} 次 · 花费 ${spent():.3f}</p>
<div class="table-scroll"><table><tr><th>时间</th><th>SKU</th><th>变体</th><th>图位</th><th>次</th><th>画质</th><th>费用$</th><th>秒</th><th>结果</th></tr>{tr or "<tr><td colspan=9 class=empty>暂无记录。</td></tr>"}</table></div></div>"""
        return page("账本", body)

    app.state.core = core
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    stored_key = load_keychain_key()
    if stored_key:
        os.environ["OPENAI_API_KEY"] = stored_key
    url = f"http://127.0.0.1:{a.port}"
    print("打开", url, "（关闭：在这个终端按 Ctrl+C）")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    uvicorn.run(create_app(), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
