"""Coordinate mapping -- the thing that must never silently drift.

Every tap in the system flows through Frame.to_global(). If it is wrong, every
tap lands off-target, and the failure looks like "the app didn't respond"
rather than like a maths bug.
"""

import pytest
from PIL import Image

from thumb import mirror
from conftest import make_frame


def test_origin_maps_to_window_plus_content_inset():
    frame = make_frame()
    assert frame.to_global(0, 0) == (1051.0 + 8.0, 162.0 + 38.0)


def test_bottom_right_maps_inside_the_window():
    frame = make_frame()
    x, y = frame.to_global(frame.device_w, frame.device_h)
    assert (x, y) == (1051.0 + 8.0 + 302.0, 162.0 + 38.0 + 655.0)
    # and must stay within the window bounds
    assert x <= frame.window.x + frame.window.width
    assert y <= frame.window.y + frame.window.height


def test_centre_maps_to_centre_of_the_content_rect():
    frame = make_frame()
    x, y = frame.to_global(frame.device_w / 2, frame.device_h / 2)
    assert x == pytest.approx(1051.0 + 8.0 + 151.0)
    assert y == pytest.approx(162.0 + 38.0 + 327.5)


def test_mapping_is_independent_of_window_position():
    """Moving the window must not change *which pixel* a device point means."""
    a = make_frame()
    moved = make_frame(window=mirror.WindowInfo(1, 2, 0.0, 0.0, 318.0, 701.0))
    ax, ay = a.to_global(100, 200)
    bx, by = moved.to_global(100, 200)
    assert ax - a.window.x == pytest.approx(bx - moved.window.x)
    assert ay - a.window.y == pytest.approx(by - moved.window.y)


def test_mapping_scales_with_zoom_level():
    """A zoomed window covers more screen points per device point."""
    small = make_frame(content_w=196.0, content_h=425.0)
    large = make_frame(content_w=394.0, content_h=852.0)
    sx, _ = small.to_global(393, 0)
    lx, _ = large.to_global(393, 0)
    assert lx - large.window.x > sx - small.window.x


@pytest.mark.parametrize(
    "content, expected",
    [
        ((393.0, 852.0), (393, 852)),   # exact match wins
        ((302.0, 655.0), (393, 852)),   # scaled down: nearest aspect
        ((440.0, 956.0), (440, 956)),
    ],
)
def test_device_identification(content, expected):
    width, height, _name = mirror._identify_device(*content)
    assert (width, height) == expected


def test_device_override_from_environment(monkeypatch):
    monkeypatch.setenv("IPHONE_MIRROR_DEVICE_SIZE", "111x222")
    assert mirror._identify_device(302.0, 655.0)[:2] == (111, 222)


def test_content_bbox_finds_the_opaque_region():
    """The chrome is transparent; the device screen is opaque."""
    image = Image.new("RGBA", (636, 1402), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (606, 1310), (20, 20, 20, 255)), (15, 76))
    left, top, right, bottom = mirror._content_bbox(image, scale=2.0)
    assert (left, top) == (15, 76)
    assert (right - left, bottom - top) == (606, 1310)


def test_content_bbox_falls_back_when_detection_is_implausible():
    """A fully transparent frame must not yield a zero-sized screen."""
    image = Image.new("RGBA", (636, 1402), (0, 0, 0, 0))
    left, top, right, bottom = mirror._content_bbox(image, scale=2.0)
    assert (left, top) == (16, 76)          # fixed insets, 8pt/38pt at 2x
    assert right - left > 500 and bottom - top > 1200


def test_downscale_caps_the_long_edge_and_never_upscales():
    tall = Image.new("RGB", (604, 1310))
    assert max(mirror.downscale(tall, max_edge=1024).size) == 1024
    small = Image.new("RGB", (100, 200))
    assert mirror.downscale(small, max_edge=1024).size == (100, 200)


def test_frame_difference_is_zero_for_identical_and_large_for_inverted():
    black = Image.new("RGB", (100, 100), (0, 0, 0))
    white = Image.new("RGB", (100, 100), (255, 255, 255))
    assert mirror.frame_difference(black, black) == 0
    assert mirror.frame_difference(black, white) > 200


def test_toolbar_chrome_is_recognised_as_too_tall():
    """iPhone Mirroring shows a toolbar on hover; it is opaque.

    Alpha detection then returns the whole window and every mapped coordinate
    silently shifts and rescales. Measured live: the full 636x1402 window,
    aspect 0.4536 instead of 0.4611.
    """
    assert mirror._looks_like_chrome(0, 0, 636, 1402)


def test_a_real_phone_shaped_detection_is_not_chrome():
    assert not mirror._looks_like_chrome(15, 76, 621, 1386)


def test_degenerate_boxes_are_not_treated_as_chrome():
    assert not mirror._looks_like_chrome(0, 0, 100, 0)


def test_chrome_detection_falls_back_to_the_fixed_insets():
    """The toolbar overlays the window without resizing it, so the phone screen
    is still exactly where the measured insets say."""
    image = Image.new("RGBA", (636, 1402), (20, 20, 20, 255))   # fully opaque
    left, top, right, bottom = mirror._content_bbox(image, scale=2.0)
    assert (left, top) == (16, 76)
    assert (right - left) / (bottom - top) == pytest.approx(0.4611, abs=0.005)
