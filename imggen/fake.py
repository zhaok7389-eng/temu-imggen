"""Demo mode: a stand-in for the OpenAI client. No network, no cost.

Lets the whole flow (import -> transcribe -> confirm -> plan -> generate -> review -> export)
be clicked through and tested without an API key. Images are simple mock-ups: the product
photo on a coloured background with the quoted copy drawn on it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for f in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _usage(**kw):
    d = {"input_tokens": 0, "output_tokens": 0, "input_tokens_details": {"text_tokens": 0, "image_tokens": 0}}
    return SimpleNamespace(model_dump=lambda: d, **kw)


class _Images:
    def edit(self, model, image, prompt, size="1024x1024", quality="medium", n=1, **kw):
        f = image[0] if isinstance(image, list) else image
        prod = Image.open(f)
        prod.load()
        if prod.mode != "RGBA":
            prod = prod.convert("RGBA")
        h = hashlib.md5(prompt.encode()).digest()
        bg = (150 + h[0] % 100, 120 + h[1] % 120, 110 + h[2] % 130)
        W = 1024
        canvas = Image.new("RGB", (W, W), bg)
        quoted = re.findall(r'"([^"]{1,60})"', prompt.split("Use the attached product photo")[0])
        right = bool(quoted) and "on the right" in prompt
        box = int(W * (0.55 if right else 0.65))
        prod.thumbnail((box, box))
        x = W - prod.width - 40 if right else (W - prod.width) // 2
        canvas.paste(prod, (x, (W - prod.height) // 2), prod)
        d = ImageDraw.Draw(canvas)
        y = 60
        for i, q in enumerate(quoted[:5]):
            d.text((40, y), q, fill=(20, 20, 20), font=_font(44 if i == 0 else 30))
            y += 70 if i == 0 else 48
        d.text((20, W - 34), "DEMO · " + prompt[:70], fill=(40, 40, 40), font=_font(18))
        b = io.BytesIO()
        canvas.save(b, "PNG")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b.getvalue()).decode())],
                               usage=_usage())


def _ocr_from_data_urls(content) -> list[str]:
    try:
        import numpy as np
        import cv2
        from imggen.qa.label_text import ocr_lines
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in content:
        if c.get("type") != "image_url":
            continue
        raw = base64.b64decode(c["image_url"]["url"].split(",", 1)[1])
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        try:
            out += [t for t, _c, _b in ocr_lines(img, upscale=1.0)]
        except RuntimeError:
            return []
    return out


class _Completions:
    def create(self, model, messages, **kw):
        content = messages[-1]["content"]
        text = content if isinstance(content, str) else " ".join(
            c.get("text", "") for c in content if c.get("type") == "text")
        if "transcribe" in text:
            lines = _ocr_from_data_urls(content) or ["DEMO PRODUCT", "Demo label line", "1.0 FL OZ / 30ML"]
            out = {
                "lines": [{"text": t, "size": "big" if i < 2 else "small", "image": 0, "hidden": False}
                          for i, t in enumerate(lines)],
                "product_name": lines[0] if lines else "DEMO PRODUCT",
                "capacity": next((t for t in lines if re.search(r"\d.*(ml|oz|g)\b", t, re.I)), ""),
                "pieces": 1, "category": "cream", "suspected_typos": [],
                "claim_candidates": ["Moisturizing", "Smooth Skin", "Daily Care"],
                "usage_steps": ["Cleanse face", "Apply a small amount", "Massage gently"],
                "key_ingredient": "", "texture": "rich white cream",
            }
        else:
            m = re.search(r'"claims": (\{.*?\})', text)
            n = len(json.loads(m.group(1))) if m else 0
            out = {"style": {"backdrop": "soft warm beige studio backdrop", "palette": "cream and gold",
                             "light": "soft daylight", "props": "linen and a cream swirl"},
                   "hero": "product centered on a low plinth",
                   "headline_claim": None, "bullet_claims": list(range(min(3, n))),
                   "ingredient_scene": "fresh botanicals", "texture_scene": "a glossy swirl of cream",
                   "step_actions": ["washing the face with water", "taking a small amount on a fingertip",
                                    "massaging onto the cheek"],
                   "lifestyle_scene": "a bright bathroom vanity", "gift_scene": "an open gift box with ribbon"}
        msg = SimpleNamespace(content=json.dumps(out, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))


class FakeOpenAI:
    def __init__(self):
        self.images = _Images()
        self.chat = SimpleNamespace(completions=_Completions())
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="gpt-demo", created=1)])
