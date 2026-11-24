"""Source-backed merchandising suggestions that take effect only after approval."""
from __future__ import annotations

import re

from .plan import build_plan, check_text, ingredient_names, validate

COPY_TYPES = {
    "benefit": (2, "功能卖点", "bullet"),
    "ingredient": (3, "成分或材质", "ingredient"),
    "texture": (4, "质地说明", "caption"),
    "usage": (5, "用法步骤", "caption"),
    "scene": (6, "使用场景", "caption"),
    "capacity": (7, "容量规格", ""),
}
_SELECTION_LIMITS = {"benefit": 3, "ingredient": 5, "texture": 1, "usage": 3, "scene": 1, "capacity": 1}


def sources_for(label: dict, source: str) -> list[str]:
    return [source] + [line["text"] for line in label.get("lines", []) if line.get("text")]


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def checked_suggestions(raw: object, sources: list[str], banned: list[str]) -> list[dict]:
    if not isinstance(raw, list):
        return []
    evidence = [_normalized(s) for s in sources]
    found, seen = [], set()
    for row in raw[:30]:
        if not isinstance(row, dict):
            continue
        kind, text, quote = (row.get(k) for k in ("kind", "text", "source_quote"))
        if not all(isinstance(v, str) for v in (kind, text, quote)) or kind not in COPY_TYPES:
            continue
        text, quote = " ".join(text.split()), quote.strip()
        q = _normalized(quote)
        identity = (kind, _normalized(text))
        if not text or len(q) < 3 or not any(q in s for s in evidence) or identity in seen:
            continue
        if kind == "capacity":
            if _normalized(text) != q or not re.search(r"\d\s*(?:ml|g|kg|l|(?:fl\.?\s*)?oz)\b", text, re.I):
                continue
            allowed = {"capacity": text}
        else:
            allowed = {"allowed_claims": [text]}
        if check_text(text, allowed, banned, COPY_TYPES[kind][2]):
            continue
        seen.add(identity)
        found.append({"kind": kind, "text": text, "source_quote": quote, "slot": COPY_TYPES[kind][0]})
        if len(found) == 18:
            break
    return found


def apply_suggestions(label: dict, selected: list[dict]) -> dict:
    updated = dict(label)
    grouped = {kind: [r["text"] for r in selected if r["kind"] == kind] for kind in COPY_TYPES}
    for kind, count in _SELECTION_LIMITS.items():
        if len(grouped[kind]) > count:
            raise ValueError(f"{COPY_TYPES[kind][1]}最多选 {count} 条。")
    if grouped["usage"] and len(grouped["usage"]) != 3:
        raise ValueError("使用方法图需要按原顺序选齐三步。可先采用其它图位文案。")
    if grouped["benefit"]:
        updated["allowed_claims"] = list(dict.fromkeys(grouped["benefit"] + label.get("allowed_claims", [])))
    if grouped["ingredient"]:
        updated["ingredient_names"] = list(dict.fromkeys(grouped["ingredient"] + ingredient_names(label)))
    if grouped["usage"]:
        updated["usage_steps"] = grouped["usage"][:3]
    for kind, field in (("texture", "texture_caption"), ("scene", "scene_caption"), ("capacity", "capacity")):
        if grouped[kind]:
            updated[field] = grouped[kind][0]
    evidence = {(x["kind"], x["text"]): x for x in label.get("copy_sources", []) + selected}
    updated["copy_sources"] = list(evidence.values())
    return updated


# Keep the visual plan and existing attempts while replacing copy for affected slots.
_FIELDS = {
    2: ("copy_layout", "headline", "bullets"),
    3: ("copy_layout", "ingredient_labels", "headline", "skip", "skip_reason"),
    4: ("copy_layout", "texture_caption", "scene"),
    5: ("steps", "skip", "skip_reason"),
    6: ("copy_layout", "scene_caption"),
    7: ("copy_layout", "capacity", "skip", "skip_reason"),
}


def transfer_copy(plan: dict, label: dict, selected: list[dict], templates: dict) -> dict:
    slots = plan["slots"]
    new = build_plan(plan["variant"], label, templates["slots"]["slots"], {})
    for slot in {row["slot"] for row in selected}:
        sid = str(slot)
        if sid not in slots or sid not in new["slots"]:
            continue
        for key in _FIELDS[slot]:
            if key in new["slots"][sid]:
                slots[sid][key] = new["slots"][sid][key]
            else:
                slots[sid].pop(key, None)
    plan["issues"] = validate(plan, label, templates["banned"]["words"])
    return plan
