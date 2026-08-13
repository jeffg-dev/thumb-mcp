"""Watch real input on the mirroring window and turn it into replayable steps.

Hand-writing a flow means measuring landmarks for every control it touches.
Recording lets the user demonstrate it once instead: drive the phone by hand,
and the gestures come back as device-point steps that replay anywhere.

Two details make this work rather than merely appear to:

* The tap is **listen-only**, so watching never alters what the user is doing.
* Every event this package posts is stamped with a marker and skipped here.
  Without that, replaying a skill while recording would record itself, and a
  skill would double in length every time it ran.

Positions are converted to device points at capture time, against the window
geometry as it was *then*. That way a skill recorded on a small window replays
correctly on a zoomed one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import Quartz

from . import mirror
from .errors import AccessibilityDenied, MirrorError
from .inputs import SYNTHETIC_MARKER

# A press shorter than this with no movement is a tap; longer is a long press.
LONG_PRESS_S = 0.45
# Movement beyond this many device points makes a press a swipe rather than a tap.
MOVE_THRESHOLD = 12.0
# Gap after which typed characters are split into separate steps.
TYPING_GAP_S = 1.2


@dataclass
class RawEvent:
    kind: str            # "down" | "up" | "drag" | "scroll" | "key"
    at: float
    x: float = 0.0       # device points
    y: float = 0.0
    text: str = ""
    keycode: int = 0
    amount: float = 0.0


@dataclass
class Step:
    """One replayable action, in device points."""

    action: str
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    text: str | None = None
    key: str | None = None
    duration_ms: int | None = None
    amount: float | None = None

    def describe(self) -> str:
        if self.action == "tap":
            return f"tap ({self.x:.0f}, {self.y:.0f})"
        if self.action == "long_press":
            return f"long_press ({self.x:.0f}, {self.y:.0f}) {self.duration_ms}ms"
        if self.action == "swipe":
            return (f"swipe ({self.x:.0f}, {self.y:.0f}) -> "
                    f"({self.x2:.0f}, {self.y2:.0f}) {self.duration_ms}ms")
        if self.action == "scroll":
            return f"scroll {'down' if (self.amount or 0) < 0 else 'up'}"
        if self.action == "type_text":
            return f"type_text {self.text!r}"
        if self.action == "press_key":
            return f"press_key {self.key!r}"
        return self.action

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(**data)


# Keycodes we can name on the way back out; anything else is dropped rather
# than replayed as a mystery key.
NAMED_KEYS = {36: "return", 48: "tab", 49: "space", 51: "delete", 53: "escape",
              123: "left", 124: "right", 125: "down", 126: "up"}


@dataclass
class Recorder:
    """Collects raw events on a background run loop until stopped."""

    events: list[RawEvent] = field(default_factory=list)
    started_at: float = 0.0
    _tap: object = None
    _source: object = None
    _loop: object = None
    _thread: threading.Thread | None = None
    _window: mirror.WindowInfo | None = None
    _geometry: tuple[float, float, float, float, int, int] | None = None
    _geometry_at: float = 0.0
    running: bool = False

    # -- geometry ---------------------------------------------------------

    def _current_geometry(self):
        """Content rect + device space, refreshed occasionally.

        Re-reading on every event would mean a screen capture per mouse move.
        Half a second is frequent enough to survive the user dragging or
        re-zooming the window mid-recording.
        """
        now = time.monotonic()
        if self._geometry is None or now - self._geometry_at > 0.5:
            try:
                frame = mirror.capture_frame()
            except MirrorError:
                return self._geometry
            self._window = frame.window
            self._geometry = (
                frame.window.x + frame.content_x,
                frame.window.y + frame.content_y,
                frame.content_w,
                frame.content_h,
                frame.device_w,
                frame.device_h,
            )
            self._geometry_at = now
        return self._geometry

    def _to_device(self, gx: float, gy: float):
        """Global screen point -> device points, or None if outside the screen."""
        geometry = self._current_geometry()
        if geometry is None:
            return None
        left, top, width, height, device_w, device_h = geometry
        if not (left <= gx <= left + width and top <= gy <= top + height):
            return None
        return ((gx - left) / width * device_w, (gy - top) / height * device_h)

    # -- capture ----------------------------------------------------------

    def _handle(self, _proxy, event_type, event, _refcon):
        # Never record our own output, or a replay would record itself.
        if Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGEventSourceUserData
        ) == SYNTHETIC_MARKER:
            return event

        now = time.monotonic()
        if event_type == Quartz.kCGEventKeyDown:
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            _count, text = Quartz.CGEventKeyboardGetUnicodeString(event, 8, None, None)
            self.events.append(
                RawEvent("key", now, text=text or "", keycode=int(keycode))
            )
            return event

        location = Quartz.CGEventGetLocation(event)
        point = self._to_device(location.x, location.y)
        if point is None:
            return event  # outside the mirrored screen; not part of the flow
        x, y = point

        if event_type == Quartz.kCGEventLeftMouseDown:
            self.events.append(RawEvent("down", now, x, y))
        elif event_type == Quartz.kCGEventLeftMouseUp:
            self.events.append(RawEvent("up", now, x, y))
        elif event_type == Quartz.kCGEventLeftMouseDragged:
            self.events.append(RawEvent("drag", now, x, y))
        elif event_type == Quartz.kCGEventScrollWheel:
            amount = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGScrollWheelEventDeltaAxis1
            )
            self.events.append(RawEvent("scroll", now, x, y, amount=float(amount)))
        return event

    def start(self) -> None:
        if self.running:
            raise MirrorError("Already recording. Call stop_recording() first.")
        self.events = []
        self.started_at = time.monotonic()
        self._geometry = None

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,   # observe, never alter
            mask,
            self._handle,
            None,
        )
        if self._tap is None:
            raise AccessibilityDenied()

        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        ready = threading.Event()

        def run() -> None:
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._loop, self._source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(self._tap, True)
            ready.set()
            Quartz.CFRunLoopRun()

        self._thread = threading.Thread(target=run, daemon=True, name="thumb-recorder")
        self._thread.start()
        ready.wait(timeout=3.0)
        self.running = True

    def stop(self) -> list[Step]:
        if not self.running:
            raise MirrorError("Not recording. Call start_recording() first.")
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.running = False
        return classify(self.events)


def classify(events: list[RawEvent]) -> list[Step]:
    """Turn raw events into the smallest set of steps that reproduces them."""
    steps: list[Step] = []
    index = 0
    pending_text: list[str] = []
    pending_at = 0.0

    def flush_text() -> None:
        if pending_text:
            steps.append(Step("type_text", text="".join(pending_text)))
            pending_text.clear()

    while index < len(events):
        event = events[index]

        if event.kind == "key":
            named = NAMED_KEYS.get(event.keycode)
            printable = event.text and event.text.isprintable()
            if printable and named not in ("return", "escape", "delete", "tab"):
                if pending_text and event.at - pending_at > TYPING_GAP_S:
                    flush_text()
                pending_text.append(event.text)
                pending_at = event.at
            else:
                flush_text()
                if named:
                    steps.append(Step("press_key", key=named))
            index += 1
            continue

        flush_text()

        if event.kind == "scroll":
            total = event.amount
            last = index
            # Collapse a burst of wheel events into one scroll step.
            while (last + 1 < len(events) and events[last + 1].kind == "scroll"
                   and events[last + 1].at - events[last].at < 0.4):
                last += 1
                total += events[last].amount
            steps.append(Step("scroll", x=event.x, y=event.y, amount=total))
            index = last + 1
            continue

        if event.kind == "down":
            end = index + 1
            moved = 0.0
            last_point = (event.x, event.y)
            while end < len(events) and events[end].kind in ("drag", "up"):
                moved = max(
                    moved,
                    abs(events[end].x - event.x) + abs(events[end].y - event.y),
                )
                last_point = (events[end].x, events[end].y)
                if events[end].kind == "up":
                    break
                end += 1
            release = events[min(end, len(events) - 1)]
            held_ms = int((release.at - event.at) * 1000)

            if moved > MOVE_THRESHOLD:
                steps.append(Step("swipe", x=event.x, y=event.y,
                                  x2=last_point[0], y2=last_point[1],
                                  duration_ms=max(80, held_ms)))
            elif release.at - event.at >= LONG_PRESS_S:
                steps.append(Step("long_press", x=event.x, y=event.y,
                                  duration_ms=max(500, held_ms)))
            else:
                steps.append(Step("tap", x=event.x, y=event.y))
            index = end + 1
            continue

        index += 1

    flush_text()
    return steps
