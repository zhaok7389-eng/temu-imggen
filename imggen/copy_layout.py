"""Copy layer: approved text rendered on top of a generated photo.

Since v1.5 the photo and the copy are separate layers:
  - the generated photo ({slot}_{n}.png) and its label-paste version ({slot}_{n}_pasted.png)
    are never written over;
  - the copy and how it is placed live in {slot}_{n}.layout.json;
  - the finished artwork ({slot}_{n}.final.png / {slot}_{n}_pasted.final.png) is rendered
    from the two and can be rendered again at any time without an API call.

Line breaking keeps phrases whole (HYALURONIC ACID, DAY & NIGHT, 1.7 FL OZ / 50ML ...).
When copy cannot be laid out at a readable size the renderer raises LayoutOverflow instead
of cutting words letter by letter; the caller flags the slot for a layout change.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

LAYOUT_VERSION = 1

# Smallest body text the panel may use, in pixels on a 1024 canvas. Below this the slot is
# sent back for a layout change. (Thumbnail readability threshold is decided in phase 2.)
MIN_BODY_PX_1024 = 18

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


# ---------------------------------------------------------------- layout data
def make_layout(heading: str, rows: list[str], *, style: str = "dark_panel",
                keep: list[str] | None = None) -> dict:
    """The copy layer for one attempt. Phase 2 adds placement fields (product box, text zone)."""
    return {"version": LAYOUT_VERSION, "mode": "panel", "style": style,
            "heading": heading or "", "rows": [r for r in rows if r and r.strip()],
            "keep": list(dict.fromkeys(k for k in (keep or []) if k and k.strip()))}


def has_copy(layout: dict | None) -> bool:
    return bool(layout and (layout.get("heading") or layout.get("rows")))


# ---------------------------------------------------------------- rendering
def render_panel(source: Image.Image, layout: dict) -> Image.Image:
    """Current placement: approved copy on a panel under the photo (replaced in phase 2)."""
    source = source.convert("RGB")
    heading, rows = layout.get("heading", ""), layout.get("rows", [])
    keep = layout.get("keep", [])
    white = layout.get("style") == "white_panel"
    width, height = source.size
    unit = width / 1024
    pad = round(48 * unit)
    text_width = width - 2 * pad
    inset = max(4, round(23 * unit))
    vertical_pad = max(4, round(25 * unit))
    max_panel = round(height * .47)
    min_body = max(1, round(MIN_BODY_PX_1024 * unit))
    chosen = None
    last_error = ""
    for title_size in range(max(1, round(57 * unit)), 0, -1):
        body_size = max(1, round(title_size * 34 / 57))
        if body_size < min_body:
            break
        gap = max(3, round(title_size * .24))
        try:
            title = copy_block(heading, title_size, text_width, keep=keep)
            options = []
            for count in ((2, 1) if len(rows) > 1 else (1,)):
                cell_width = text_width // count
                try:
                    blocks = [copy_block(text, body_size, cell_width - inset - gap, bold=False, keep=keep)
                              for text in rows]
                except LayoutOverflow as e:
                    last_error = str(e)
                    continue
                grid = [blocks[i:i + count] for i in range(0, len(blocks), count)]
                panel = title.height + sum(max(block.height for block in row) for row in grid)
                panel += vertical_pad * 2 + gap * max(0, len(grid) - (0 if title.lines else 1))
                options.append((panel, cell_width, grid))
        except LayoutOverflow as e:
            last_error = str(e)
            continue
        if not options:
            continue
        panel_height, cell_width, grid = min(options, key=lambda item: item[0])
        if panel_height <= max_panel:
            chosen = (title, gap, panel_height, cell_width, grid)
            break
    if chosen is None:
        raise LayoutOverflow(last_error or "文案在可读字号下排不进这张图，需要调整构图或减少文案。")
    title, gap, panel_height, cell_width, grid = chosen

    photo_height = height - panel_height
    swatch = source.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    ink = (34, 33, 31) if white else (255, 255, 255)
    background = (255, 255, 255) if white else tuple(round(x * .25 + 14) for x in swatch)
    final = Image.new("RGB", (width, height), background)
    if panel_height <= height * .28:
        photo = ImageOps.fit(source, (width, photo_height), Image.Resampling.LANCZOS)
    else:
        # Taller panels leave the whole product visible.
        ImageDraw.Draw(final).rectangle((0, 0, width, photo_height), fill=(255, 255, 255) if white else swatch)
        photo = ImageOps.contain(source, (width, photo_height), Image.Resampling.LANCZOS)
    final.paste(photo, ((width - photo.width) // 2, (photo_height - photo.height) // 2))
    draw = ImageDraw.Draw(final)
    y = photo_height + vertical_pad
    title.draw(draw, pad, y, ink)
    if title.lines:
        y += title.height + gap
    for row in grid:
        for column, block in enumerate(row):
            x = pad + column * cell_width
            center = y + block.line_height // 3
            dot = max(1, min(round(5 * unit), block.line_height // 5))
            draw.ellipse((x, center - dot, x + 2 * dot, center + dot), fill=ink)
            block.draw(draw, x + inset, y, ink)
        y += max(block.height for block in row) + gap
    return final


def render_layout(photo_path: Path, layout: dict, out_path: Path) -> Path:
    """Render photo + copy layer into out_path. The photo file is only read."""
    with Image.open(photo_path) as source:
        source = ImageOps.exif_transpose(source)
        final = render_panel(source, layout)
    _atomic_save(final, out_path)
    return out_path


def _atomic_save(image: Image.Image, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
        temp = Path(output.name)
    try:
        image.save(temp, "PNG")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def compose_copy_panel(path: Path, heading: str, rows: list[str], *, white: bool = False,
                       keep: list[str] | None = None, out: Path | None = None) -> Path:
    """Backward-compatible helper: render copy onto `path` (or into `out`)."""
    layout = make_layout(heading, rows, style="white_panel" if white else "dark_panel", keep=keep)
    return render_layout(path, layout, out or path)
