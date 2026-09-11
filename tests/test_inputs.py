"""Inspect real Quartz events without sending input to the desktop."""

import Quartz
import pytest

from thumb import inputs
from conftest import make_frame


@pytest.fixture
def wheel(monkeypatch):
    events, warps = [], []
    monkeypatch.setattr(inputs, 'ensure_accessibility', lambda: None)
    monkeypatch.setattr(inputs, '_prepare', lambda pid: None)
    monkeypatch.setattr(inputs, '_cursor_position', lambda: Quartz.CGPoint(10, 20))
    monkeypatch.setattr(inputs.time, 'sleep', lambda _: None)
    monkeypatch.setattr(Quartz, 'CGWarpMouseCursorPosition', warps.append)
    monkeypatch.setattr(Quartz, 'CGAssociateMouseAndMouseCursorPosition', lambda _: None)
    monkeypatch.setattr(Quartz, 'CGEventPost', lambda tap, event: events.append(event))
    monkeypatch.setenv('IPHONE_MIRROR_EVENT_TARGET', 'hid')
    return events, warps


@pytest.mark.parametrize('pixels', [-511, 511, -3, 3])
def test_trackpad_scroll_distance_phases_and_location(wheel, pixels):
    events, warps = wheel
    frame = make_frame()
    point = inputs.scroll_wheel(frame, 100, 200, pixels)
    assert Quartz.CGEventGetType(events[0]) == Quartz.kCGEventMouseMoved
    assert tuple(Quartz.CGEventGetLocation(events[0])) == point
    events = events[1:]
    field = Quartz.CGEventGetIntegerValueField
    assert sum(field(e, Quartz.kCGScrollWheelEventPointDeltaAxis1) for e in events) == pixels
    assert [field(e, Quartz.kCGScrollWheelEventScrollPhase) for e in events] == (
        [Quartz.kCGScrollPhaseBegan]
        + [Quartz.kCGScrollPhaseChanged] * (min(14, abs(pixels)) - 1)
        + [Quartz.kCGScrollPhaseEnded]
    )
    for event in events:
        assert Quartz.CGEventGetType(event) == Quartz.kCGEventScrollWheel
        assert tuple(Quartz.CGEventGetLocation(event)) == point
        assert field(event, Quartz.kCGScrollWheelEventIsContinuous) == 1
        assert field(event, Quartz.kCGScrollWheelEventMomentumPhase) == 0
        assert field(event, Quartz.kCGEventSourceUserData) == inputs.SYNTHETIC_MARKER
        assert Quartz.CGEventGetFlags(event) == 0
    assert tuple(warps[-1]) == (10, 20)


def test_zero_scroll_does_not_move_pointer(wheel):
    inputs.scroll_wheel(make_frame(), 100, 200, 0)
    assert wheel == ([], [])


def test_cursor_restored_when_event_creation_fails(wheel, monkeypatch):
    monkeypatch.setattr(Quartz, 'CGEventCreateScrollWheelEvent', lambda *args: None)
    with pytest.raises(inputs.MirrorError, match='create a scroll event'):
        inputs.scroll_wheel(make_frame(), 100, 200, -300)
    assert tuple(wheel[1][-1]) == (10, 20)
