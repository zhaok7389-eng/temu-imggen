"""Cost and timing ledger: one CSV row per API call. Tokens come from the API response."""
from __future__ import annotations
import csv, datetime as dt, threading
from pathlib import Path

FIELDS = ["time", "sku_id", "variant", "slot", "attempt", "model", "quality", "text_in", "image_in",
          "image_out", "cost_usd", "latency_s", "status", "error"]
_lock = threading.Lock()


def cost_usd(usage: dict, prices: dict) -> float:
    return round(usage.get("text_in", 0) * prices["text_input"] / 1e6
                 + usage.get("image_in", 0) * prices["image_input"] / 1e6
                 + usage.get("image_out", 0) * prices["image_output"] / 1e6, 6)


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, FIELDS).writeheader()

    def add(self, **row):
        row.setdefault("time", dt.datetime.now().isoformat(timespec="seconds"))
        with _lock, self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, FIELDS, extrasaction="ignore").writerow(row)

    def spent_today(self) -> float:
        today = dt.date.today().isoformat()
        total = 0.0
        with self.path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["time"].startswith(today) and r["cost_usd"]:
                    total += float(r["cost_usd"])
        return round(total, 6)
