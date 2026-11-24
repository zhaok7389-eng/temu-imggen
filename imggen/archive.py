"""Finished images (成品): an approved slot is filed at once, no ZIP step.

workspace/成品/YYYY-MM-DD/<SKU>/
    <SKU>_<V>_<slot>_v<n>.jpg     every approved version is kept (decision 2026-11-23 #4)
    manifest.json                 per variant and slot: current approved file + all versions

The date folder is the day of approval. Approving the same version twice does nothing;
approving a newer version adds a file and makes it current; older files stay.
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from PIL import Image

from .store import Store, now, read_json, write_json

_lock = threading.Lock()


def archive_root(workspace: str | Path) -> Path:
    return Path(workspace) / "成品"


def archive_slot(store: Store, workspace: str | Path, sku_id: str, v: str, slot: int,
                 quality: int = 92) -> Path | None:
    cur = store.current(sku_id, v, slot)
    if cur is None:
        return None
    day = date.today().isoformat()
    folder = archive_root(workspace) / day / sku_id
    name = f"{sku_id}_{v}_{slot:02d}_v{cur['n']}.jpg"
    out = folder / name
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            with Image.open(cur["file"]) as im:
                im.convert("RGB").save(out, "JPEG", quality=quality)
        manifest = read_json(folder / "manifest.json", {"sku": sku_id, "name": store.sku(sku_id).get("name", sku_id)})
        entry = manifest.setdefault("variants", {}).setdefault(v, {}).setdefault(str(slot), {"versions": []})
        if name not in [x["file"] for x in entry["versions"]]:
            entry["versions"].append({"file": name, "attempt": cur["n"],
                                      "which": "pasted" if cur["file"] == cur["pasted"] else "generated",
                                      "approved_at": now()})
        entry["current"] = name
        write_json(folder / "manifest.json", manifest)
    store.set_review(sku_id, v, slot, archived=str(out))
    return out


def latest_folder(workspace: str | Path, sku_id: str) -> Path | None:
    root = archive_root(workspace)
    if not root.exists():
        return None
    days = sorted((d for d in root.iterdir() if (d / sku_id).is_dir()), reverse=True)
    return days[0] / sku_id if days else None
