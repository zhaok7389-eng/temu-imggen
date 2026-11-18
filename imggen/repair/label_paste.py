"""Paste the original product label back onto a generated image.

Approach (no cutout model needed):
1. Product mask on the source photo (source photos are on white/plain backgrounds).
2. SIFT features on source product vs generated image, ratio-test matching.
3. Sequential RANSAC: find up to N planar surfaces (box face, bottle front ...),
   each with its own homography.
4. For each surface: warp the source pixels inside that surface's region, match
   colour/lighting to the generated image, feather the edge, and blend.
The text pixels come straight from the source photo, so label text cannot change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Surface:
    H: np.ndarray            # source -> generated homography
    src_poly: np.ndarray     # region on source (convex hull of inliers, dilated)
    inliers: int
    reproj_err: float


@dataclass
class PasteResult:
    image: np.ndarray
    surfaces: list[Surface] = field(default_factory=list)
    coverage: float = 0.0    # share of source product area that was pasted
    ok: bool = False
    reason: str = ""
    status: str = "failed"          # applied | not_applied | failed
    outside_change: float = 0.0     # share of image changed outside the paste zones
    zone: np.ndarray | None = None  # generated-image mask of the pasted label zones (for the guard)
    line_align: list = field(default_factory=list)  # per pasted text line: gradient NCC source vs generated


def source_mask(src: np.ndarray, thr: int = 18) -> np.ndarray:
    """Product mask for a photo on a light background.

    The background colour is estimated per row from the left/right borders, so a
    studio floor that darkens towards the bottom (a grey gradient) is not taken for
    product. (v0.1 used one global border colour and swallowed the whole floor of
    the INKEEY collagen photo, which pasted white blocks beside the jar.)"""
    h, w = src.shape[:2]
    b = max(5, w // 50)
    side = np.concatenate([src[:, :b], src[:, -b:]], axis=1).astype(np.float32)
    row_bg = np.median(side, axis=1)
    row_bg = cv2.blur(row_bg.reshape(h, 1, 3), (1, max(3, h // 100))).reshape(h, 3)
    diff = np.abs(src.astype(np.float32) - row_bg[:, None, :]).max(axis=2)
    m = (diff > thr).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    # fill holes
    ff = m.copy()
    cv2.floodFill(ff, None, (0, 0), 255)
    m = m | cv2.bitwise_not(ff)
    # keep large components; drop shadows/reflections below the product
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] > 0.01 * h * w:
            keep[lab == i] = 255
    return keep


def text_mask(shape, boxes) -> np.ndarray:
    """Filled OCR text boxes (source coordinates)."""
    m = np.zeros(shape[:2], np.uint8)
    for b in boxes:
        cv2.fillPoly(m, [np.asarray(b, np.float32).reshape(-1, 2).astype(np.int32)], 255)
    return m


def _label_zone(text, region, smask_in, pad):
    """Area to paste for one surface: hull of the label text that falls in this
    surface's region, padded a little, kept inside the (eroded) product.
    Caps, collars, box edges and floor shadows carry no text and stay generated."""
    t = text & region
    if not t.any():
        return np.zeros_like(region)
    hull = cv2.convexHull(cv2.findNonZero(t))
    z = np.zeros_like(region)
    cv2.fillConvexPoly(z, hull, 255)
    z = cv2.dilate(z, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pad + 1,) * 2))
    return z & region & smask_in


def _match(src_gray, src_mask, gen_gray, ratio=0.75):
    sift = cv2.SIFT_create(nfeatures=8000, contrastThreshold=0.02)
    k1, d1 = sift.detectAndCompute(src_gray, src_mask)
    k2, d2 = sift.detectAndCompute(gen_gray, None)
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return np.zeros((0, 2)), np.zeros((0, 2))
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
    raw = flann.knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in raw if len(p) == 2) if m.distance < ratio * n.distance]
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    return p1, p2


def _sane(H, src_shape, gen_shape):
    """Reject degenerate or extreme homographies."""
    A = H[:2, :2] / H[2, 2]
    det = np.linalg.det(A)
    if det <= 0:
        return False
    scale = np.sqrt(det) * max(src_shape[:2]) / max(gen_shape[:2])
    if not (0.08 < scale < 3.0):
        return False
    if abs(H[2, 0] / H[2, 2]) > 0.0015 or abs(H[2, 1] / H[2, 2]) > 0.0015:
        return False
    sx, sy = np.linalg.svd(A, compute_uv=False)
    return sx / max(sy, 1e-6) < 2.5  # no strong shear/anisotropy


def find_surfaces(src, src_mask, gen, max_surfaces=3, min_inliers=40, min_extra_inliers=80):
    s_gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    g_gray = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY)
    p1, p2 = _match(s_gray, src_mask, g_gray)
    surfaces = []
    remaining = np.ones(len(p1), bool)
    for _ in range(max_surfaces):
        idx = np.where(remaining)[0]
        if len(idx) < min_inliers:
            break
        H, inl = cv2.findHomography(p1[idx], p2[idx], cv2.USAC_MAGSAC, 4.0,
                                    maxIters=5000, confidence=0.999)
        if H is None:
            break
        inl = inl.ravel().astype(bool)
        need = min_inliers if not surfaces else min_extra_inliers
        if inl.sum() < need or not _sane(H, src.shape, gen.shape):
            break
        sel = idx[inl]
        proj = cv2.perspectiveTransform(p1[sel].reshape(-1, 1, 2), H).reshape(-1, 2)
        err = float(np.mean(np.linalg.norm(proj - p2[sel], axis=1)))
        hull = cv2.convexHull(p1[sel].astype(np.int32))
        surfaces.append(Surface(H=H, src_poly=hull, inliers=int(inl.sum()), reproj_err=err))
        remaining[sel] = False
        # drop leftover matches whose source point lies inside this hull
        for j in np.where(remaining)[0]:
            if cv2.pointPolygonTest(hull, tuple(map(float, p1[j])), False) >= 0:
                remaining[j] = False
    return surfaces


def _match_colour(patch, target, mask):
    """Per-channel mean/std transfer in LAB, measured inside mask."""
    m = mask > 0
    if m.sum() < 100:
        return patch
    a = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
    for c in range(3):
        ma, sa = a[..., c][m].mean(), a[..., c][m].std() + 1e-3
        mb, sb = b[..., c][m].mean(), b[..., c][m].std() + 1e-3
        # partial transfer: keep label contrast, adopt scene lighting
        k = 0.6
        a[..., c] = (a[..., c] - ma) * ((1 - k) + k * sb / sa) + ((1 - k) * ma + k * mb)
    return cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def _agreement(warped, gen, mask, hull_mask, block=24, min_ncc=0.25, flat_std=6.0):
    """Keep only blocks where pasted source and generated image line up.
    Textured blocks need gradient correlation; flat blocks are kept only inside
    the matched-feature hull (so a missing box is not hallucinated back)."""
    gw = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gg = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY).astype(np.float32)
    def grad(x):
        x = cv2.GaussianBlur(x, (0, 0), 1.5)
        return cv2.magnitude(cv2.Sobel(x, cv2.CV_32F, 1, 0), cv2.Sobel(x, cv2.CV_32F, 0, 1))
    a, b = grad(gw), grad(gg)
    keep = np.zeros(mask.shape, np.uint8)
    H, W = mask.shape
    for y in range(0, H, block):
        for x in range(0, W, block):
            m = mask[y:y+block, x:x+block] > 0
            if m.mean() < 0.3:
                continue
            ga, gb = a[y:y+block, x:x+block][m], b[y:y+block, x:x+block][m]
            if ga.std() < flat_std and gb.std() < flat_std:
                ok = hull_mask[y:y+block, x:x+block].mean() > 127
            else:
                ncc = float(np.corrcoef(ga, gb)[0, 1]) if ga.std() > 0 and gb.std() > 0 else 0.0
                ok = ncc > min_ncc
            if ok:
                keep[y:y+block, x:x+block] = 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((block * 2 + 1,) * 2, np.uint8))
    return keep & mask


def _occluders(warped, gen, mask, thr=40, min_blob_frac=0.0012):
    """Large solid regions where the generated image differs a lot from the pasted label
    (a thumb over the bottle, a dropper, a leaf). Thin text strokes are removed by the
    morphological opening, so text differences are still replaced."""
    d = cv2.absdiff(cv2.GaussianBlur(warped, (0, 0), 2), cv2.GaussianBlur(gen, (0, 0), 2)).max(axis=2)
    m = ((d > thr) & (mask > 0)).astype(np.uint8) * 255
    k = max(5, int(0.012 * max(gen.shape[:2])) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    out = np.zeros_like(m)
    area = gen.shape[0] * gen.shape[1]
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > min_blob_frac * area:
            out[lab == i] = 255
    return cv2.dilate(out, np.ones((7, 7), np.uint8))


def _grad(gray):
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.0)
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def line_alignment(src_bgr, gen_bgr, plans, text_boxes, keep_boxes=False):
    """For each label text line that will be pasted: does the warped source line sit on top of
    the line the model drew? Gradient NCC inside the warped line box. A low value means the
    paste puts the line somewhere the generated image has other strokes -> ghosting."""
    gh, gw = gen_bgr.shape[:2]
    gg = _grad(cv2.cvtColor(gen_bgr, cv2.COLOR_BGR2GRAY))
    out = []
    warped_cache = {}
    for b in text_boxes:
        poly = np.asarray(b, np.float32).reshape(-1, 2)
        cx, cy = poly.mean(axis=0)
        owner = None
        for k, (s, _part, region) in enumerate(plans):
            if region[int(np.clip(cy, 0, region.shape[0] - 1)), int(np.clip(cx, 0, region.shape[1] - 1))]:
                owner = k
                break
        if owner is None:
            continue
        s = plans[owner][0]
        if owner not in warped_cache:
            w = cv2.warpPerspective(src_bgr, s.H, (gw, gh), flags=cv2.INTER_LINEAR)
            warped_cache[owner] = _grad(cv2.cvtColor(w, cv2.COLOR_BGR2GRAY))
        q = cv2.perspectiveTransform(poly.reshape(-1, 1, 2), s.H).reshape(-1, 2)
        m = np.zeros((gh, gw), np.uint8)
        cv2.fillConvexPoly(m, q.astype(np.int32), 255)
        sel = m > 0
        if sel.sum() < 30:
            continue
        a, g = warped_cache[owner][sel], gg[sel]
        ncc = float(np.corrcoef(a, g)[0, 1]) if a.std() > 1e-3 and g.std() > 1e-3 else 0.0
        h = float(np.linalg.norm(q[0] - q[3])) if len(q) == 4 else 0.0
        out.append((round(ncc, 3), round(h, 1), b) if keep_boxes else (round(ncc, 3), round(h, 1)))
    return out


def _drop_misaligned(src_bgr, gen_bgr, plan, text_boxes, pad, thr=0.3):
    """Remove label lines that would not sit on the generated line (ghosting) from a plan's
    zone, together with their surroundings. Returns (new plan or None, [(ncc, height)])."""
    s, part, region = plan
    al = line_alignment(src_bgr, gen_bgr, [plan], text_boxes, keep_boxes=True)
    bad = np.zeros_like(region)
    for ncc, _h, b in al:
        if ncc < thr:
            cv2.fillPoly(bad, [np.asarray(b, np.float32).reshape(-1, 2).astype(np.int32)], 255)
    if bad.any():
        k = 4 * pad + 1
        region = region & ~cv2.dilate(bad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    stats = [(n, h) for n, h, _b in al]
    return ((s, part, region) if region.any() else None), stats


def _paste_surface(src_bgr, smask, region, s, gen_bgr, out, exclude=None):
    """Warp one source region onto the output.
    Returns (out, region actually pasted in source coords, pasted mask in generated coords)."""
    gh, gw = gen_bgr.shape[:2]
    sh, sw = smask.shape
    warped = cv2.warpPerspective(src_bgr, s.H, (gw, gh), flags=cv2.INTER_CUBIC)
    wmask = cv2.warpPerspective(region, s.H, (gw, gh), flags=cv2.INTER_LINEAR)
    wmask = cv2.erode(wmask, np.ones((3, 3), np.uint8))
    if exclude is not None:
        wmask = cv2.bitwise_and(wmask, cv2.bitwise_not(exclude))
    warped = _match_colour(warped, gen_bgr, wmask)
    hull_src = np.zeros_like(smask)
    cv2.fillConvexPoly(hull_src, s.src_poly, 255)
    hull_src = cv2.dilate(hull_src, np.ones((25, 25), np.uint8))
    hull_gen = cv2.warpPerspective(hull_src, s.H, (gw, gh))
    wmask = _agreement(warped, gen_bgr, wmask, hull_gen)
    wmask = cv2.bitwise_and(wmask, cv2.bitwise_not(_occluders(warped, gen_bgr, wmask)))
    done = cv2.warpPerspective(wmask, np.linalg.inv(s.H), (sw, sh))
    done = (done > 127).astype(np.uint8) * 255
    feather = cv2.GaussianBlur(wmask, (0, 0), 2).astype(np.float32)[..., None] / 255.0
    out = (warped * feather + out * (1 - feather)).astype(np.uint8)
    return out, done, wmask


def paste_label(src_bgr: np.ndarray, gen_bgr: np.ndarray, text_boxes=None, max_err: float = 3.5,
                min_coverage: float | None = None, min_extra_inliers: int = 80,
                second_pass_min_inliers: int = 40) -> PasteResult:
    """text_boxes: OCR boxes of the label text on the source photo (see source_text_boxes).
    With boxes, only the label zone (text hull, padded) is pasted and coverage is the share
    of label text restored. Without boxes the whole product face is pasted (v0.1 behaviour,
    kept for tests and as a fallback)."""
    cv2.setRNGSeed(0)                         # RANSAC: same input, same result
    if min_coverage is None:
        # label-text coverage: a missing box leaves half the text unrestored (T2_A, T6_A_2: 0.50)
        min_coverage = 0.7 if text_boxes is not None else 0.35
    gh, gw = gen_bgr.shape[:2]
    smask = source_mask(src_bgr)
    sh, sw = smask.shape
    L = max(sh, sw)
    surfaces = find_surfaces(src_bgr, smask, gen_bgr, min_extra_inliers=min_extra_inliers)
    out = gen_bgr.copy()

    def fail(reason, used=(), cov=0.0):
        return PasteResult(gen_bgr.copy(), list(used), cov, False, reason, "failed", 0.0)

    if not surfaces:
        return fail("no matching surface")
    good = [s for s in surfaces if s.reproj_err <= max_err]
    if not good:
        return fail("surfaces failed error check")

    e = max(3, int(0.005 * L)) | 1
    smask_in = cv2.erode(smask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (e, e)))
    if text_boxes is not None:
        text = text_mask(smask.shape, text_boxes) & smask_in
        if not text.any():
            return fail("no label text on source")
        target = text                       # coverage = share of label text restored
    else:
        text = None
        target = smask

    # Split the product between surfaces: each pixel goes to the nearest surface hull
    # (within a reach limit), so a seam never runs through the label text.
    reach = 0.12 * L
    dists = []
    for s in good:
        hull_img = np.full((sh, sw), 255, np.uint8)
        cv2.fillConvexPoly(hull_img, s.src_poly, 0)
        dists.append(cv2.distanceTransform(hull_img, cv2.DIST_L2, 5))
    dists = np.stack(dists)
    owner = dists.argmin(axis=0)
    near = dists.min(axis=0) <= reach
    pad = max(4, int(0.015 * L))
    plans = []                                # (surface, object part, label zone), source coords
    for i, s in enumerate(good):
        part = ((owner == i) & near & (smask > 0)).astype(np.uint8) * 255
        region = _label_zone(text, part, smask_in, pad) if text is not None else part & smask_in
        if region.any():
            plans.append((s, part, region))
    align = []
    if text_boxes is not None:
        kept = []
        for pl in plans:
            pl2, st_ = _drop_misaligned(src_bgr, gen_bgr, pl, text_boxes, pad)
            align += st_
            if pl2 is not None:
                kept.append(pl2)
        plans = kept

    # Dry run of the first pass, only to see which label parts are still unrestored.
    first_done = np.zeros_like(smask)
    for s, part, region in plans:
        _o, done, _w = _paste_surface(src_bgr, smask, region, s, gen_bgr, gen_bgr.copy())
        first_done |= done

    # Second pass: label parts that found no surface (typically a small, tilted bottle
    # held in a hand, or the front sachet of a mask box). Match each leftover product part
    # on its own against an upscaled generated image, where its features are big enough.
    left_text = target & ~cv2.dilate(first_done, np.ones((15, 15), np.uint8))
    if text is not None:
        # leftover label = clusters of unrestored text (e.g. the bottle's label when the
        # first surface was the box); matching only there finds that object's own geometry
        kc = max(15, int(0.05 * L)) | 1
        left = cv2.dilate(left_text, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kc, kc))) & smask
    else:
        left = smask & ~cv2.dilate(first_done, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(left)
    up = 2.0
    gen_up = None
    for k in range(1, n):
        comp = (lab == k).astype(np.uint8) * 255
        if text is None and st[k, cv2.CC_STAT_AREA] < 0.08 * smask.sum() / 255:
            continue
        if text is not None and (comp & left_text).sum() < 0.05 * text.sum():
            continue                         # no label text worth restoring on this part
        x, y, w, h = st[k, :4]
        m = 20
        x0, y0 = max(0, x - m), max(0, y - m)
        x1, y1 = min(sw, x + w + m), min(sh, y + h + m)
        crop, cmask = src_bgr[y0:y1, x0:x1], comp[y0:y1, x0:x1]
        if gen_up is None:
            gen_up = cv2.resize(gen_bgr, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        cands = find_surfaces(crop, cmask, gen_up, max_surfaces=1, min_inliers=second_pass_min_inliers)
        if not cands or cands[0].reproj_err > max_err * up:
            continue
        c = cands[0]
        T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
        S = np.array([[1 / up, 0, 0], [0, 1 / up, 0], [0, 0, 1]], np.float64)
        H = S @ c.H @ T
        if not _sane(H, src_bgr.shape, gen_bgr.shape):
            continue
        poly = (c.src_poly.reshape(-1, 2) + [x0, y0]).astype(np.int32).reshape(-1, 1, 2)
        s2 = Surface(H=H, src_poly=poly, inliers=c.inliers, reproj_err=c.reproj_err / up)
        hull = np.zeros_like(smask)
        cv2.fillConvexPoly(hull, poly, 255)
        reg = comp & cv2.dilate(hull, np.ones((31, 31), np.uint8)) & smask_in  # only around what really matched
        if text is not None:
            reg = _label_zone(text, reg, smask_in, pad)
        if reg.any() and text_boxes is not None:
            pl2, st_ = _drop_misaligned(src_bgr, gen_bgr, (s2, comp, reg), text_boxes, pad)
            align += st_
            if pl2 is not None:
                plans.append(pl2)
        elif reg.any():
            plans.append((s2, comp, reg))

    # No pasting across objects (box vs sachets, box vs bottle): when the generated image
    # arranges the objects differently from the source, one surface's zone lands on another
    # object. Where a zone falls on another surface's object part, neither is pasted there.
    parts_gen = [cv2.warpPerspective(part, s.H, (gw, gh)) for s, part, _ in plans]
    mg = max(5, int(0.01 * max(gh, gw))) | 1
    out = gen_bgr.copy()
    pasted_src = np.zeros_like(smask)
    zone_gen = np.zeros((gh, gw), np.uint8)
    used = []
    for i, (s, part, region) in enumerate(plans):
        others = np.zeros((gh, gw), np.uint8)
        for j, pg in enumerate(parts_gen):
            if j != i:
                others |= pg
        excl = cv2.dilate(others, np.ones((mg, mg), np.uint8)) if others.any() else None
        out, done, wm = _paste_surface(src_bgr, smask, region, s, gen_bgr, out, exclude=excl)
        if done.any():
            pasted_src |= done
            zone_gen |= wm
            used.append(s)

    cov = float((pasted_src & target).sum()) / max(float(target.sum()), 1.0)
    if not used:
        return fail("nothing pasted", cov=cov)
    # Out-of-bounds check: nothing may change outside the warped paste zones
    # (the 2px feather is allowed for).
    changed = cv2.absdiff(out, gen_bgr).max(axis=2) > 6
    allowed = cv2.dilate(zone_gen, np.ones((9, 9), np.uint8)) > 0
    outside = float((changed & ~allowed).sum()) / (gh * gw)
    if not changed.any():
        return PasteResult(gen_bgr.copy(), used, cov, False, "paste changed nothing", "not_applied", 0.0)
    if outside > 0.002:
        return PasteResult(gen_bgr.copy(), used, cov, False, "paste spilled outside zone %.4f" % outside,
                           "failed", outside)
    if cov < min_coverage:
        return PasteResult(gen_bgr.copy(), used, cov, False, ("label text only partly found %.2f (part of the product missing?)" % cov), "failed", outside)
    # A failed paste is never handed on: the caller regenerates instead.
    return PasteResult(out, used, cov, True, "", "applied", outside,
                       cv2.dilate(zone_gen, np.ones((15, 15), np.uint8)), align)


def source_text_boxes(src_bgr: np.ndarray):
    """OCR boxes of the label text on a source photo (needs rapidocr).
    Cache per SKU: the source photo never changes."""
    from imggen.qa.label_text import ocr_lines
    return [b for _t, _c, b in ocr_lines(src_bgr, upscale=1.0)]
