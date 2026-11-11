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


def source_mask(src: np.ndarray, thr: int = 18) -> np.ndarray:
    """Product mask for a photo on a near-uniform light background."""
    h, w = src.shape[:2]
    border = np.concatenate([src[:5].reshape(-1, 3), src[-5:].reshape(-1, 3),
                             src[:, :5].reshape(-1, 3), src[:, -5:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    diff = np.abs(src.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
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


def _occluders(warped, gen, mask, thr=45, min_blob_frac=0.004):
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


def paste_label(src_bgr: np.ndarray, gen_bgr: np.ndarray, max_err: float = 3.5,
                min_coverage: float = 0.35) -> PasteResult:
    gh, gw = gen_bgr.shape[:2]
    smask = source_mask(src_bgr)
    surfaces = find_surfaces(src_bgr, smask, gen_bgr)
    out = gen_bgr.copy()
    if not surfaces:
        return PasteResult(out, [], 0.0, False, "no matching surface")
    # Split the whole product mask between surfaces: each mask pixel goes to the
    # nearest surface hull (within a reach limit). Pasting the full face of each
    # surface avoids a seam running through the label text.
    good = [s for s in surfaces if s.reproj_err <= max_err]
    if not good:
        return PasteResult(out, [], 0.0, False, "surfaces failed error check")
    sh, sw = smask.shape
    reach = 0.12 * max(sh, sw)
    dists = []
    for s in good:
        hull_img = np.full((sh, sw), 255, np.uint8)
        cv2.fillConvexPoly(hull_img, s.src_poly, 0)
        dists.append(cv2.distanceTransform(hull_img, cv2.DIST_L2, 5))
    dists = np.stack(dists)
    owner = dists.argmin(axis=0)
    near = dists.min(axis=0) <= reach
    pasted_src = np.zeros_like(smask)
    used = []
    for i, s in enumerate(good):
        region = ((owner == i) & near & (smask > 0)).astype(np.uint8) * 255
        if region.sum() == 0:
            continue
        warped = cv2.warpPerspective(src_bgr, s.H, (gw, gh), flags=cv2.INTER_CUBIC)
        wmask = cv2.warpPerspective(region, s.H, (gw, gh), flags=cv2.INTER_LINEAR)
        wmask = cv2.erode(wmask, np.ones((3, 3), np.uint8))
        warped = _match_colour(warped, gen_bgr, wmask)
        hull_src = np.zeros_like(smask)
        cv2.fillConvexPoly(hull_src, s.src_poly, 255)
        hull_src = cv2.dilate(hull_src, np.ones((25, 25), np.uint8))
        hull_gen = cv2.warpPerspective(hull_src, s.H, (gw, gh))
        wmask = _agreement(warped, gen_bgr, wmask, hull_gen)
        wmask = cv2.bitwise_and(wmask, cv2.bitwise_not(_occluders(warped, gen_bgr, wmask)))
        pasted_px = float((wmask > 127).sum())
        region = cv2.warpPerspective(wmask, np.linalg.inv(s.H), (sw, sh))  # what was really pasted, in source coords
        region = (region > 127).astype(np.uint8) * 255
        feather = cv2.GaussianBlur(wmask, (0, 0), 2).astype(np.float32)[..., None] / 255.0
        out = (warped * feather + out * (1 - feather)).astype(np.uint8)
        pasted_src |= region
        used.append(s)
    cov = float(pasted_src.sum()) / max(float(smask.sum()), 1.0)
    ok = bool(used) and cov >= min_coverage
    reason = "" if ok else ("low coverage %.2f" % cov if used else "surfaces failed error check")
    # A failed paste is never handed on: the caller regenerates instead.
    return PasteResult(out if ok else gen_bgr.copy(), used, cov, ok, reason)
