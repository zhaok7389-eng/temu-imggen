"""Local web tool. Run:  python app.py   then the browser opens http://127.0.0.1:8765

Only listens on this computer. The OpenAI key is read from the OPENAI_API_KEY environment
variable and is never shown, stored or logged.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import urllib.parse
import webbrowser
from datetime import date
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from imggen.config import ROOT, api_key_present, load_config
from imggen.pipeline import App
from imggen.store import plan_slots

E = lambda s: html.escape(str(s if s is not None else ""))  # noqa: E731
CATS = ["serum", "cream", "mask", "cleanser", "toner", "lip", "eye", "body", "other"]
SIZE_CN = {"big": "大", "medium": "中", "small": "小"}

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#222;--mute:#777;--line:#e4e1db;--ok:#2e9b6a;--warn:#d99a1e;--bad:#d2493f;--acc:#2f6fd6}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
header{background:#1f2328;color:#fff;padding:10px 20px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header a{color:#ddd;text-decoration:none}header a:hover{color:#fff}header b{color:#fff;margin-right:8px}
header .sp{margin-left:auto;font-size:13px;color:#bbb}
main{padding:20px;max-width:1400px;margin:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 16px}
h2{margin:0 0 12px;font-size:18px}h3{margin:16px 0 8px;font-size:16px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
input[type=text],input[type=number],select,textarea{font:inherit;padding:5px 7px;border:1px solid #ccc;border-radius:6px;width:100%}
textarea{min-height:70px}
button,.btn{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid #bbb;background:#fff;cursor:pointer;text-decoration:none;color:var(--ink);display:inline-block}
button.p,.btn.p{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.5}
.mute{color:var(--mute);font-size:13px}.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#eee}
.tag.ok{background:#dff3e8;color:#17663f}.tag.warn{background:#fdf0d6;color:#8a5a00}.tag.bad{background:#fbe0dd;color:#9b2a22}
.yellow{background:#fff6d6}.err{color:var(--bad)}.okc{color:var(--ok)}
.thumbs img{height:110px;border-radius:6px;border:1px solid var(--line);margin:0 6px 6px 0;background:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.cell{border:2px solid var(--line);border-radius:8px;padding:6px;background:#fff;outline:none}
.cell:focus{border-color:var(--acc)}.cell.approved{border-color:var(--ok)}.cell.warn{border-color:var(--warn)}.cell.redo{border-color:var(--bad)}
.cell img{width:100%;border-radius:4px;cursor:zoom-in;display:block}
.cell .t{font-size:13px;margin:4px 0}.cell .f{font-size:12px;color:#8a5a00}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;z-index:9;overflow:auto;padding:20px}
#modal .imgs{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}#modal img{max-height:88vh;max-width:32vw;background:#fff}
#modal .cap{color:#fff;text-align:center;margin-bottom:8px}
#jobs .j{font-size:13px;margin:2px 0}
@media (max-width:700px){.cols2{grid-template-columns:1fr}#modal img{max-width:92vw}}
"""

JS = """
function post(url, data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})}).then(r=>r.json())}
function pollJobs(sku){
  const box=document.getElementById('jobs'); if(!box) return;
  let wasRunning=false;
  const tick=()=>fetch('/api/jobs'+(sku?'?sku='+encodeURIComponent(sku):'')).then(r=>r.json()).then(js=>{
    const run=js.filter(j=>j.state==='running');
    box.innerHTML=js.slice(0,6).map(j=>`<div class="j">${j.kind} ${j.variant||''} · ${j.done}/${j.total} · ${j.state==='running'?'进行中…':j.state==='done'?'<span class=okc>完成</span>':'<span class=err>'+j.error+'</span>'}</div>`).join('')||'<span class=mute>没有进行中的任务</span>';
    if(wasRunning && run.length===0){location.reload()}
    wasRunning = run.length>0;
    setTimeout(tick, run.length?2000:6000);
  });
  tick();
}
function showBig(urls, caps){
  const m=document.getElementById('modal');
  m.innerHTML='<div class="cap">点任意处关闭</div><div class="imgs">'+urls.map((u,i)=>`<div><div class="cap">${caps[i]}</div><img src="${u}"></div>`).join('')+'</div>';
  m.style.display='block'; m.onclick=()=>m.style.display='none';
}
function act(el, action){
  const d=el.closest('.cell').dataset;
  let note='';
  if(action==='redo'){note=prompt('重出：这张哪里不行？（会写进提示词，可留空）','');if(note===null)return}
  post('/api/review',{sku:d.sku,variant:d.v,slot:+d.slot,action:action,note:note}).then(r=>{
    if(r.error){alert(r.error);return}
    location.reload();
  });
}
document.addEventListener('keydown',e=>{
  const c=document.activeElement; if(!c||!c.classList||!c.classList.contains('cell'))return;
  const k=e.key.toLowerCase();
  if(k==='a')act(c,'approve'); if(k==='r')act(c,'redo'); if(k==='l')act(c,'toggle');
});
"""


