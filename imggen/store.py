"""Workspace layout and on-disk state. Everything is a file, so any step can be resumed (C7).

workspace/
  skus/{sku_id}/
    sku.json                 name, category, created
    source/                  product photos (read-only copies, C8)
    label.json               confirmed label text (F2)
    label_draft.json         machine transcription waiting for confirmation
    variants/{V}/
      plan.json              style lock + copy per slot (F3)
      attempts/{slot}_{n}.png          generated
      attempts/{slot}_{n}_pasted.png   with the original label pasted back
      attempts/{slot}_{n}.qa.json      checks (F5)
      review.json            user decisions per slot (F7)
  ledger.csv
  exports/{batch_id}/
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import threading
from datetime import datetime
from pathlib import Path

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SLOTS = list(range(1, 9))


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(p: Path, data) -> None:
    """Atomic write: a crash never leaves half a file behind."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip(), flags=re.UNICODE).strip("-")
    return s[:60] or "sku"


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._review_lock = threading.Lock()
        (self.root / "skus").mkdir(parents=True, exist_ok=True)

    # ---- SKUs -------------------------------------------------------------
    def sku_dir(self, sku_id: str) -> Path:
        d = (self.root / "skus" / sku_id).resolve()
        if d.parent != (self.root / "skus").resolve():
            raise ValueError("bad sku id")
        return d

    def list_skus(self) -> list[str]:
        return sorted(p.name for p in (self.root / "skus").iterdir()
                      if (p / "sku.json").exists() and not read_json(p / "sku.json").get("deleted_at"))

    def delete_sku(self, sku_id: str) -> None:
        """Hide a product reversibly; retain source photos, results and ledger identity."""
        if not self.sku(sku_id):
            raise FileNotFoundError(sku_id)
        self.update_sku(sku_id, deleted_at=now())

    def restore_sku(self, sku_id: str) -> None:
        if not self.sku(sku_id):
            raise FileNotFoundError(sku_id)
        data = self.sku(sku_id)
        data.pop("deleted_at", None)
        write_json(self.sku_dir(sku_id) / "sku.json", data)

    def create_sku(self, name: str, files: list[tuple[str, bytes]], category: str = "") -> str:
        base = slug(name)
        sku_id, i = base, 2
        while (self.root / "skus" / sku_id).exists():
            sku_id, i = f"{base}-{i}", i + 1
        d = self.sku_dir(sku_id)
        (d / "source").mkdir(parents=True)
        n = 0
        for fname, data in files:
            ext = Path(fname).suffix.lower()
            if ext not in IMG_EXT or not data:
                continue
            n += 1
            out = d / "source" / f"{n:02d}{ext}"
            out.write_bytes(data)
            os.chmod(out, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)   # read-only copy (C8)
        if n == 0:
            shutil.rmtree(d)
            raise ValueError("没有可用的图片（支持 jpg/png/webp）")
        write_json(d / "sku.json", {"id": sku_id, "name": name, "category": category, "created": now()})
        return sku_id

    def import_folder(self, folder: str | Path) -> list[str]:
        """One sub-folder = one SKU. A folder with images directly in it is one SKU too."""
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            raise ValueError(f"找不到文件夹：{folder}")
        groups = [p for p in sorted(folder.iterdir()) if p.is_dir()]
        if any(p.suffix.lower() in IMG_EXT for p in folder.iterdir()):
            groups = [folder] + groups
        made = []
        for g in groups:
            imgs = [p for p in sorted(g.iterdir()) if p.suffix.lower() in IMG_EXT]
            if imgs:
                made.append(self.create_sku(g.name, [(p.name, p.read_bytes()) for p in imgs]))
        return made

    def sku(self, sku_id: str) -> dict:
        return read_json(self.sku_dir(sku_id) / "sku.json", {})

    def update_sku(self, sku_id: str, **kw) -> None:
        d = self.sku(sku_id)
        d.update(kw)
        write_json(self.sku_dir(sku_id) / "sku.json", d)

    def sources(self, sku_id: str) -> list[Path]:
        return sorted(p for p in (self.sku_dir(sku_id) / "source").iterdir() if p.suffix.lower() in IMG_EXT)

    # ---- label ------------------------------------------------------------
    def label(self, sku_id: str) -> dict | None:
        return read_json(self.sku_dir(sku_id) / "label.json")

    def label_draft(self, sku_id: str) -> dict | None:
        return read_json(self.sku_dir(sku_id) / "label_draft.json")

    # ---- variants ---------------------------------------------------------
    def vdir(self, sku_id: str, v: str) -> Path:
        if not re.fullmatch(r"[A-Z]", v):
            raise ValueError("bad variant")
        return self.sku_dir(sku_id) / "variants" / v

    def variants(self, sku_id: str) -> list[str]:
        d = self.sku_dir(sku_id) / "variants"
        return sorted(p.name for p in d.iterdir() if (p / "plan.json").exists()) if d.exists() else []

    def plan(self, sku_id: str, v: str) -> dict | None:
        return read_json(self.vdir(sku_id, v) / "plan.json")

    def attempts(self, sku_id: str, v: str, slot: int) -> list[int]:
        d = self.vdir(sku_id, v) / "attempts"
        if not d.exists():
            return []
        ns = []
        for p in d.glob(f"{slot}_*.png"):
            m = re.fullmatch(rf"{slot}_(\d+)\.png", p.name)
            if m:
                ns.append(int(m.group(1)))
        return sorted(ns)

    def attempt_paths(self, sku_id: str, v: str, slot: int, n: int) -> dict:
        d = self.vdir(sku_id, v) / "attempts"
        return {"gen": d / f"{slot}_{n}.png", "pasted": d / f"{slot}_{n}_pasted.png",
                "qa": d / f"{slot}_{n}.qa.json"}

    def review(self, sku_id: str, v: str) -> dict:
        return read_json(self.vdir(sku_id, v) / "review.json", {})

    def set_review(self, sku_id: str, v: str, slot: int, **kw) -> None:
        with self._review_lock:
            r = self.review(sku_id, v)
            cur = r.get(str(slot), {})
            cur.update(kw, time=now())
            r[str(slot)] = cur
            write_json(self.vdir(sku_id, v) / "review.json", r)

    def current(self, sku_id: str, v: str, slot: int) -> dict | None:
        """The image shown for a slot: latest attempt, pasted version unless the user picked the raw one."""
        ns = self.attempts(sku_id, v, slot)
        if not ns:
            return None
        n = ns[-1]
        p = self.attempt_paths(sku_id, v, slot, n)
        qa = read_json(p["qa"], {})
        pick = self.review(sku_id, v).get(str(slot), {}).get("pick")
        use_pasted = p["pasted"].exists() and pick != "gen"
        return {"n": n, "file": p["pasted"] if use_pasted else p["gen"], "gen": p["gen"],
                "pasted": p["pasted"] if p["pasted"].exists() else None, "qa": qa, "attempts": len(ns)}

    # ---- status (F1) ------------------------------------------------------
    def status(self, sku_id: str, busy: bool = False) -> str:
        if busy:
            return "生成中"
        lab = self.label(sku_id)
        if lab is None:
            return "待确认" if self.label_draft(sku_id) else "待转录"
        vs = self.variants(sku_id)
        if not vs:
            return "计划中"
        if self.sku(sku_id).get("exported"):
            return "已导出"
        any_missing = False
        for v in vs:
            plan = self.plan(sku_id, v)
            for slot in plan_slots(plan):
                cur = self.current(sku_id, v, slot)
                if cur is None:
                    any_missing = True
                elif cur["qa"].get("error"):
                    return "有问题"
        return "计划中" if any_missing else "待审核"


def plan_slots(plan: dict | None) -> list[int]:
    if not plan:
        return []
    return [int(k) for k, s in sorted(plan.get("slots", {}).items(), key=lambda kv: int(kv[0]))
            if not s.get("skip")]
