"""Compact observations and conservative screen-bound OCR references."""
from __future__ import annotations

import hashlib
import secrets
from collections import OrderedDict
from dataclasses import dataclass

from . import vision
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

    def render(self, query: str = '') -> str:
        lines = [f'screen {self.screen} · {self.width}x{self.height} · OCR text targets']
        for i, element in enumerate(self.elements, 1):
            if not query or query.casefold() in element.text.casefold():
                lines.append(f'@{i} {element.text!r}')
        if len(lines) == 1:
            lines.append('(no matching text; use screenshot for icons/layout)')
        return '\n'.join(lines)


class Observations:
    """Bounded OCR cache; a single latest snapshot owns actionable references."""
    def __init__(self):
        self.cache: OrderedDict[str, tuple[vision.TextElement, ...]] = OrderedDict()
        self.latest: Observation | None = None
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
            self.latest = Observation(f'{self.prefix}-s{self.sequence}', key, elements, frame.device_w, frame.device_h)
        return self.latest

    def validate(self, frame: Frame, screen: str | None) -> Observation:
        previous = self.latest
        current = self.read(frame)
        if previous is None or screen != previous.screen or current.screen != screen:
            raise MirrorError('Stale or missing screen ID; no input sent.\n' + current.render())
        return current

    def target(self, frame: Frame, screen: str | None, ref: str) -> vision.TextElement:
        observation = self.validate(frame, screen)
        try:
            if not ref.startswith('@'):
                raise ValueError
            index = int(ref[1:]) - 1
            if index < 0:
                raise ValueError
            return observation.elements[index]
        except (ValueError, IndexError):
            raise MirrorError('Unknown target reference; no input sent.\n' + observation.render()) from None

    def invalidate(self) -> None:
        self.latest = None
