from __future__ import annotations
from pathlib import Path
import os
import yaml

ROOT = Path(__file__).resolve().parents[1]

# Text/vision model price used when config.yaml leaves it at 0 or empty, so label, plan and
# copy calls count toward the daily budget (decision 2026-11-23). Estimate at GPT-4.1 list
# price, USD per million tokens; the OpenAI bill is authoritative.
DEFAULT_TEXT_PRICING = {"input": 2.0, "output": 8.0}


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    if not p.exists():
        p = ROOT / "config.example.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg["workspace"] = str((ROOT / cfg.get("workspace", "./workspace")).resolve())
    tp = cfg.get("text_pricing_usd_per_million_tokens") or {}
    if not float(tp.get("input") or 0) and not float(tp.get("output") or 0):
        cfg["text_pricing_usd_per_million_tokens"] = dict(DEFAULT_TEXT_PRICING)
        cfg["text_pricing_estimated"] = True
    return cfg


def api_key_present() -> bool:
    """Only reports whether the key exists; never returns or logs it."""
    return bool(os.getenv("OPENAI_API_KEY"))
