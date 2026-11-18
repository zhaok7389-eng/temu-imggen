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
LIMITS = {"headline": 40, "bullet": 28, "caption": 28}


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
    return [x for x in out if x and x.strip()]


def check_text(t: str, label: dict, banned: list[str], kind: str) -> list[str]:
    """Problems with one piece of added text (empty list = fine)."""
    issues = []
    nt = _norm(t)
    allowed = {_norm(a) for a in allowed_texts(label)}
    if nt not in allowed:
        issues.append(f"“{t}” 不在已确认的卖点/品名/容量/件数/用法里")
    raw_ok = {_norm(label.get(k) or "") for k in ("capacity", "pieces_text")}
    if re.search(r"\d|%", t) and nt not in raw_ok:
        issues.append(f"“{t}” 含数字或百分比（只有容量、件数原样照抄可以有数字）")
    for w in banned:
        if re.search(r"(?<!\w)" + re.escape(w.casefold()) + r"(?!\w)", t.casefold()):
            issues.append(f"“{t}” 含禁用词 “{w}”")
    lim = LIMITS.get(kind)
    if lim and len(t) > lim:
        issues.append(f"“{t}” 超过 {lim} 个字符")
    return issues


def validate(plan: dict, label: dict, banned: list[str]) -> list[str]:
    issues = []
    for sid, s in plan.get("slots", {}).items():
        if s.get("skip"):
            continue
        if s.get("headline"):
            issues += [f"图位{sid}：{x}" for x in check_text(s["headline"], label, banned, "headline")]
        for b in s.get("bullets", []):
            issues += [f"图位{sid}：{x}" for x in check_text(b, label, banned, "bullet")]
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
            return claims[int(i)]
        except (TypeError, ValueError, IndexError):
            return None

    headline = claim(llm_out.get("headline_claim")) or label.get("product_name", "")
    bullets = [c for c in (claim(i) for i in llm_out.get("bullet_claims", [])) if c and c != headline]
    if not bullets:
        bullets = [c for c in claims if c != headline]
    style = llm_out.get("style") or {}
    actions = list(llm_out.get("step_actions") or [])
    steps_src = label.get("usage_steps") or []
    slots = {}
    for t in slots_tpl:
        sid, copy = str(t["id"]), t.get("copy", "none")
        s = {"name": t["name"]}
        if copy == "headline_bullets":
            s["headline"] = headline
            s["bullets"] = bullets[: t.get("max_bullets", 3)]
        elif copy == "headline":
            s["headline"] = label.get("key_ingredient") if label.get("key_ingredient") in claims else headline
            s["scene"] = llm_out.get("ingredient_scene", "")
        elif copy == "capacity":
            s["capacity"] = label.get("capacity", "")
            if not s["capacity"]:
                s["skip"] = True
                s["skip_reason"] = "没有容量，尺寸图跳过"
        elif copy == "steps":
            s["steps"] = []
            for k in range(3):
                cap = steps_src[k] if k < len(steps_src) else ""
                act = actions[k] if k < len(actions) else (cap or "applying the product")
                s["steps"].append({"caption": cap, "action": act})
        sid_int = int(sid)
        if sid_int == 1:
            s["scene"] = llm_out.get("hero", "")
        elif sid_int == 4:
            s["scene"] = llm_out.get("texture_scene") or label.get("texture", "")
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
                  capacity=s.get("capacity", ""), scene=s.get("scene", ""), people=people)
    tail = "\n\n" + tpl["slots"]["fidelity"] + " " + (people if slot_id == 6 else tpl["slots"]["no_faces"])
    lb = label_block(label)
    if lb:
        tail += "\n\n" + lb
    if fix_note:
        tail += "\n\nThe previous attempt was rejected. Fix this: " + fix_note
    if t.get("copy") == "steps":
        return [" ".join(_fmt(t["step_prompt"], step_no=k + 1, action=st["action"], **common).split()) + tail
                for k, st in enumerate(s.get("steps", []))]
    return [" ".join(_fmt(t["prompt"], **common).split()) + tail]
