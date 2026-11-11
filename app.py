"""Local web tool. Run:  python app.py  then open http://127.0.0.1:8765

v0 (11/11): review page for label-repaste results. Point it at a results folder:
    python app.py --results ./workspace/paste-demo
"""
from __future__ import annotations
import argparse, html, json, webbrowser
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from imggen.config import load_config, api_key_present

def build_app(results: Path) -> FastAPI:
    app = FastAPI(title="temu-imggen")
    if results.exists():
        app.mount("/r", StaticFiles(directory=results), name="r")

    @app.get("/", response_class=HTMLResponse)
    def index():
        cfg = load_config()
        summary = results / "summary.json"
        rows = json.loads(summary.read_text()) if summary.exists() else []
        cards = []
        for r in rows:
            cmp_name = r["file"].replace(".png", "_cmp.jpg")
            status = "通过" if r["ok"] else "未通过：" + html.escape(r["reason"])
            cls = "ok" if r["ok"] else "bad"
            cards.append(f'<div class="card {cls}"><div class="t">{html.escape(r["file"])} · {status} · 覆盖 {r["coverage"]}</div>'
                         f'<img loading="lazy" src="/r/{html.escape(cmp_name)}"></div>')
        key = "已设置" if api_key_present() else "未设置（本页不需要）"
        return f"""<!doctype html><meta charset="utf-8"><title>temu-imggen</title>
<style>body{{font-family:-apple-system,sans-serif;margin:24px;background:#f6f6f4}}
.card{{background:#fff;border-radius:10px;padding:10px;margin:0 0 16px;border-left:6px solid #3a9}}
.card.bad{{border-left-color:#d55}} .t{{font-size:14px;margin-bottom:6px}} img{{width:100%;max-width:1536px}}</style>
<h2>原标签回贴 · 结果审核</h2>
<p>左：产品原图　中：AI 生成图　右：贴回原标签后。API key：{key}。模型：{html.escape(cfg['generation']['model'])}</p>
<p>结果目录：{html.escape(str(results))}，共 {len(rows)} 张。</p>
{''.join(cards) or '<p>还没有结果。先运行 scripts/try_label_paste.py。</p>'}"""
    return app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="./workspace/paste-demo")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    print("打开", url)
    try: webbrowser.open(url)
    except Exception: pass
    uvicorn.run(build_app(Path(a.results).resolve()), host="127.0.0.1", port=a.port)
