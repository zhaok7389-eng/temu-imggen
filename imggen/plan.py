"""Set plan (F3): style lock + copy per slot, validation, and image prompts (F4).

The text model only picks copy by index from the confirmed claims, so added text can only
come from the confirmed label (C4). Validation still checks everything, including copy the
user edits by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

PKG_TEMPLATES = Path(__file__).resolve().parent / "templates"


def load_templates(workspace: str | Path) -> dict:
    """workspace/templates/*.yaml override the defaults shipped with the code."""
    out = {}
    for name in ("slots", "banned"):
        p = Path(workspace) / "templates" / f"{name}.yaml"
        if not p.exists():
            p = PKG_TEMPLATES / f"{name}.yaml"
        out[name] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w%/.&+'-]+", " ", s.casefold()).split())


def allowed_texts(label: dict) -> list[str]:
    out = list(label.get("allowed_claims", []))
    for k in ("product_name", "capacity", "pieces_text"):
        if label.get(k):
            out.append(label[k])
    out += label.get("usage_steps", [])
    out += ingredient_names(label)
    out.append(label.get("texture_caption", ""))
    out.append(label.get("scene_caption", ""))
    return [x for x in out if x and x.strip()]


def ingredient_names(label: dict) -> list[str]:
    return list(dict.fromkeys(label.get("ingredient_names") or
                              ([label["key_ingredient"]] if label.get("key_ingredient") else [])))


def check_text(t: str, label: dict, banned: list[str], kind: str = "") -> list[str]:
    """Validate copy against confirmed sources. Layout handles its length."""
    issues = []
    nt = _norm(t)
    allowed = {_norm(a) for a in allowed_texts(label)}
    if nt not in allowed:
        issues.append(f"“{t}” 不在已确认的商品文案里")
    raw_ok = {_norm(label.get(k) or "") for k in ("capacity", "pieces_text")}
    if re.search(r"\d|%", t) and nt not in raw_ok:
        issues.append(f"“{t}” 含数字或百分比（只有容量、件数原样照抄可以有数字）")
    for w in banned:
        if re.search(r"(?<!\w)" + re.escape(w.casefold()) + r"(?!\w)", t.casefold()):
            issues.append(f"“{t}” 含禁用词 “{w}”")
    return issues


def validate(plan: dict, label: dict, banned: list[str]) -> list[str]:
    issues = []
    for sid, s in plan.get("slots", {}).items():
        if s.get("skip"):
            continue
        if s.get("copy_layout") == "benefits" and len(s.get("bullets", [])) > 3:
            issues.append(f"图位{sid}：卖点图最多三条要点")
        if s.get("headline"):
            issues += [f"图位{sid}：{x}" for x in check_text(s["headline"], label, banned, "headline")]
        for b in s.get("bullets", []):
            issues += [f"图位{sid}：{x}" for x in check_text(b, label, banned, "bullet")]
        for name in s.get("ingredient_labels", []):
            if _norm(name) not in {_norm(n) for n in ingredient_names(label)}:
                issues.append(f"图位{sid}：“{name}” 不是已确认的成分或材质")
            issues += [f"图位{sid}：{x}" for x in check_text(name, label, banned, "ingredient")]
        if s.get("texture_caption"):
            issues += [f"图位{sid}：{x}" for x in check_text(s["texture_caption"], label, banned, "caption")]
        if s.get("scene_caption"):
            issues += [f"图位{sid}：{x}" for x in check_text(s["scene_caption"], label, banned, "caption")]
        for st in s.get("steps", []):
            if st.get("caption"):
                issues += [f"图位{sid}：{x}" for x in check_text(st["caption"], label, banned, "caption")]
        if s.get("capacity"):
            issues += [f"图位{sid}：{x}" for x in check_text(s["capacity"], label, banned, "")]
    return issues


def build_plan(variant: str, label: dict, slots_tpl: list[dict], llm_out: dict) -> dict:
    """Turn the model's choices (indices) into a plan with concrete copy."""
    claims = label.get("allowed_claims", [])

    def claim(i):
        try:
            index = int(i)
            return claims[index] if index >= 0 else None
        except (TypeError, ValueError, IndexError):
            return None

    headline = label.get("product_name") or claim(llm_out.get("headline_claim")) or ""
    chosen = [claim(i) for i in (llm_out.get("bullet_claims") or [])]
    bullets = list(dict.fromkeys(c for c in chosen + claims if c and _norm(c) != _norm(headline)))
    style = llm_out.get("style") or {}
    actions = list(llm_out.get("step_actions") or [])
    steps_src = label.get("usage_steps") or []
    slots = {}
    for t in slots_tpl:
        sid, copy = str(t["id"]), t.get("copy", "none")
        s = {"name": t["name"]}
        if t.get("layout"):
            s["copy_layout"] = t["layout"]
        if copy == "headline_bullets":
            s["headline"] = headline
            s["bullets"] = bullets[: t.get("max_bullets", 3)]
        elif copy == "headline":
            s["headline"] = label.get("key_ingredient") if label.get("key_ingredient") in claims else headline
            s["scene"] = llm_out.get("ingredient_scene", "")
        elif copy == "ingredients":
            s["ingredient_labels"] = ingredient_names(label)[:5]
            if not s["ingredient_labels"]:
                s.update(skip=True, skip_reason="缺少已确认成分或材质，成分图跳过")
        elif copy == "texture":
            s["texture_caption"] = label.get("texture_caption", "")
        elif copy == "scene":
            s["scene_caption"] = label.get("scene_caption", "")
        elif copy == "capacity":
            s["capacity"] = label.get("capacity", "")
            if not s["capacity"]:
                s["skip"] = True
                s["skip_reason"] = "没有容量，尺寸图跳过"
        elif copy == "steps":
            s["steps"] = []
            if t.get("confirmed_steps_only") and (len(steps_src) < 3 or not all(steps_src[:3])):
                s.update(skip=True, skip_reason="缺少三步已确认用法，使用方法图跳过")
                slots[sid] = s
                continue
            for k in range(3):
                cap = steps_src[k] if k < len(steps_src) else ""
                act = cap if t.get("confirmed_steps_only") else (
                    actions[k] if k < len(actions) else (cap or "applying the product"))
                s["steps"].append({"caption": cap, "action": act})
        sid_int = int(sid)
        if sid_int == 1:
            s["scene"] = llm_out.get("hero", "")
        elif sid_int == 4:
            s["scene"] = label.get("texture_caption") or llm_out.get("texture_scene") or label.get("texture", "")
        elif sid_int == 6:
            s["scene"] = llm_out.get("lifestyle_scene", "a bright bathroom vanity")
        elif sid_int == 8:
            s["scene"] = llm_out.get("gift_scene", "an elegant gift setting")
        slots[sid] = s
    return {"variant": variant, "style": style, "slots": slots}


def style_text(style: dict) -> str:
    parts = []
    for k, lab in (("backdrop", "Backdrop"), ("palette", "Colour palette"), ("light", "Lighting"),
                   ("props", "Props")):
        if style.get(k):
            parts.append(f"{lab}: {style[k]}.")
    return " ".join(parts)


def panel_copy(slot: dict) -> tuple[str, list[str]]:
    layout = slot.get("copy_layout")
    if layout == "benefits":
        return slot.get("headline", ""), slot.get("bullets", [])
    if layout == "ingredients" and slot.get("ingredient_labels"):
        return "KEY INGREDIENTS", slot["ingredient_labels"]
    if layout == "texture":
        return slot.get("texture_caption", ""), []
    if layout == "scene":
        return slot.get("scene_caption", ""), []
    if layout == "capacity":
        return slot.get("capacity", ""), []
    return "", []


def label_block(label: dict) -> str:
    lines = [ln["text"] for ln in label.get("lines", []) if ln.get("text")]
    if not lines:
        return ""
    body = "\n".join(f"[{i}] {t}" for i, t in enumerate(lines, 1))
    return ("The product label contains exactly this text, line by line. Reproduce it exactly, "
            "including misspellings:\n" + body + "\nDo not correct, translate or re-letter any of it.")


def _fmt(tpl: str, **kw) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(kw.get(m.group(1), "")), tpl)


def prompts_for_slot(slot_id: int, plan: dict, label: dict, tpl: dict, faces: bool,
                     fix_note: str = "") -> list[str]:
    """Image prompt(s) for one slot. The how-to slot returns one prompt per step."""
    t = next(x for x in tpl["slots"]["slots"] if int(x["id"]) == slot_id)
    s = plan["slots"][str(slot_id)]
    people = tpl["slots"]["faces_ok"] if faces else tpl["slots"]["no_faces"]
    common = dict(style=style_text(plan.get("style", {})), headline=s.get("headline", ""),
                  bullets=" / ".join(f'"{b}"' for b in s.get("bullets", [])),
                  capacity=s.get("capacity", ""), scene=s.get("scene", ""), people=people,
                  ingredients=", ".join(s.get("ingredient_labels", [])))
    tail = "\n\n" + tpl["slots"]["fidelity"] + " " + (people if slot_id == 6 else tpl["slots"]["no_faces"])
    lb = label_block(label)
    if lb:
        tail += "\n\n" + lb
    if fix_note:
        tail += "\n\nThe previous attempt was rejected. Fix this: " + fix_note
    if t.get("copy") == "steps":
        return [" ".join(_fmt(t["step_prompt"], step_no=k + 1, action=st["action"], **common).split()) + tail
                for k, st in enumerate(s.get("steps", []))]
    if s.get("copy_layout"):
        if s["copy_layout"] == "ingredients":
            common["style"] = style_text({k: v for k, v in plan.get("style", {}).items() if k != "props"})
        photo = " ".join(_fmt(t.get("photo_prompt", t["prompt"]), **common).split())
        if any(panel_copy(s)):
            photo += " Keep every important object within the central 72 percent of the image height."
        # The renderer, not the image model, puts approved copy on the finished artwork.
        return [photo + tail + "\n\nNo added text, symbols, badges, captions, headlines, "
                "or lettering outside the original packaging. The workbench adds approved copy later."]
    return [" ".join(_fmt(t["prompt"], **common).split()) + tail]
