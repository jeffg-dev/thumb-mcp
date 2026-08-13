"""Test doubles so the suite runs without a phone, a mirroring session, or CI hardware.

Everything worth testing here is pure: coordinate maths, ranking, keycode
mapping, and the settle/assert logic that decides whether an action landed.
Those are exactly the places where bugs shipped silently, and none of them need
a device.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from thumb.mirror import Frame, WindowInfo


def solid(width: int = 604, height: int = 1310, colour: tuple[int, int, int] = (10, 10, 10)):
    return Image.new("RGB", (width, height), colour)


def with_block(base: Image.Image, box, colour):
    """Copy of an image with a filled rectangle -- a cheap 'the screen changed'."""
    out = base.copy()
    out.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), colour), box[:2])
    return out


def make_frame(
    image: Image.Image | None = None,
    *,
    device_w: int = 393,
    device_h: int = 852,
    window: WindowInfo | None = None,
    content_x: float = 8.0,
    content_y: float = 38.0,
    content_w: float = 302.0,
    content_h: float = 655.0,
    scale: float = 2.0,
) -> Frame:
    """A Frame with the real geometry measured from a live session."""
    return Frame(
        image=image if image is not None else solid(),
        window=window or WindowInfo(window_id=1, pid=2, x=1051.0, y=162.0,
                                    width=318.0, height=701.0),
        scale=scale,
        content_x=content_x,
        content_y=content_y,
        content_w=content_w,
        content_h=content_h,
        device_w=device_w,
        device_h=device_h,
        device_name="test device",
    )


@dataclass
class FakeSession:
    """Replays a scripted sequence of frames.

    ``frame()`` walks the script and then holds on the last entry, which lets a
    test say "the screen changes twice and then goes still" and assert what
    settle() concludes.
    """

    images: list[Image.Image]
    calls: int = 0

    def _next(self) -> Image.Image:
        image = self.images[min(self.calls, len(self.images) - 1)]
        self.calls += 1
        return image

    def frame(self) -> Frame:
        return make_frame(self._next())

    def live_frame(self) -> Frame:
        return self.frame()


@pytest.fixture
def still_session():
    """A screen that never changes."""
    return FakeSession(images=[solid()])


@pytest.fixture
def changing_session():
    """Two different frames, then still."""
    base = solid()
    return FakeSession(images=[base, with_block(base, (0, 0, 604, 700), (240, 240, 240))])
