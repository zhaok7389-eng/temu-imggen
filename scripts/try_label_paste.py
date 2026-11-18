"""Run label repaste over a folder of generated images and write comparison sheets.

usage:
  python scripts/try_label_paste.py GEN_DIR SKU_DIR OUT_DIR [--src A=path/to/A.jpg --src B=...]

GEN_DIR holds images named like T1_A_25flare_med_1.png (letter = product).
--src overrides where each product's source photo is (absolute, or relative to SKU_DIR).
"""
import json, sys, time
from pathlib import Path
import cv2, numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from imggen.repair.label_paste import paste_label, source_text_boxes
from imggen.repair import paste_guard
from imggen.qa.label_text import ocr_lines

SRC = {  # product letter in GPT test -> source photo (relative to SKU_DIR)
    "A": "01-turmeric-serum/orange.jpg",
    "B": "02-zyzt-retinol-cream/main.png",
    "C": "11-inkeey-cream/collagen-retinol.jpg",
    "D": "11-inkeey-cream/dark-spot.png",
    "E": "06-collagen-sheet-mask/white-gold.jpg",
}

def load(p):
    im = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if im.ndim == 3 and im.shape[2] == 4:  # flatten alpha on white
        a = im[..., 3:4] / 255.0
        im = (im[..., :3] * a + 255 * (1 - a)).astype(np.uint8)
    return im

def label(img, text):
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4)
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return img

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("gen_dir"); ap.add_argument("sku_dir"); ap.add_argument("out")
ap.add_argument("--src", action="append", default=[], help="LETTER=path")
ap.add_argument("--no-guard", action="store_true", help="skip the before/after OCR guard")
args = ap.parse_args()
for kv in args.src:
    k, v = kv.split("=", 1); SRC[k] = v
gen_dir, sku_dir, out = Path(args.gen_dir), Path(args.sku_dir), Path(args.out)
out.mkdir(parents=True, exist_ok=True)
rows = []
srcs = {}   # letter -> (image, text boxes, expected lines); OCR once per product
for g in sorted(gen_dir.glob("T*_*.png")):
    letter = g.name.split("_")[1]
    if letter not in SRC:
        continue
    if letter not in srcs:
        im = load(sku_dir / SRC[letter])
        lines = ocr_lines(im, upscale=1.0)
        srcs[letter] = (im, [b for _t, _c, b in lines], [t for t, _c, _b in lines])
    src, boxes, expected = srcs[letter]
    gen = load(g)
    t = time.time(); r = paste_label(src, gen, text_boxes=boxes)
    guard = None
    if r.ok and not args.no_guard:
        guard = paste_guard.check(gen, r.image, expected, zone=r.zone)
        if not guard.keep:
            r.ok, r.status, r.reason, r.image = False, "failed", guard.reason, gen.copy()
    dt = time.time() - t
    cv2.imwrite(str(out / g.name.replace(".png", "_pasted.png")), r.image)
    s = 512
    tag = "pasted" if r.ok else "%s: %s" % (r.status.upper(), r.reason)
    sheet = np.hstack([label(cv2.resize(src, (s, s)), "source"),
                       label(cv2.resize(gen, (s, s)), "generated"),
                       label(cv2.resize(r.image, (s, s)), tag[:48])])
    cv2.imwrite(str(out / g.name.replace(".png", "_cmp.jpg")), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    row = dict(file=g.name, ok=r.ok, status=r.status, reason=r.reason, coverage=round(r.coverage, 2),
               outside=round(r.outside_change, 4),
               surfaces=[(s_.inliers, round(s_.reproj_err, 2)) for s_ in r.surfaces], sec=round(dt, 1))
    if guard:
        row.update(text_before=round(guard.before.mean_sim, 3), text_after=round(guard.after.mean_sim, 3),
                   repeats=(guard.before.repeats, guard.after.repeats))
    rows.append(row)
    print(row, flush=True)
(out / "summary.json").write_text(json.dumps(rows, indent=1))
