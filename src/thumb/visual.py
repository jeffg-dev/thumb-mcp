"""Small local OpenCV operations; no model calls or semantic guesses."""
import cv2
import numpy as np

# Tiny images do not benefit from a large native worker pool.
cv2.setNumThreads(1)


def app_gray(image):
    # Exclude clock, rounded borders and home indicator. These should not
    # determine whether app content has loaded or changed.
    w, h = image.size
    crop = image.crop((int(w*.04), int(h*.12), int(w*.96), int(h*.94)))
    return cv2.resize(np.asarray(crop.convert('L')), (180, 300), interpolation=cv2.INTER_AREA)


def is_blank(image) -> bool:
    gray = app_gray(image)
    edges = cv2.Canny(gray, 40, 100)
    return np.count_nonzero(edges) / edges.size < .001 and float(gray.std()) < 8


def changed_fraction(before, after) -> float:
    delta = cv2.absdiff(app_gray(before), app_gray(after))
    # Suppress isolated compression noise; report the fraction of app pixels
    # with a meaningful luminance difference, not just a whole-frame average.
    mask = cv2.threshold(delta, 12, 255, cv2.THRESH_BINARY)[1]
    return float(np.count_nonzero(mask) / mask.size)


def pixels_match(before, after, *, max_fraction: float = .01, max_mean: float = 1.5) -> bool:
    """Tolerate capture noise, while preserving color-sensitive control changes.

    Inputs are already in the same coordinate space. Do not blur/downsample:
    a small changed switch or target must remain visible to this comparison.
    """
    if before.size != after.size:
        return False
    delta = cv2.absdiff(np.asarray(before.convert('RGB')), np.asarray(after.convert('RGB')))
    changed = np.any(delta > 12, axis=2)
    return float(changed.mean()) <= max_fraction and float(delta.mean()) <= max_mean
