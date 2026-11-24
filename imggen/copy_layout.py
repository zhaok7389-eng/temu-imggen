"""Phrase rules for added text.

Since v1.6 the image model designs the approved text into the image itself (no program-drawn
text strip). This module decides which words belong together, so the prompt can tell the
model which phrases must stay on one line (HYALURONIC ACID, DAY & NIGHT, 1.7 FL OZ / 50ML),
and the Q3 check can verify it. wrap_copy is kept for any text the program still sets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import ImageFont

UNITS = (r"fl\.?\s?oz|fl\.?|oz|ml|l|g|kg|mg|lb|lbs|pcs|pc|pieces|sheets|sheet|count|ct|pack|packs|"
         r"capsules|tablets|pads|x")


class LayoutOverflow(ValueError):
    """The approved copy does not fit at a readable size: change the layout, do not cut words."""


def copy_font(size: int, bold: bool = True):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    linux = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (f"/System/Library/Fonts/Supplemental/{name}",
                 f"/usr/share/fonts/truetype/dejavu/{linux}",
                 f"C:/Windows/Fonts/{'arialbd.ttf' if bold else 'arial.ttf'}"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------- phrase units
def _words(paragraph: str) -> list[str]:
    return paragraph.split()


def _norm_word(w: str) -> str:
    return re.sub(r"[^\w&%/.+'-]", "", w.casefold())


def phrase_units(text: str, keep: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Split one paragraph into units that are never broken across lines.

    Rules, in order:
      1. phrases listed in `keep` (confirmed ingredient names, product name ...), matched
         word by word, case-insensitive;
      2. "A & B" / "A + B" stay together (DAY & NIGHT, COLLAGEN, RETINOL & ...);
      3. a number stays with its unit and with "/" or "X" joins: 1.7 FL OZ / 50ML,
         4 SHEETS X 1.13OZ (32G);
      4. a parenthesised part stays with the word before it.
    """
    words = _words(text)
    if not words:
        return []
    keep_seqs = sorted(({tuple(_norm_word(w) for w in k.split()) for k in keep if k and len(k.split()) > 1}),
                       key=len, reverse=True)
    glue = [False] * len(words)          # glue[i]: word i joins the word before it

    # 1. keep phrases
    normed = [_norm_word(w) for w in words]
    i = 0
    while i < len(words):
        for seq in keep_seqs:
            if tuple(normed[i:i + len(seq)]) == seq:
                for j in range(i + 1, i + len(seq)):
                    glue[j] = True
                i += len(seq) - 1
                break
        i += 1

    unit_re = re.compile(rf"^\(?({UNITS})[).,;:]*$", re.I)
    num_re = re.compile(r"^\(?\d[\d.,]*(%|" + UNITS + r")?\)?[.,;:]*$", re.I)
    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        # 2. ampersand / plus joins
        if cur in {"&", "+"} or prev in {"&", "+"}:
            glue[i] = True
        # 3. numbers with units and joiners
        elif num_re.match(prev) and (unit_re.match(cur) or num_re.match(cur)):
            glue[i] = True
        elif unit_re.match(prev) and re.match(r"(?i)^(oz|fl\.?)$", cur):
            glue[i] = True              # FL OZ written as two words
        elif cur in {"/", "x", "X", "×"} and (num_re.match(prev) or unit_re.match(prev)):
            glue[i] = True
        elif prev in {"/", "x", "X", "×"} and num_re.match(cur):
            glue[i] = True
        # 4. parenthesis
        elif cur.startswith("("):
            glue[i] = True

    units, current = [], words[0]
    for i in range(1, len(words)):
        if glue[i]:
            current += " " + words[i]
        else:
            units.append(current)
            current = words[i]
    units.append(current)
    return units


def wrap_copy(text: str, font, width: int, keep: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Wrap by rendered width without ever splitting a word or a protected phrase.

    Raises LayoutOverflow when one unit alone is wider than `width`.
    """
    lines = []
    for paragraph in text.splitlines():
        current = ""
        for unit in phrase_units(paragraph, keep):
            if font.getlength(unit) > width:
                raise LayoutOverflow(f"“{unit}” 在当前字号下一行放不下")
            candidate = f"{current} {unit}" if current else unit
            if font.getlength(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = unit
        lines.append(current)
    return lines


@dataclass
class CopyBlock:
    lines: list[str]
    font: ImageFont.ImageFont
    line_height: int

    @property
    def height(self):
        return len(self.lines) * self.line_height

    def draw(self, canvas, x, y, color):
        for index, line in enumerate(self.lines):
            canvas.text((x, y + index * self.line_height), line, font=self.font, fill=color, anchor="lt")


def copy_block(text: str, size: int, width: int, *, bold: bool = True,
               keep: list[str] | tuple[str, ...] = ()) -> CopyBlock:
    font = copy_font(size, bold)
    line_height = max(1, round(size * 1.18))
    return CopyBlock(wrap_copy(text, font, width, keep), font, line_height)
