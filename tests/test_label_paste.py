"""Synthetic check: a generated image whose label text was 'corrected' gets the original back."""
import cv2, numpy as np
from imggen.repair.label_paste import paste_label


def _product(text_line):
    img = np.full((800, 800, 3), 255, np.uint8)
    cv2.rectangle(img, (250, 150), (550, 700), (60, 120, 230), -1)           # box
    rng = np.random.default_rng(0)
    for _ in range(300):                                                    # texture for features
        x, y = rng.integers(260, 540), rng.integers(160, 690)
        cv2.circle(img, (int(x), int(y)), int(rng.integers(2, 6)), tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    cv2.putText(img, "Turmeric", (270, 260), cv2.FONT_HERSHEY_DUPLEX, 1.6, (255, 255, 255), 3)
    cv2.putText(img, text_line, (270, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return img


def test_paste_restores_original_text():
    src = _product("Replenis h moisture")
    wrong = _product("Replenish moisture")                                  # the model 'fixed' the typo
    M = cv2.getRotationMatrix2D((400, 400), 4, 0.8)
    M[:, 2] += (60, 40)
    scene = np.full((800, 800, 3), (180, 210, 235), np.uint8)
    gen = cv2.warpAffine(wrong, M, (800, 800), borderValue=(180, 210, 235))
    r = paste_label(src, gen)
    assert r.ok, r.reason
    expected = cv2.warpAffine(src, M, (800, 800), borderValue=(180, 210, 235))
    # compare the text line area: pasted must be much closer to the original than the generated was
    diff = np.abs(gen.astype(int) - expected.astype(int)).max(axis=2) > 40   # where the texts differ
    assert diff.sum() > 200
    err_before = np.abs(gen.astype(int) - expected.astype(int))[diff].mean()
    err_after = np.abs(r.image.astype(int) - expected.astype(int))[diff].mean()
    assert err_after < 0.5 * err_before, (err_before, err_after)


def test_no_match_returns_untouched():
    src = _product("abc")
    gen = np.full((800, 800, 3), 128, np.uint8)
    r = paste_label(src, gen)
    assert not r.ok and np.array_equal(r.image, gen)
