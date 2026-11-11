"""Label text check (QA Q1/Q2): OCR the image and compare with the confirmed label lines."""
from __future__ import annotations
import difflib
from functools import lru_cache
import numpy as np


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:  # optional dependency, see requirements-ocr.txt
        raise RuntimeError("本地 OCR 未安装（可选）：pip install -r requirements-ocr.txt，需要 Python 3.12 或更低") from e
    return RapidOCR()


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def ocr_lines(bgr: np.ndarray, upscale: float = 2.0):
    """Return [(text, confidence, box)]. Upscaling helps the small label print."""
    import cv2
    img = cv2.resize(bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC) if upscale != 1 else bgr
    res, _ = _engine()(img)
    return [(t, float(c), np.array(b) / upscale) for b, t, c in (res or [])]


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def compare(expected: list[str], found: list[str]):
    """For each expected line, the best similarity against any OCR line or any
    concatenation of two neighbouring OCR lines (OCR sometimes splits/merges lines)."""
    cands = [_norm(f) for f in found]
    cands += [_norm(a + " " + b) for a, b in zip(found, found[1:])]
    out = []
    for e in expected:
        en = _norm(e)
        best, best_txt = 0.0, ""
        for c in cands:
            r = difflib.SequenceMatcher(None, en, c).ratio()
            # also allow the expected line to be a substring of a longer OCR line
            if en and en in c:
                r = max(r, 0.999)
            if r > best:
                best, best_txt = r, c
        out.append({"expected": e, "found": best_txt, "sim": round(best, 3)})
    return out
