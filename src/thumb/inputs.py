"""Synthesised mouse and keyboard input for the mirroring window.

Delivery mechanism -- measured, not assumed
-------------------------------------------
CGEventPostToPid(mirroring_pid, ...) is the tidier approach: it hands the event
straight to one process without touching the real cursor. iPhone Mirroring
**ignores it completely**. Measured against a live session: an identical
down/up pair produced a frame delta of 0.007 (nothing happened) via
CGEventPostToPid and 100.18 (the tapped app launched) via CGEventPost to the
HID tap. Apple's client only honours events that arrive through the HID event
stream.

So the default is CGEventPost(kCGHIDEventTap), which has consequences worth
knowing:

* The real mouse cursor moves. We save and restore its position around every
  gesture so the user's pointer ends up where they left it.
* Events land on whatever is under that screen point, so the mirroring window
  must be frontmost and unobscured. Every gesture activates the app first,
  which raises its window.

Set IPHONE_MIRROR_EVENT_TARGET=pid to force the per-process path if a future
macOS release starts honouring it.

Timing
------
A tap needs a real hold between down and up or the app discards it, and a swipe
needs genuinely interpolated drag points or iOS reads it as a tap.
"""

from __future__ import annotations

import math
import os
import time

import Quartz

from .ax import activate, ensure_accessibility
from .errors import MirrorError
from .mirror import Frame

# Virtual keycodes (ANSI layout). These are physical key positions, so they stay
# correct regardless of the Mac's keyboard layout.
KEYCODES: dict[str, int] = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _use_pid_target() -> bool:
    return os.environ.get("IPHONE_MIRROR_EVENT_TARGET", "hid").lower() == "pid"


def _post(pid: int, event) -> None:
    if event is None:
        raise MirrorError("Quartz failed to create an input event.")
    if _use_pid_target():
        Quartz.CGEventPostToPid(pid, event)
    else:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _cursor_position() -> Quartz.CGPoint:
    return Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))


def _restore_cursor(point: Quartz.CGPoint) -> None:
    """Put the user's pointer back where it was before we borrowed it."""
    if _use_pid_target():
        return
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft
    )
    if event is not None:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _prepare(pid: int) -> None:
    """Front the app so the click lands on the mirror window, not through it."""
    activate(pid)
    time.sleep(_env_float("IPHONE_MIRROR_ACTIVATE_DELAY_MS", 150.0) / 1000.0)


def focus(pid: int) -> None:
    """Public: make the mirroring window frontmost and let focus settle."""
    _prepare(pid)


