from __future__ import annotations
from pathlib import Path
import os
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    if not p.exists():
        p = ROOT / "config.example.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg["workspace"] = str((ROOT / cfg.get("workspace", "./workspace")).resolve())
    return cfg


def api_key_present() -> bool:
    """Only reports whether the key exists; never returns or logs it."""
    return bool(os.getenv("OPENAI_API_KEY"))
