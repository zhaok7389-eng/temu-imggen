"""Compare label text on source / generated / pasted images against the confirmed text."""
import json, sys
from pathlib import Path
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from imggen.qa.label_text import ocr_lines, compare

GT = {
 "A": ["Turmeric", "Serum", "Help regulate the skin's", "moisture-oil balance,", "making it appear more",
       "even and vibrant", "MOISTURIZES", "IMPROVE SKIN TEXTURE", "SMOOTH SKIN", "IMPROVES THE",
       "TEXTURE OF SKIN", "30ML / 1.0 FL.OZ"],
 "B": ["RETINOL CREAM", "Reduce wrinkles, fine lines and", "crow's feet.", "Replenis h moisture to the skin",
       "And unclog the pores.", "Replenishcollagen, tighten and lift", "Our skin.", "NET: 1.7fl.oz / 50g"],
 "C": ["INKEEY", "PROVEN BY SCIENCE", "COLLAGEN, RETINOL", "&HYALURONIC ACID", "ANTI-AGING & RECOVERY CREAM",
       "1.7 FL OZ / 50ML", "DAY & NIGHT"],
 "D": ["INKEEY", "DARK SPOT", "CORRECTOR CREAM", "CREME CORRECTRICE ANTI-TACHES",
       "CORRECT DARK SPOTS & DISCOLORATIONS", "4-BUTYLRESORCINOL & VITAMIN E", "-DESIGNEDINNEWYORK-",
       "MULTI-INTENSE ACTIVE BLENDING", "CONCENTRATE", "1.7 FL OZ / 50ML"],
}
SRC = {"A": "01-turmeric-serum/orange.jpg", "B": "02-zyzt-retinol-cream/main.png",
       "C": "11-inkeey-cream/collagen-retinol.jpg", "D": "11-inkeey-cream/dark-spot.png"}
gen_dir, paste_dir, sku_dir = map(Path, sys.argv[1:4])
names = sys.argv[4:]

def score(img, k):
    found = [t for t, c, b in ocr_lines(img)]
    r = compare(GT[k], found)
    sims = [x["sim"] for x in r]
    return dict(mean=round(sum(sims) / len(sims), 3), exact=sum(s >= 0.999 for s in sims), n=len(sims),
                worst=sorted(r, key=lambda x: x["sim"])[:2])

rows = []
for k, p in SRC.items():
    rows.append(dict(img="SOURCE_" + k, **score(cv2.imread(str(sku_dir / p)), k)))
for n in names:
    k = n.split("_")[1]
    g = cv2.imread(str(gen_dir / n)); pp = cv2.imread(str(paste_dir / n.replace(".png", "_pasted.png")))
    rows.append(dict(img=n + " [gen]", **score(g, k)))
    rows.append(dict(img=n + " [pasted]", **score(pp, k)))
for r in rows:
    print(r["img"][:40].ljust(40), "exact %d/%d" % (r["exact"], r["n"]), "mean", r["mean"],
          "| worst:", "; ".join("%s -> %s (%.2f)" % (w["expected"], w["found"], w["sim"]) for w in r["worst"]))
