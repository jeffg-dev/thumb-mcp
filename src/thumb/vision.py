"""On-screen text recognition, so shortcuts can aim at *words* instead of pixels.

Every fragile thing in this codebase comes from not knowing what is on screen:
hard-coded tile coordinates, colour-sniffing for buttons, brightness thresholds
to guess whether a dialog is up. Reading the text and its position removes the
guesswork -- ``tap_text("Wallet")`` beats computing where Wallet probably is.

Uses Apple's Vision framework locally: no API key, no network, no upload.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import Quartz
import Vision
from PIL import Image as PILImage

# VNRequestTextRecognitionLevel
LEVEL_ACCURATE = 0
LEVEL_FAST = 1


@dataclass(frozen=True)
class TextElement:
    """A recognised string and where to tap it, in device points."""

    text: str
    x: float  # centre
    y: float
    width: float
    height: float
    confidence: float

    def __str__(self) -> str:
        return f"{self.text!r} at ({self.x:.0f}, {self.y:.0f})"


def _cgimage_from_pil(image: PILImage.Image):
    """PIL -> CGImage without a PNG round-trip.

    Encoding to PNG and re-decoding costs more than the recognition itself on
    small frames, so hand Vision the raw pixels directly.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    data = rgba.tobytes()
    provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
    colorspace = Quartz.CGColorSpaceCreateDeviceRGB()
    return Quartz.CGImageCreate(
        width, height, 8, 32, width * 4, colorspace,
        Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrderDefault,
        provider, None, False, Quartz.kCGRenderingIntentDefault,
    )


def recognize(
    image: PILImage.Image,
    device_w: float,
    device_h: float,
    accurate: bool | None = None,
    min_confidence: float = 0.3,
) -> list[TextElement]:
    """Recognise every text element, returning positions in device points."""
    if accurate is None:
        # Accurate by default: fast mode misreads text often enough to matter
        # when the result is used to aim a tap ("Ishan" came back as "Ish8n"),
        # and it only costs ~125ms. Set THUMB_OCR_FAST=1 to trade back.
        accurate = os.environ.get("THUMB_OCR_FAST", "").lower() not in ("1", "true", "yes")

    cgimage = _cgimage_from_pil(image)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(LEVEL_ACCURATE if accurate else LEVEL_FAST)
    request.setUsesLanguageCorrection_(False)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, {})
    ok, _err = handler.performRequests_error_([request], None)
    if not ok:
        return []

    elements: list[TextElement] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        confidence = float(candidate.confidence())
        if confidence < min_confidence:
            continue
        box = observation.boundingBox()
        # Vision is normalised with a bottom-left origin; the device space is
        # top-left, so flip Y.
        cx = (box.origin.x + box.size.width / 2) * device_w
        cy = (1.0 - (box.origin.y + box.size.height / 2)) * device_h
        elements.append(
            TextElement(
                text=str(candidate.string()),
                x=cx,
                y=cy,
                width=box.size.width * device_w,
                height=box.size.height * device_h,
                confidence=confidence,
            )
        )
    elements.sort(key=lambda e: (round(e.y / 8), e.x))
    return elements


def find(elements: list[TextElement], query: str) -> list[TextElement]:
    """Elements matching a query, best match first.

    Ranked exact match, then prefix, then substring -- so "Wallet" prefers the
    button labelled exactly "Wallet" over "Wallet balance".
    """
    needle = query.strip().lower()
    scored: list[tuple[int, float, TextElement]] = []
    for element in elements:
        haystack = element.text.strip().lower()
        if haystack == needle:
            rank = 0
        elif haystack.startswith(needle):
            rank = 1
        elif needle in haystack:
            rank = 2
        else:
            continue
        scored.append((rank, -element.confidence, element))
    scored.sort(key=lambda item: (item[0], item[1], item[2].y, item[2].x))
    return [element for _rank, _conf, element in scored]
