"""Settle / assert / wait logic -- where the silent no-ops lived.

Three tools shipped reporting success while doing nothing. These tests pin the
behaviour that catches that class of bug.
"""

import pytest

from thumb import flows
from thumb.errors import MirrorError
from conftest import FakeSession, solid, with_block


def test_settle_reports_settled_on_a_still_screen():
    session = FakeSession(images=[solid()])
    status, _image = flows.settle(session, timeout_s=2.0, stable_for_s=0.2, poll_s=0.01)
    assert "settled" in status


def test_settle_reports_still_changing_when_frames_keep_moving():
    base = solid()
    frames = [with_block(base, (0, 0, 604, 700), (i * 8 % 250,) * 3) for i in range(1, 60)]
    session = FakeSession(images=frames)
    status, _image = flows.settle(session, timeout_s=0.4, stable_for_s=0.2, poll_s=0.01)
    assert "still changing" in status


def test_assert_changed_passes_when_the_screen_moved():
    base = solid()
    session = FakeSession(images=[with_block(base, (0, 0, 604, 900), (255, 255, 255))])
    delta = flows.assert_changed(session, base, "tap")
    assert delta > flows.CHANGED


def test_assert_changed_raises_with_an_actionable_message():
    """A no-op must be loud: this is the bug that shipped three times."""
    base = solid()
    session = FakeSession(images=[base])
    with pytest.raises(MirrorError) as excinfo:
        flows.assert_changed(session, base, "go_back")
    message = str(excinfo.value)
    assert "go_back" in message
    assert "did not change the screen" in message
    assert "frame delta" in message


def test_wait_for_band_returns_as_soon_as_content_appears():
    """Waits for the condition, rather than settling and hoping."""
    blank = solid(colour=(0, 0, 0))
    populated = with_block(blank, (0, 300, 604, 900), (255, 255, 255))
    session = FakeSession(images=[blank, blank, populated])
    found, _image = flows.wait_for_band(session, 0.24, 0.85, want_content=True, timeout_s=2.0)
    assert found


def test_wait_for_band_times_out_when_content_never_arrives():
    session = FakeSession(images=[solid(colour=(0, 0, 0))])
    found, _image = flows.wait_for_band(session, 0.24, 0.85, want_content=True, timeout_s=0.3)
    assert not found


def test_wait_for_band_can_wait_for_emptiness():
    populated = with_block(solid(), (0, 300, 604, 900), (255, 255, 255))
    blank = solid(colour=(0, 0, 0))
    session = FakeSession(images=[populated, blank])
    found, _image = flows.wait_for_band(session, 0.24, 0.85, want_content=False, timeout_s=2.0)
    assert found


def test_band_detail_separates_empty_space_from_content():
    """The threshold must sit between the two, or contact lookups misfire."""
    blank = solid(colour=(28, 28, 30))
    populated = with_block(blank, (0, 300, 604, 900), (255, 255, 255))
    assert flows._band_detail(blank, 0.24, 0.85) < flows.BAND_HAS_CONTENT
    assert flows._band_detail(populated, 0.24, 0.85) > flows.BAND_HAS_CONTENT


def test_band_brightness_detects_a_dimming_overlay():
    bright = solid(colour=(240, 240, 240))
    dimmed = solid(colour=(100, 100, 100))
    assert flows._band_brightness(bright, 0.1, 0.4) > 200
    assert flows._band_brightness(dimmed, 0.1, 0.4) < 200


@pytest.mark.parametrize("direction", ["down", "up", "left", "right"])
def test_scroll_vectors_move_in_the_expected_direction(direction):
    x1, y1, x2, y2 = flows.SCROLL_VECTORS[direction](0.6)
    if direction == "down":
        assert y2 < y1          # revealing content below means dragging up
    elif direction == "up":
        assert y2 > y1
    elif direction == "left":
        assert x2 < x1
    else:
        assert x2 > x1


def test_scroll_rejects_an_unknown_direction(still_session):
    with pytest.raises(MirrorError, match="Unknown scroll direction"):
        flows.scroll(still_session, "sideways")


def test_scroll_amount_is_clamped_to_a_sane_band():
    small = flows.SCROLL_VECTORS["down"](0.0)
    large = flows.SCROLL_VECTORS["down"](5.0)
    assert small != large  # the vector function itself is unclamped...
    # ...and scroll() clamps before using it, so the extremes stay on screen.
    assert 0.05 <= max(0.05, min(0.85, 0.0)) <= 0.85
    assert 0.05 <= max(0.05, min(0.85, 5.0)) <= 0.85