def _mouse(pid: int, event_type: int, x: float, y: float) -> None:
    event = Quartz.CGEventCreateMouseEvent(
        None, event_type, Quartz.CGPoint(x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 1)
    _post(pid, event)


def tap(frame: Frame, x: float, y: float) -> tuple[float, float]:
    """Single tap at a device point. Returns the global screen point used."""
    ensure_accessibility()
    pid = frame.window.pid
    gx, gy = frame.to_global(x, y)
    origin = _cursor_position()
    _prepare(pid)
    _mouse(pid, Quartz.kCGEventMouseMoved, gx, gy)
    time.sleep(0.02)
    _mouse(pid, Quartz.kCGEventLeftMouseDown, gx, gy)
    time.sleep(_env_float("IPHONE_MIRROR_TAP_HOLD_MS", 80.0) / 1000.0)
    _mouse(pid, Quartz.kCGEventLeftMouseUp, gx, gy)
    time.sleep(0.02)
    _restore_cursor(origin)
    return gx, gy


def swipe(
    frame: Frame,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    duration_ms: int = 300,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Press, drag along an interpolated path, then release."""
    ensure_accessibility()
    pid = frame.window.pid
    start = frame.to_global(x1, y1)
    end = frame.to_global(x2, y2)
    duration_s = max(duration_ms, 1) / 1000.0

    origin = _cursor_position()
    _prepare(pid)
    _mouse(pid, Quartz.kCGEventMouseMoved, *start)
    time.sleep(0.01)
    _mouse(pid, Quartz.kCGEventLeftMouseDown, *start)

    # Drive the drag off the wall clock, not off a fixed per-step sleep. Posting
    # an event costs real time, so a "sleep(duration/steps)" loop overshoots --
    # measured at 485ms for a requested 350ms. Overshooting past iOS's ~500ms
    # long-press threshold turns a Home Screen swipe into an icon *drag*, which
    # rearranges the user's apps. Track elapsed time instead so a 300ms swipe
    # really takes 300ms.
    began = time.perf_counter()
    while True:
        progress = min(1.0, (time.perf_counter() - began) / duration_s)
        # Ease out only. Easing *in* leaves the finger nearly stationary at the
        # start, which also reads as a long press; this moves immediately and
        # decelerates into the release, which is what iOS momentum expects.
        eased = math.sin(progress * math.pi / 2)
        gx = start[0] + (end[0] - start[0]) * eased
        gy = start[1] + (end[1] - start[1]) * eased
        _mouse(pid, Quartz.kCGEventLeftMouseDragged, gx, gy)
        if progress >= 1.0:
            break
        time.sleep(0.004)

    _mouse(pid, Quartz.kCGEventLeftMouseUp, *end)
    time.sleep(0.02)
    _restore_cursor(origin)
    return start, end


# US-ANSI virtual keycodes per character. iPhone Mirroring forwards the *keycode*
# to the phone and ignores any attached unicode payload, so typing has to press
# the physically correct key -- see type_text().
CHAR_KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    " ": 49, "\t": 48, "\n": 36,
}

# Characters reached with Shift on a US layout.
SHIFTED_CHARS: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}


def _char_key(char: str) -> tuple[int, bool] | None:
    """(keycode, needs_shift) for a character, or None if not typeable this way."""
    if char in CHAR_KEYCODES:
        return CHAR_KEYCODES[char], False
    lower = char.lower()
    if char.isalpha() and lower in CHAR_KEYCODES:
        return CHAR_KEYCODES[lower], char.isupper()
    if char in SHIFTED_CHARS:
        return CHAR_KEYCODES[SHIFTED_CHARS[char]], True
    return None


def scroll_wheel(
    frame: Frame,
    x: float,
    y: float,
    lines: int,
    steps: int = 14,
    step_delay: float = 0.03,
) -> tuple[float, float]:
    """Scroll the content under a device point using wheel events.

    A click-drag does NOT scroll iOS through mirroring -- horizontal drags page
    the Home Screen fine, but a vertical drag over a list does nothing at all.
    Mirroring expects trackpad-style scroll events instead.

    The catch is that scroll events are delivered to whatever is under the
    *system* cursor, and posting a synthetic mouse-moved event does not move it.
    The cursor has to be warped there for real, which is why this had silently
    never worked.

    ``lines`` is positive to scroll up (towards earlier content) and negative to
    scroll down.
    """
    ensure_accessibility()
    pid = frame.window.pid
    gx, gy = frame.to_global(x, y)
    origin = _cursor_position()
    _prepare(pid)

    Quartz.CGWarpMouseCursorPosition(Quartz.CGPoint(gx, gy))
    Quartz.CGAssociateMouseAndMouseCursorPosition(True)
    time.sleep(0.12)

    per_step = int(lines / max(1, steps)) or (1 if lines > 0 else -1)
    for _ in range(steps):
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitPixel, 1, per_step
        )
        if event is None:
            raise MirrorError("Quartz failed to create a scroll event.")
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(step_delay)

    time.sleep(0.05)
    Quartz.CGWarpMouseCursorPosition(origin)
    return gx, gy


def long_press(frame: Frame, x: float, y: float, hold_ms: int = 700) -> tuple[float, float]:
    """Press and hold at a device point -- context menus, icon pickup, previews.

    iOS's long-press threshold is around 500ms, so the default clears it with
    margin. The pointer is held still: any drift and iOS reclassifies the
    gesture as a drag.
    """
    ensure_accessibility()
    pid = frame.window.pid
    gx, gy = frame.to_global(x, y)
    origin = _cursor_position()
    _prepare(pid)
    _mouse(pid, Quartz.kCGEventMouseMoved, gx, gy)
    time.sleep(0.02)
    _mouse(pid, Quartz.kCGEventLeftMouseDown, gx, gy)
    # Keep the press alive with stationary drags; a bare sleep can let the
    # gesture lapse before iOS registers the hold.
    deadline = time.perf_counter() + max(hold_ms, 1) / 1000.0
    while time.perf_counter() < deadline:
        _mouse(pid, Quartz.kCGEventLeftMouseDragged, gx, gy)
        time.sleep(0.05)
    _mouse(pid, Quartz.kCGEventLeftMouseUp, gx, gy)
    time.sleep(0.02)
    _restore_cursor(origin)
    return gx, gy


def double_tap(frame: Frame, x: float, y: float, gap_ms: int = 90) -> tuple[float, float]:
    """Two taps in quick succession, e.g. zoom or like.

    The second click carries clickState 2 -- without it the pair arrives as two
    unrelated single taps and the app never sees a double tap.
    """
    ensure_accessibility()
    pid = frame.window.pid
    gx, gy = frame.to_global(x, y)
    origin = _cursor_position()
    _prepare(pid)
    _mouse(pid, Quartz.kCGEventMouseMoved, gx, gy)
    time.sleep(0.02)
    for click_state in (1, 2):
        for event_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(
                None, event_type, Quartz.CGPoint(gx, gy), Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventClickState, click_state
            )
            Quartz.CGEventSetFlags(event, 0)
            _post(pid, event)
            time.sleep(0.03)
        if click_state == 1:
            time.sleep(max(gap_ms, 1) / 1000.0)
    time.sleep(0.02)
    _restore_cursor(origin)
    return gx, gy


def drag(
    frame: Frame,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    hold_ms: int = 700,
    move_ms: int = 900,
    settle_ms: int = 700,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Pick something up, move it, put it down -- reordering, drag-and-drop.

    Distinct from swipe(): a swipe is a flick that must stay *under* the
    long-press threshold, whereas a drag must deliberately exceed it to make
    iOS lift the item first, then dwell at the destination so the drop target
    registers before release.
    """
    ensure_accessibility()
    pid = frame.window.pid
    start = frame.to_global(x1, y1)
    end = frame.to_global(x2, y2)
    origin = _cursor_position()
    _prepare(pid)
    _mouse(pid, Quartz.kCGEventMouseMoved, *start)
    time.sleep(0.05)
    _mouse(pid, Quartz.kCGEventLeftMouseDown, *start)

    deadline = time.perf_counter() + max(hold_ms, 1) / 1000.0
    while time.perf_counter() < deadline:  # hold still until the item lifts
        _mouse(pid, Quartz.kCGEventLeftMouseDragged, *start)
        time.sleep(0.05)

    move_s = max(move_ms, 1) / 1000.0
    began = time.perf_counter()
    while True:
        progress = min(1.0, (time.perf_counter() - began) / move_s)
        eased = 0.5 - 0.5 * math.cos(progress * math.pi)
        _mouse(
            pid,
            Quartz.kCGEventLeftMouseDragged,
            start[0] + (end[0] - start[0]) * eased,
            start[1] + (end[1] - start[1]) * eased,
        )
        if progress >= 1.0:
            break
        time.sleep(0.02)

    deadline = time.perf_counter() + max(settle_ms, 0) / 1000.0
    while time.perf_counter() < deadline:  # dwell so the drop target activates
        _mouse(pid, Quartz.kCGEventLeftMouseDragged, *end)
        time.sleep(0.05)

    _mouse(pid, Quartz.kCGEventLeftMouseUp, *end)
    time.sleep(0.02)
    _restore_cursor(origin)
    return start, end


def type_text(pid: int, text: str) -> int:
    """Type text into the focused field on the device.

    The obvious implementation -- one event with keycode 0 carrying a unicode
    payload via CGEventKeyboardSetUnicodeString -- is silently wrong here.
    iPhone Mirroring relays the *keycode* to the phone and drops the unicode
    string, and keycode 0 is the physical "A" key, so every character arrives as
    "a" (and the repeats trip iOS's press-and-hold accent picker). So press the
    real key for each character, with Shift where the layout needs it.

    Characters with no US-layout key (emoji, accented letters) still go through
    the unicode path, which is better than nothing for apps that do honour it.
    """
    ensure_accessibility()
    if not text:
        return 0
    _prepare(pid)
    delay = _env_float("IPHONE_MIRROR_KEY_DELAY_MS", 14.0) / 1000.0
    for char in text:
        mapping = _char_key(char)
        if mapping is not None:
            keycode, shift = mapping
            flags = Quartz.kCGEventFlagMaskShift if shift else 0
            _key(pid, keycode, True, flags)
            _key(pid, keycode, False, flags)
        else:
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                if event is None:
                    raise MirrorError("Quartz failed to create a keyboard event.")
                Quartz.CGEventKeyboardSetUnicodeString(event, len(char), char)
                _post(pid, event)
        time.sleep(delay)
    return len(text)


def _key(pid: int, keycode: int, down: bool, flags: int = 0) -> None:
    event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    if event is None:
        raise MirrorError("Quartz failed to create a keyboard event.")
    # Always set flags explicitly, including to zero. A synthesised event
    # inherits the current modifier state otherwise, so a Command flag left over
    # from an earlier shortcut rides along on ordinary letters -- typing
    # "instagram" then delivers Cmd-M and minimises the mirroring window.
    Quartz.CGEventSetFlags(event, flags)
    _post(pid, event)


def press_key(pid: int, key: str) -> str:
    """Press a single named non-character key."""
    ensure_accessibility()
    name = key.strip().lower()
    if name not in KEYCODES:
        raise MirrorError(
            f"Unknown key {key!r}. Supported keys: "
            f"{', '.join(sorted(set(KEYCODES)))}."
        )
    _prepare(pid)
    keycode = KEYCODES[name]
    _key(pid, keycode, True)
    time.sleep(0.03)
    _key(pid, keycode, False)
    return name


def press_key_repeat(pid: int, key: str, times: int, delay_s: float = 0.012) -> None:
    """Press a named key repeatedly in one activation, e.g. backspace to clear."""
    ensure_accessibility()
    name = key.strip().lower()
    if name not in KEYCODES:
        raise MirrorError(f"Unknown key {key!r}.")
    _prepare(pid)
    code = KEYCODES[name]
    for _ in range(max(0, times)):
        _key(pid, code, True)
        _key(pid, code, False)
        time.sleep(delay_s)


def press_command_key(pid: int, character: str) -> None:
    """Cmd-<digit> fallback for the View-menu shortcuts (Cmd-1/2/3)."""
    ensure_accessibility()
    digits = {"1": 18, "2": 19, "3": 20}
    if character not in digits:
        raise MirrorError(f"Unsupported command shortcut Cmd-{character}.")
    _prepare(pid)
    keycode = digits[character]
    flags = Quartz.kCGEventFlagMaskCommand
    _key(pid, keycode, True, flags)
    time.sleep(0.03)
    _key(pid, keycode, False, flags)
