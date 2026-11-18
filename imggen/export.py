"""Export (F8): JPG files, ERP sheet, contact sheets, one zip."""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .store import Store, plan_slots

DEFAULT_COLUMNS = {   # our field -> column title in the ERP template (change in config.yaml: erp_columns)
    "sku_id": "SKU",
    "variant": "变体",
    "main": "主图",
    "sub": "副图{n}",
    "qa": "验收状态",
    "cost": "费用(USD)",
}


def export(store: Store, items: list[tuple[str, str]], ledger_rows: list[dict], cfg: dict,
           approved_only: bool = True) -> Path:
    """items: [(sku_id, variant)]. Returns the zip path."""
    from openpyxl import Workbook
    batch = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(cfg["workspace"]) / "exports" / batch
    out.mkdir(parents=True, exist_ok=True)
    cols = dict(DEFAULT_COLUMNS, **(cfg.get("erp_columns") or {}))
    wb = Workbook()
    ws = wb.active
    ws.title = "images"
    ws.append([cols["sku_id"], cols["variant"], cols["main"]] + [cols["sub"].format(n=i) for i in range(2, 9)]
              + [cols["qa"], cols["cost"]])
    cost = {}
    for r in ledger_rows:
        try:
            cost[r["sku_id"]] = cost.get(r["sku_id"], 0.0) + float(r.get("cost_usd") or 0)
        except ValueError:
            pass
    q = int(cfg.get("export_jpg_quality", 92))
    for sku_id, v in items:
        plan = store.plan(sku_id, v)
        review = store.review(sku_id, v)
        names, missing = {}, []
        for slot in plan_slots(plan):
            cur = store.current(sku_id, v, slot)
            ok = review.get(str(slot), {}).get("status") == "approved"
            if cur is None or (approved_only and not ok):
                missing.append(slot)
                continue
            name = f"{sku_id}_{v}_{slot:02d}.jpg"
            Image.open(cur["file"]).convert("RGB").save(out / name, "JPEG", quality=q)
            names[slot] = name
        ws.append([sku_id, v] + [names.get(i, "") for i in range(1, 9)]
                  + ["完整" if not missing else "缺图位 " + ",".join(map(str, missing)),
                     round(cost.get(sku_id, 0.0), 4)])
        contact_sheet([out / names[i] for i in sorted(names)], out / f"{sku_id}_{v}_contact.jpg",
                      f"{sku_id} · {v}")
        store.update_sku(sku_id, exported=batch)
    wb.save(out / "erp_import.xlsx")
    zpath = out.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            z.write(p, p.name)
    return zpath


def contact_sheet(files: list[Path], out: Path, title: str, cell: int = 300) -> None:
    if not files:
        return
    cols, rows = 4, (len(files) + 3) // 4
    sheet = Image.new("RGB", (cols * cell + 50, rows * cell + 80 + rows * 10), "white")
    d = ImageDraw.Draw(sheet)
    d.text((20, 20), title, fill="black")
    for i, f in enumerate(files):
        im = Image.open(f).convert("RGB").resize((cell, cell))
        sheet.paste(im, (10 + (i % cols) * (cell + 10), 60 + (i // cols) * (cell + 10)))
    sheet.save(out, "JPEG", quality=88)
