"""Render approved copy on a dedicated strip outside the photographed area."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


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


def wrap_copy(text: str, font, width: int) -> list[str]:
    """Wrap by rendered width. Preserve every word and symbol, including long tokens."""
    lines = []
    for paragraph in text.splitlines():
        current, separator = "", ""
        for token in re.findall(r"\S+|\s+", paragraph):
            if token.isspace():
                separator = token
                continue
            candidate = current + separator + token if current else token
            if font.getlength(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = ""
                for char in token:
                    if current and font.getlength(current + char) > width:
                        lines.append(current)
                        current = ""
                    current += char
            separator = ""
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


def copy_block(text: str, size: int, width: int, *, bold: bool = True) -> CopyBlock:
    font = copy_font(size, bold)
    line_height = max(1, round(size * 1.18))
    return CopyBlock(wrap_copy(text, font, width), font, line_height)


def compose_copy_panel(path: Path, heading: str, rows: list[str], *, white: bool = False):
    with Image.open(path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
    width, height = source.size
    unit = width / 1024
    pad = round(48 * unit)
    text_width = width - 2 * pad
    inset = max(4, round(23 * unit))
    vertical_pad = max(4, round(25 * unit))
    max_panel = round(height * .47)
    for title_size in range(max(1, round(57 * unit)), 0, -1):
        gap = max(3, round(title_size * .24))
        body_size = max(1, round(title_size * 34 / 57))
        title = copy_block(heading, title_size, text_width)
        options = []
        for count in ((2, 1) if len(rows) > 1 else (1,)):
            cell_width = text_width // count
            blocks = [copy_block(text, body_size, cell_width - inset - gap, bold=False) for text in rows]
            grid = [blocks[i:i + count] for i in range(0, len(blocks), count)]
            panel = title.height + sum(max(block.height for block in row) for row in grid)
            panel += vertical_pad * 2 + gap * max(0, len(grid) - (0 if title.lines else 1))
            options.append((panel, cell_width, grid))
        panel_height, cell_width, grid = min(options, key=lambda item: item[0])
        if panel_height <= max_panel:
            break
    if panel_height >= height:
        raise ValueError("当前画布无法完整容纳文案，请改用多张副图展示。")

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

    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
        temp = Path(output.name)
    try:
        final.save(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