def create_app(cfg: dict | None = None) -> FastAPI:
    core = App(cfg)
    app = FastAPI(title="temu-imggen")
    ws = Path(core.cfg["workspace"]).resolve()

    def furl(p: Path | None) -> str:
        if not p:
            return ""
        rel = Path(p).resolve().relative_to(ws)
        v = int(Path(p).stat().st_mtime) if Path(p).exists() else 0
        return "/files/" + urllib.parse.quote(str(rel)) + f"?v={v}"

    def spent() -> float:
        return core.ledger.spent_today()

    def page(title: str, body: str, sku: str | None = None) -> HTMLResponse:
        c = core.cfg
        mode = ('<span class="tag warn">演示模式：不调用 OpenAI，不花钱</span>' if c.get("demo_mode") else
                ('<span class="tag ok">OpenAI key 已设置</span>' if api_key_present()
                 else '<span class="tag bad">没有 OPENAI_API_KEY</span>'))
        poll = f"<script>pollJobs({json.dumps(sku or '')})</script>"
        return HTMLResponse(f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{E(title)} · 主图工具</title>
<style>{CSS}</style><script>{JS}</script>
<header><b>Temu 主图工具</b><a href="/">产品库</a><a href="/review">审核墙</a><a href="/export">导出</a>
<a href="/settings">设置</a><a href="/ledger">账本</a>
<span class="sp">{mode} 今日已花 ${spent():.3f} / ${float(c['budget_usd_per_day']):.0f}</span></header>
<main><div class="card"><div id="jobs"></div></div>{body}</main><div id="modal"></div>{poll}</html>""")

    def back(url: str, msg: str = "") -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + urllib.parse.quote(msg)
        return RedirectResponse(url, status_code=303)

    def flash(request: Request) -> str:
        m = request.query_params.get("msg")
        return f'<div class="card yellow">{E(m)}</div>' if m else ""

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
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, status: str = ""):
        st = core.store
        rows = []
        counts: dict[str, int] = {}
        for sid in st.list_skus():
            s = st.sku(sid)
            stat = st.status(sid, core.jobs.busy(sid))
            counts[stat] = counts.get(stat, 0) + 1
            if status and stat != status:
                continue
            srcs = st.sources(sid)
            thumb = f'<img src="{furl(srcs[0])}" style="height:60px">' if srcs else ""
            cls = {"已导出": "ok", "待审核": "ok", "有问题": "bad", "生成中": "warn", "待确认": "warn"}.get(stat, "")
            rows.append(f'<tr><td>{thumb}</td><td><a href="/sku/{E(sid)}"><b>{E(s.get("name"))}</b></a>'
                        f'<div class="mute">{E(sid)}</div></td><td><span class="tag {cls}">{E(stat)}</span></td>'
                        f'<td>{E(s.get("category", ""))}</td><td>${sku_cost(sid):.3f}</td>'
                        f'<td><a class="btn" href="/sku/{E(sid)}">打开</a></td></tr>')
        filt = " ".join(f'<a class="btn" href="/?status={urllib.parse.quote(k)}">{E(k)} {v}</a>'
                        for k, v in counts.items())
        body = f"""{flash(request)}
<div class="card"><h2>导入产品</h2><div class="cols2">
<form method="post" action="/sku" enctype="multipart/form-data">
<h3>上传一个 SKU</h3><p><input type="text" name="name" placeholder="产品名，如 01 姜黄精华" required></p>
<p><input type="file" name="files" multiple accept="image/*" required></p>
<p class="mute">一个 SKU 可以放多张图（不同角度/颜色），第一张作为主图。原图只读保存，不会被改动。</p>
<button class="p">上传</button></form>
<form method="post" action="/import"><h3>导入整个文件夹</h3>
<p><input type="text" name="folder" placeholder="/Users/你的用户名/.../产品图片" required></p>
<p class="mute">文件夹里每个子文件夹 = 一个 SKU（在访达里右键文件夹 → 按住 Option →「拷贝为路径名称」，粘贴到这里）。</p>
<button class="p">导入</button></form></div></div>
<div class="card"><h2>产品库</h2><div class="row" style="margin-bottom:10px"><a class="btn" href="/">全部</a> {filt}</div>
<table><tr><th></th><th>产品</th><th>状态</th><th>类目</th><th>已花费用</th><th></th></tr>{''.join(rows) or '<tr><td colspan=6 class=mute>还没有产品，先在上面导入。</td></tr>'}</table></div>"""
        return page("产品库", body)

    @app.post("/sku")
    async def new_sku(name: str = Form(...), files: list[UploadFile] = File(...)):
        data = [(f.filename or "x.jpg", await f.read()) for f in files]
        try:
            sid = core.store.create_sku(name, data)
        except ValueError as e:
            return back("/", str(e))
        return back(f"/sku/{sid}", "已导入，下一步：识别标签")

    @app.post("/import")
    def import_folder(folder: str = Form(...)):
        try:
            made = core.store.import_folder(folder.strip().strip("'\""))
        except ValueError as e:
            return back("/", str(e))
        return back("/", f"导入了 {len(made)} 个 SKU：" + "、".join(made))

    # ------------------------------------------------------------ SKU page
    @app.get("/sku/{sku_id}", response_class=HTMLResponse)
    def sku_page(request: Request, sku_id: str, edit: int = 0):
        st = core.store
        s = st.sku(sku_id)
        if not s:
            return back("/", "没有这个 SKU")
        srcs = "".join(f'<img src="{furl(p)}">' for p in st.sources(sku_id))
        busy = core.jobs.busy(sku_id)
        label, draft = st.label(sku_id), st.label_draft(sku_id)
        parts = [flash(request), f'<div class="card"><h2>{E(s.get("name"))} <span class="tag">{E(st.status(sku_id, busy))}</span></h2>'
                 f'<div class="thumbs">{srcs}</div><div class="mute">已花费用 ${sku_cost(sku_id):.3f}</div></div>']
        # ---- label
        if label and not edit:
            parts.append(label_summary(sku_id, label))
        elif draft or (label and edit):
            parts.append(label_form(sku_id, label if (label and edit) else draft, from_label=bool(label and edit)))
        else:
            parts.append(f"""<div class="card"><h2>第 1 步 · 识别标签</h2>
<p>用 OpenAI 视觉模型逐行抄写包装上的文字（含原有错字），本地 OCR 做第二路核对。约 $0.01。</p>
<form method="post" action="/sku/{E(sku_id)}/transcribe"><button class="p" {'disabled' if busy else ''}>识别标签</button></form></div>""")
        # ---- variants
        if label and not edit:
            parts.append(variants_block(sku_id, busy))
        return page(s.get("name", sku_id), "".join(parts), sku_id)

    def label_summary(sku_id: str, lab: dict) -> str:
        lines = "".join(f"<li>{E(ln['text'])} <span class=mute>{SIZE_CN.get(ln.get('size'), '')}</span></li>"
                        for ln in lab.get("lines", []))
        return f"""<div class="card"><h2>标签（已确认 {E(lab.get('confirmed_at', ''))}）</h2><div class="cols2">
<div><b>包装文字</b><ol>{lines}</ol></div><div>
<p><b>大字</b>：{E(' / '.join(lab.get('big_text', [])))}</p>
<p><b>品名</b>：{E(lab.get('product_name'))}　<b>容量</b>：{E(lab.get('capacity'))}　<b>件数</b>：{E(lab.get('pieces'))} {E(lab.get('pieces_text', ''))}</p>
<p><b>类目</b>：{E(lab.get('category'))}　<b>主成分</b>：{E(lab.get('key_ingredient'))}　<b>质地</b>：{E(lab.get('texture'))}</p>
<p><b>可用卖点</b>：{E(' / '.join(lab.get('allowed_claims', [])))}</p>
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
        cands = list(dict.fromkeys(list(d.get("allowed_claims", [])) + list(d.get("claim_candidates", []))))
        checked = set(d.get("allowed_claims", [])) if from_label else set()
        claims = "".join(f'<label style="display:block"><input type="checkbox" name="claim" value="{E(c)}" {"checked" if c in checked else ""}> {E(c)}</label>'
                         for c in cands)
        cat = "".join(f'<option {"selected" if d.get("category") == c else ""}>{c}</option>' for c in CATS)
        ocr_note = "" if d.get("ocr_available", True) else '<p class="mute">本地 OCR 未安装，没有第二路核对，请逐行对照原图。</p>'
        return f"""<div class="card"><h2>第 2 步 · 确认标签文字</h2>
<p>逐行对照原图。改的是<b>识别错误</b>；包装上本来的错字（比如 “Replenis h”）<b>要原样保留</b>。黄色行请重点看。删掉一行：把文字清空。</p>{ocr_note}
<form method="post" action="/sku/{E(sku_id)}/label"><input type="hidden" name="n" value="{len(lines)}">
<table><tr><th>文字（原样）</th><th>字号</th><th>验收大字</th></tr>{''.join(rows)}</table>
<div class="cols2" style="margin-top:12px"><div>
<p>品名（原样）<input type="text" name="product_name" value="{E(d.get('product_name'))}"></p>
<p>容量（原样，可空；空则不出尺寸图）<input type="text" name="capacity" value="{E(d.get('capacity'))}"></p>
<p>件数 <input type="number" name="pieces" value="{E(d.get('pieces') or 1)}" min="1">
件数文案（可空，如 4 Sheets）<input type="text" name="pieces_text" value="{E(d.get('pieces_text', ''))}"></p>
<p>类目 <select name="category">{cat}</select></p>
<p>主成分 <input type="text" name="key_ingredient" value="{E(d.get('key_ingredient'))}"></p>
<p>质地描述（给模型画质地图用，不会印在图上）<input type="text" name="texture" value="{E(d.get('texture'))}"></p>
</div><div>
<p><b>勾选可以用作卖点文案的说法</b>（图上新加的英文只能来自这里、品名、容量、件数、用法）</p>{claims}
<p>其他卖点（每行一个，必须是包装上写了的意思；不能有数字、百分比、医疗说法）<textarea name="extra_claims"></textarea></p>
<p>用法步骤（每行一个，最多 3 行，会印在使用方法图上；可空）<textarea name="usage_steps">{E(chr(10).join(d.get('usage_steps', [])))}</textarea></p>
</div></div><button class="p">确认标签</button></form></div>"""

    def variants_block(sku_id: str, busy: bool) -> str:
        st = core.store
        vs = st.variants(sku_id)
        nxt = chr(ord(vs[-1]) + 1) if vs else "A"
        out = [f"""<div class="card"><h2>第 3 步 · 套图计划与出图</h2>
<p class="mute">一个变体 = 一套 8 张。同一 SKU 铺不同店铺时加变体，系统会换风格、避开已用过的风格。</p>
<form method="post" action="/sku/{E(sku_id)}/plan/{nxt}"><button class="p" {'disabled' if busy else ''}>{'做计划（变体 A）' if not vs else f'新增变体 {nxt}'}</button></form></div>"""]
        for v in vs:
            out.append(plan_card(sku_id, v, busy))
        return "".join(out)

    def plan_card(sku_id: str, v: str, busy: bool) -> str:
        st = core.store
        plan = st.plan(sku_id, v)
        calls, usd = core.estimate(plan)
        style = plan.get("style", {})
        srows = "".join(f'<p>{lab}<input type="text" name="style_{k}" value="{E(style.get(k))}"></p>'
                        for k, lab in (("backdrop", "背景"), ("palette", "配色"), ("light", "光线"), ("props", "道具")))
        slot_rows = []
        tpl_names = {int(t["id"]): t["name"] for t in core.tpl["slots"]["slots"]}
        for sid in sorted(plan["slots"], key=int):
            s = plan["slots"][sid]
            f = []
            if "headline" in s:
                f.append(f'标题 <input type="text" name="h{sid}" value="{E(s["headline"])}">')
            if "bullets" in s:
                f.append(f'要点（每行一个）<textarea name="b{sid}">{E(chr(10).join(s["bullets"]))}</textarea>')
            if "capacity" in s:
                f.append(f'容量文字 <input type="text" name="c{sid}" value="{E(s["capacity"])}">')
            if "steps" in s:
                for k, stp in enumerate(s["steps"]):
                    f.append(f'第{k + 1}步说明 <input type="text" name="sc{sid}_{k}" value="{E(stp.get("caption"))}">'
                             f'<span class=mute>画面：</span><input type="text" name="sa{sid}_{k}" value="{E(stp.get("action"))}">')
            if "scene" in s:
                f.append(f'画面（不含文字）<input type="text" name="sn{sid}" value="{E(s["scene"])}">')
            cur = st.current(sku_id, v, int(sid))
            img = f'<img src="{furl(cur["file"])}" style="width:120px;border-radius:6px">' if cur else ""
            skip = f'<div class="mute">{E(s.get("skip_reason", "跳过"))}</div>' if s.get("skip") else ""
            slot_rows.append(f'<tr><td><b>{sid}</b> {E(tpl_names.get(int(sid), s.get("name")))}{skip}</td><td>{"".join(f"<div>{x}</div>" for x in f)}</td><td>{img}</td></tr>')
        issues = "".join(f'<li class="err">{E(x)}</li>' for x in plan.get("issues", []))
        done = sum(1 for s in plan_slots(plan) if st.attempts(sku_id, v, s))
        total = len(plan_slots(plan))
        return f"""<div class="card"><h2>变体 {v}</h2>
<form method="post" action="/sku/{E(sku_id)}/plan/{v}/save"><div class="cols2"><div><h3>风格锁</h3>{srows}</div>
<div><h3>状态</h3><p>已出 {done}/{total} 个图位　预计 {calls} 次调用 ≈ ${usd}（按 ${core.cfg.get('est_usd_per_image', 0.026)}/张）</p>
<p>人脸：{'允许' if plan.get('faces') else '不出现完整人脸'}</p>{'<ul>' + issues + '</ul>' if issues else '<p class="okc">文案校验通过</p>'}</div></div>
<h3>图位</h3><table>{''.join(slot_rows)}</table><p><button>保存修改</button></p></form>
<div class="row"><form method="post" action="/sku/{E(sku_id)}/generate/{v}"><button class="p" {'disabled' if busy or plan.get('issues') else ''}>{'开始出图' if done == 0 else '补齐没出的图位'}</button></form>
<form method="post" action="/sku/{E(sku_id)}/plan/{v}" onsubmit="return confirm('重新做计划会覆盖上面的修改，已出的图保留。继续？')"><button {'disabled' if busy else ''}>重新做计划</button></form>
<a class="btn" href="/review?sku={E(sku_id)}">去审核</a></div></div>"""

    @app.post("/sku/{sku_id}/transcribe")
    def do_transcribe(sku_id: str):
        try:
            core.transcribe(sku_id)
        except Exception as e:  # noqa: BLE001
            return back(f"/sku/{sku_id}", str(e))
        return back(f"/sku/{sku_id}", "正在识别标签，完成后页面会自动刷新")

    @app.post("/sku/{sku_id}/label")
    async def save_label(request: Request, sku_id: str):
        f = await request.form()
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
        steps = [c.strip() for c in str(f.get("usage_steps", "")).splitlines() if c.strip()][:3]
        data = {"lines": lines, "big_text": big, "allowed_claims": list(dict.fromkeys(claims)),
                "product_name": str(f.get("product_name", "")).strip(),
                "capacity": str(f.get("capacity", "")).strip(),
                "pieces": int(f.get("pieces") or 1), "pieces_text": str(f.get("pieces_text", "")).strip(),
                "category": str(f.get("category", "")), "key_ingredient": str(f.get("key_ingredient", "")).strip(),
                "texture": str(f.get("texture", "")).strip(), "usage_steps": steps,
                "suspected_typos": (core.store.label_draft(sku_id) or {}).get("suspected_typos", [])}
        problems = []
        for c in data["allowed_claims"]:
            from imggen.plan import check_text
            problems += check_text(c, data, core.tpl["banned"]["words"], "bullet")
        core.confirm_label(sku_id, data)
        msg = "标签已确认。下一步：做计划" + ("。注意：" + "；".join(problems[:4]) if problems else "")
        return back(f"/sku/{sku_id}", msg)

    @app.post("/sku/{sku_id}/plan/{v}")
    def do_plan(sku_id: str, v: str):
        try:
            core.make_plan(sku_id, v)
        except Exception as e:  # noqa: BLE001
            return back(f"/sku/{sku_id}", str(e))
        return back(f"/sku/{sku_id}", f"正在做变体 {v} 的计划…")

    @app.post("/sku/{sku_id}/plan/{v}/save")
    async def save_plan(request: Request, sku_id: str, v: str):
        f = await request.form()
        plan = core.store.plan(sku_id, v)
        for k in ("backdrop", "palette", "light", "props"):
            plan.setdefault("style", {})[k] = str(f.get(f"style_{k}", "")).strip()
        for sid, s in plan["slots"].items():
            if f"h{sid}" in f:
                s["headline"] = str(f[f"h{sid}"]).strip()
            if f"b{sid}" in f:
                s["bullets"] = [x.strip() for x in str(f[f"b{sid}"]).splitlines() if x.strip()]
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
                    cur = st.current(sid, v, slot)
                    r = rev.get(str(slot), {})
                    stt = r.get("status", "")
                    if stt == "approved":
                        n_ok += 1
                    if cur is None:
                        cells.append(f'<div class="cell"><div class="t">{slot} {E(tpl_names.get(slot))}</div><div class="mute">还没出图</div></div>')
                        continue
                    qa = cur["qa"]
                    flags = qa.get("flags", []) + ([qa["error"]] if qa.get("error") else [])
                    cls = "approved" if stt == "approved" else "redo" if stt == "redo" else "warn" if flags else ""
                    urls = [furl(st.sources(sid)[0]), furl(cur["gen"])] + ([furl(cur["pasted"])] if cur["pasted"] else [])
                    caps = ["产品原图", "AI 生成图", "贴回原标签后"][:len(urls)]
                    which = "回贴版" if cur["file"] == cur["pasted"] else "生成原图"
                    tog = f'<button onclick="act(this,\'toggle\')" title="L">{"改用生成原图" if which == "回贴版" else "改用回贴版"}</button>' if cur["pasted"] else ""
                    status_tag = {"approved": '<span class="tag ok">已通过</span>', "redo": '<span class="tag bad">重出中</span>'}.get(stt, "")
                    cells.append(
                        f'<div class="cell {cls}" tabindex="0" data-sku="{E(sid)}" data-v="{v}" data-slot="{slot}">'
                        f'<img src="{furl(cur["file"])}" onclick=\'showBig({json.dumps(urls)},{json.dumps(caps, ensure_ascii=False)})\'>'
                        f'<div class="t">{slot} {E(tpl_names.get(slot))} · 第{cur["attempts"]}版 · {which} {status_tag}</div>'
                        + "".join(f'<div class="f">⚠ {E(x)}</div>' for x in flags) +
                        f'<div class="row" style="margin-top:4px"><button onclick="act(this,\'approve\')" title="A">通过</button>'
                        f'<button onclick="act(this,\'redo\')" title="R">重出</button>{tog}</div></div>')
                if show == "todo" and not sku and n_ok == len(plan_slots(plan)):
                    continue
                name = st.sku(sid).get("name", sid)
                blocks.append(f'<div class="card"><div class="row"><h2 style="margin:0">{E(name)} · 变体 {v}</h2>'
                              f'<span class="mute">通过 {n_ok}/{len(plan_slots(plan))}</span>'
                              f'<button onclick="post(\'/api/review_all\',{{sku:\'{E(sid)}\',variant:\'{v}\'}}).then(()=>location.reload())">整套通过</button>'
                              f'<a class="btn" href="/sku/{E(sid)}">SKU 页</a></div>'
                              f'<div class="grid" style="margin-top:10px">{"".join(cells)}</div></div>')
        body = f"""{flash(request)}<div class="card"><div class="row"><b>审核墙</b>
<span class="mute">点图看大图（原图 / 生成图 / 回贴后并排）。选中一张后按 A 通过、R 重出、L 切换回贴版。黄框 = 有提示，绿框 = 已通过。</span>
<a class="btn" href="/review?show=all">显示全部</a><a class="btn" href="/review">只看没审完的</a>
<button class="p" onclick="if(confirm('把所有没有黄色提示的图都设为通过？'))post('/api/review_clean').then(r=>{{alert('通过了 '+r.n+' 张');location.reload()}})">批量通过没有提示的</button></div></div>
{''.join(blocks) or '<div class="card mute">没有要审核的图。</div>'}"""
        return page("审核墙", body)

    @app.post("/api/review")
    async def api_review(request: Request):
        d = await request.json()
        sid, v, slot, action = d["sku"], d["variant"], int(d["slot"]), d["action"]
        st = core.store
        if action == "approve":
            st.set_review(sid, v, slot, status="approved")
        elif action == "toggle":
            cur = st.current(sid, v, slot)
            pick = "gen" if cur and cur["file"] == cur["pasted"] else "pasted"
            st.set_review(sid, v, slot, pick=pick)
        elif action == "redo":
            n = len(st.attempts(sid, v, slot))
            if n >= int(core.cfg.get("repair", {}).get("max_attempts_per_slot", 3)) + 3:
                return JSONResponse({"error": f"这个图位已经出了 {n} 版，建议改计划里的文案/画面描述再试"})
            st.set_review(sid, v, slot, status="redo", note=d.get("note", ""))
            try:
                core.generate(sid, v, slots=[slot], notes={slot: d.get("note", "")})
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"error": str(e)})
        return JSONResponse({"ok": True})

    @app.post("/api/review_all")
    async def api_review_all(request: Request):
        d = await request.json()
        plan = core.store.plan(d["sku"], d["variant"])
        for slot in plan_slots(plan):
            if core.store.current(d["sku"], d["variant"], slot):
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
                    if cur and not cur["qa"].get("flags") and not cur["qa"].get("error") \
                            and rev.get(str(slot), {}).get("status") != "approved":
                        st.set_review(sid, v, slot, status="approved")
                        n += 1
        return JSONResponse({"n": n})

    @app.get("/api/jobs")
    def api_jobs(sku: str = ""):
        js = [j for j in core.jobs.recent() if not sku or j["sku"] == sku]
        return JSONResponse([{k: j[k] for k in ("id", "kind", "sku", "variant", "total", "done", "state", "error")}
                             for j in js])

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
                            f'<td>{E(st.sku(sid).get("name"))}</td><td>{v}</td><td>{ok}/{len(slots)}</td>'
                            f'<td>{E(st.sku(sid).get("exported", ""))}</td></tr>')
        exp = Path(core.cfg["workspace"]) / "exports"
        zips = sorted(exp.glob("*.zip"), reverse=True)[:10] if exp.exists() else []
        links = "".join(f'<li><a href="{furl(z)}">{E(z.name)}</a></li>' for z in zips)
        body = f"""{flash(request)}<div class="card"><h2>导出</h2>
<form method="post" action="/export"><table><tr><th></th><th>产品</th><th>变体</th><th>已通过</th><th>上次导出</th></tr>{''.join(rows)}</table>
<p><label><input type="checkbox" name="all"> 没通过的图也导出（默认只导出已通过的）</label></p>
<p class="mute">输出：{{sku}}_{{变体}}_{{图位两位数}}.jpg（质量 92）、ERP 导入表 erp_import.xlsx、每套一张联系表，打包成 zip。
ERP 列名可在 config.yaml 的 erp_columns 改。</p><button class="p">导出</button></form></div>
<div class="card"><h2>最近导出</h2><ul>{links or '<li class=mute>还没有</li>'}</ul>
<p class="mute">文件夹位置：{E(exp)}</p></div>"""
        return page("导出", body)

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
                opts = f"<p class=err>{E(e)}</p>"
        tp = c.get("text_pricing_usd_per_million_tokens", {})
        pr = c["pricing_usd_per_million_tokens"]
        f = c.get("faces", {})
        body = f"""{flash(request)}<div class="card"><h2>设置</h2><form method="post" action="/settings">
<p>API key：{'<span class="tag ok">可用（已设置环境变量）</span>' if api_key_present() else '<span class="tag bad">没有设置</span> 在终端运行 <code>export OPENAI_API_KEY=你的key</code> 后再 <code>python app.py</code>'}</p>
<p><label><input type="checkbox" name="demo_mode" {'checked' if c.get('demo_mode') else ''}> 演示模式（不调用 OpenAI、不花钱，出占位图，用来熟悉流程）</label></p>
<div class="cols2"><div>
<p>每日预算（美元）<input type="number" step="0.5" name="budget" value="{E(c['budget_usd_per_day'])}"></p>
<p>并发（同时出几张）<input type="number" name="concurrency" value="{E(c.get('concurrency', 6))}"></p>
<p>出图模型 <input type="text" name="model" value="{E(g['model'])}"></p>
<p>画质 <select name="quality">{''.join(f'<option {"selected" if g["quality"] == q else ""}>{q}</option>' for q in ("low", "medium", "high"))}</select></p>
<p><label><input type="checkbox" name="route" {'checked' if g.get('route_label_heavy') else ''}> 标签小字多（≥6 行）或有疑似错字时改用</label>
<input type="text" name="heavy_model" value="{E(g['label_heavy_model'])}"><span class=mute>（第二轮实测：默认 flare 即可，sunburst 只在修复时用，所以默认关闭）</span></p>
</div><div>
<p>文本/视觉模型（识别标签、做计划）<input type="text" name="text_model" value="{E(c.get('text_model', 'auto'))}">
<span class=mute>auto = 自动选账号里最新的。</span> <a href="/settings?models=1">查看账号可用模型</a></p>{opts}
<p><label><input type="checkbox" name="faces_all" {'checked' if f.get('allow_full_face') else ''}> 所有类目都允许 AI 完整人脸</label></p>
<p>只在这些类目允许人脸（逗号分隔）<input type="text" name="faces_cats" value="{E(','.join(f.get('categories') or []))}">
<span class=mute>人脸只用于场景图（图位 6），首图仍以产品为主。</span></p>
<p>单价（美元/百万 token）：出图 文字入 <input type="number" step="0.01" name="p_ti" value="{pr['text_input']}">
图片入 <input type="number" step="0.01" name="p_ii" value="{pr['image_input']}"> 输出 <input type="number" step="0.01" name="p_io" value="{pr['image_output']}"></p>
<p>文本模型 输入 <input type="number" step="0.01" name="t_in" value="{tp.get('input', 0)}"> 输出 <input type="number" step="0.01" name="t_out" value="{tp.get('output', 0)}">
<span class=mute>（0 = 不计入预算；按 OpenAI 价格页填）</span></p>
</div></div><button class="p">保存</button></form></div>"""
        return page("设置", body)

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
                     ("time", "sku_id", "variant", "slot", "attempt", "model", "quality", "cost_usd", "latency_s", "status")) + "</tr>"
                     for r in reversed(rows[-200:]))
        n_today = sum(1 for r in rows if r["time"].startswith(today) and r["status"] == "ok")
        body = f"""<div class="card"><h2>账本</h2><p>今天成功调用 {n_today} 次，花费 ${spent():.3f}。完整记录：{E(core.ledger.path)}</p>
<table><tr><th>时间</th><th>SKU</th><th>变体</th><th>图位</th><th>次</th><th>模型</th><th>画质</th><th>费用$</th><th>秒</th><th>结果</th></tr>{tr}</table></div>"""
        return page("账本", body)

    app.state.core = core
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
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
