"""Compact observations and conservative screen-bound OCR references."""
from __future__ import annotations

import hashlib
import secrets
from collections import OrderedDict
from dataclasses import dataclass

from . import vision, visual
from PIL import Image
from .errors import MirrorError
from .mirror import Frame


def fingerprint(frame: Frame) -> str:
    # Ignore only the status bar clock/battery. Keep the full app content at
    # native resolution: small controls changing must invalidate references.
    image = frame.image
    content = image.crop((0, int(image.height * 0.06), image.width, image.height))
    geometry = (image.size, frame.device_w, frame.device_h, frame.window.window_id,
                frame.content_x, frame.content_y, frame.content_w, frame.content_h)
    return hashlib.sha256(repr(geometry).encode() + content.tobytes()).hexdigest()


@dataclass(frozen=True)
class Observation:
    screen: str
    key: str
    elements: tuple[vision.TextElement, ...]
    width: int
    height: int
    image: Image.Image
    window_id: int

    def render(self, query: str = '') -> str:
        lines = [f'screen {self.screen} · {self.width}x{self.height} · OCR text targets']
        for i, element in enumerate(self.elements, 1):
            if not query or query.casefold() in element.text.casefold():
                lines.append(f'@{i} {element.text!r}')
        if len(lines) == 1:
            lines.append('(no matching text; use screenshot for icons/layout)')
        return '\n'.join(lines)


class Observations:
    """Bounded OCR and issued-screen caches; references retain their original labels."""
    def __init__(self):
        self.cache: OrderedDict[str, tuple[vision.TextElement, ...]] = OrderedDict()
        self.latest: Observation | None = None
        self.issued: OrderedDict[str, Observation] = OrderedDict()
        self.sequence = 0
        self.prefix = secrets.token_hex(3)
        self.ocr_calls = 0
        self.cache_hits = 0

    def read(self, frame: Frame) -> Observation:
        key = fingerprint(frame)
        if key in self.cache:
            self.cache_hits += 1
            elements = self.cache[key]
            self.cache.move_to_end(key)
        else:
            self.ocr_calls += 1
            elements = tuple(e for e in vision.recognize(frame.image, frame.device_w, frame.device_h)
                             if e.y - e.height / 2 >= frame.device_h * 0.06)
            self.cache[key] = elements
            if len(self.cache) > 4:
                self.cache.popitem(last=False)
        if self.latest is None or self.latest.key != key:
            self.sequence += 1
            self.latest = Observation(f'{self.prefix}-s{self.sequence}', key, elements, frame.device_w, frame.device_h, frame.coordinate_image(), frame.window.window_id)
            self.issued[self.latest.screen] = self.latest
            if len(self.issued) > 8:
                self.issued.popitem(last=False)
        return self.latest

    def _stale(self, frame: Frame) -> None:
        raise MirrorError('Stale or missing screen ID; no input sent.\n' + self.read(frame).render())

    def validate(self, frame: Frame, screen: str | None) -> Observation:
        previous = self.issued.get(screen)
        if previous is None or previous.window_id != frame.window.window_id or (
            previous.width, previous.height
        ) != (frame.device_w, frame.device_h):
            self._stale(frame)
        current = frame.coordinate_image()
        box = (0, int(frame.device_h * .06), frame.device_w, frame.device_h)
        if not visual.pixels_match(previous.image.crop(box), current.crop(box)):
            self._stale(frame)
        return previous

    def validate_point(self, frame: Frame, screen: str | None, point,
                       radius: float = 24) -> Observation:
        previous = self.validate(frame, screen)
        x, y = point
        box = (max(0, int(x-radius)), max(0, int(y-radius)),
               min(frame.device_w, int(x+radius+1)), min(frame.device_h, int(y+radius+1)))
        if not visual.pixels_match(previous.image.crop(box), frame.coordinate_image().crop(box),
                                   max_fraction=.005, max_mean=.5):
            self._stale(frame)
        return previous

    def target(self, frame: Frame, screen: str | None, ref: str) -> vision.TextElement:
        observation = self.validate(frame, screen)
        try:
            if not ref.startswith('@'):
                raise ValueError
            index = int(ref[1:]) - 1
            if index < 0:
                raise ValueError
            target = observation.elements[index]
        except (ValueError, IndexError):
            raise MirrorError('Unknown target reference; use a ref listed for its screen ID. '
                              'No input sent.\n' + observation.render()) from None
        # Resolve against the issuing screen, never renumber from a later OCR
        # pass. Guard the target region more strictly than unrelated UI pixels.
        self.validate_point(frame, screen, (target.x, target.y), radius=max(24, target.height))
        return target

    def invalidate(self) -> None:
        self.latest = None
        self.issued.clear()
