"""Paste guard: a label paste must not make the label text worse.

OCR the generated image before and after the paste and compare both with the
expected label lines (label.json lines once F2 exists; until then the OCR of the
source photo, which carries the same OCR noise as the images being checked).

Reject the paste when
  - the text agreement drops (mean best-line similarity falls by more than `tol`), or
  - a label line now shows up more often than before (ghosting / doubled text).

Local OCR is too noisy to tell "Replenis h" from "Replenish" (see 更新说明.md, v0.1), so
this only catches gross damage: cut, doubled or garbled lines. Fine spelling checks
stay with the vision model (F5).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from imggen.qa.label_text import compare, ocr_lines


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class TextScore:
    mean_sim: float
    line_sims: dict          # expected line -> mean of its best `count` matches
    repeats: int             # extra occurrences of expected lines beyond what the source has
    found: list


def _occurrences(lines: list[str], expected: list[str]) -> dict[str, int]:
    joined = [_key(f) for f in lines]
    out = {}
    for e in set(expected):
        k = _key(e)
        if len(k) >= 5:
            out[e] = sum(j.count(k) for j in joined)
    return out


def _line_sims(expected: list[str], found: list[str]) -> dict[str, float]:
    """A line printed twice on the product (box and sachet both say COLLAGEN FACIAL)
    must be readable twice: score it by its best `count` matches, not just the best one."""
    counts = {}
    for e in expected:
        counts[e] = counts.get(e, 0) + 1
    cands = found + [a + " " + b for a, b in zip(found, found[1:])]
    out = {}
    for e, c in counts.items():
        sims = sorted((compare([e], [f])[0]["sim"] for f in cands), reverse=True) or [0.0]
        top = (sims + [0.0] * c)[:c]
        out[e] = float(np.mean(top))
    return out


def ocr_in_zone(img_bgr: np.ndarray, zone: np.ndarray | None) -> list[str]:
    """OCR lines whose centre lies on the product label zone (added headline text is ignored)."""
    out = []
    for t, _c, b in ocr_lines(img_bgr):
        if zone is not None:
            cx, cy = np.asarray(b).reshape(-1, 2).mean(axis=0)
            y, x = int(round(cy)), int(round(cx))
            if not (0 <= y < zone.shape[0] and 0 <= x < zone.shape[1] and zone[y, x]):
                continue
        out.append(t)
    return out


def text_score(img_bgr, expected, source_counts, zone=None) -> TextScore:
    found = ocr_in_zone(img_bgr, zone)
    ls = _line_sims(expected, found)
    occ = _occurrences(found, expected)
    repeats = sum(max(0, n - source_counts.get(e, 1)) for e, n in occ.items())
    return TextScore(float(np.mean(list(ls.values()))) if ls else 0.0, ls, repeats, found)


def source_counts(expected: list[str]) -> dict[str, int]:
    """How often each expected line legitimately appears on the product itself
    (box and bottle both say "Turmeric Serum"), counted the same way as in the
    checked images so short lines inside longer ones do not count as repeats."""
    return _occurrences(expected, expected)


@dataclass
class GuardResult:
    keep: bool
    reason: str
    before: TextScore
    after: TextScore


def check(gen_bgr, pasted_bgr, expected: list[str], zone=None, tol: float = 0.02,
          line_drop: float = 0.15) -> GuardResult:
    """zone: generated-image mask of the label zones (PasteResult.zone)."""
    counts = source_counts(expected)
    b = text_score(gen_bgr, expected, counts, zone)
    a = text_score(pasted_bgr, expected, counts, zone)
    if a.repeats > b.repeats:
        return GuardResult(False, "paste doubled label text (%d -> %d repeats)" % (b.repeats, a.repeats), b, a)
    worse = [e for e in a.line_sims if len(_key(e)) >= 5 and a.line_sims[e] < b.line_sims[e] - line_drop]
    if worse:
        e = worse[0]
        return GuardResult(False, "paste damaged label line %r (%.2f -> %.2f)" % (e, b.line_sims[e], a.line_sims[e]), b, a)
    if a.mean_sim < b.mean_sim - tol:
        return GuardResult(False, "paste made label text worse (%.3f -> %.3f)" % (b.mean_sim, a.mean_sim), b, a)
    return GuardResult(True, "", b, a)
