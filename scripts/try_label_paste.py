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
from imggen.repair.label_paste import paste_label

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
args = ap.parse_args()
for kv in args.src:
    k, v = kv.split("=", 1); SRC[k] = v
gen_dir, sku_dir, out = Path(args.gen_dir), Path(args.sku_dir), Path(args.out)
out.mkdir(parents=True, exist_ok=True)
rows = []
for g in sorted(gen_dir.glob("T*_*.png")):
    letter = g.name.split("_")[1]
    if letter not in SRC:
        continue
    src = load(sku_dir / SRC[letter]); gen = load(g)
    t = time.time(); r = paste_label(src, gen); dt = time.time() - t
    cv2.imwrite(str(out / g.name.replace(".png", "_pasted.png")), r.image)
    s = 512
    sheet = np.hstack([label(cv2.resize(src, (s, s)), "source"),
                       label(cv2.resize(gen, (s, s)), "generated"),
                       label(cv2.resize(r.image, (s, s)), "pasted" if r.ok else "pasted (FAIL: %s)" % r.reason)])
    cv2.imwrite(str(out / g.name.replace(".png", "_cmp.jpg")), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    rows.append(dict(file=g.name, ok=r.ok, reason=r.reason, coverage=round(r.coverage, 2),
                     surfaces=[(s_.inliers, round(s_.reproj_err, 2)) for s_ in r.surfaces], sec=round(dt, 1)))
    print(rows[-1], flush=True)
(out / "summary.json").write_text(json.dumps(rows, indent=1))
