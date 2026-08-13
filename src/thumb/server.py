"""MCP server exposing the iPhone Mirroring window as a controllable device."""

from __future__ import annotations

import base64
import functools
import io
import time
from dataclasses import dataclass

import Quartz
from mcp.server import MCPServer
from mcp.types import ImageContent, TextContent
from PIL import Image as PILImage

from . import ax, flows, inputs, landmarks, mirror, recorder, skills, vision
from .errors import (
    AccessibilityDenied,
    CoordinatesOutOfRange,
    MirrorError,
    MirroringNotRunning,
    MirroringPaused,
    ScreenRecordingDenied,
    WindowNotFound,
    host_app,
)
from .mirror import Frame

# How long to wait out a transient "Connecting…" handshake before failing.
TRANSIENT_WAIT_S = 12.0

server = MCPServer(
    name="thumb",
    version="0.1.0",
    instructions=(
        "Controls a physical iPhone through the macOS iPhone Mirroring app.\n\n"
        "Always call screenshot() before tapping: it reports the device "
        "coordinate space and shows the current screen. All coordinates are "
        "device points with (0,0) at the top-left of the phone screen -- never "
        "Mac screen coordinates, and never pixels of the returned image.\n\n"
        "After any action that changes the screen, call wait_until_settled() "
        "and then screenshot() to observe the result before acting again."
    ),
)


@dataclass
class Session:
    """Holds the cached window ID and nothing else that can go stale."""

    window_id: int | None = None
    last_frame: Frame | None = None

    def frame(self) -> Frame:
        """Capture a fresh frame, re-resolving the window if the ID went bad.

        The window ID changes every time mirroring reconnects, so a cached ID is
        only ever a fast path -- any failure falls back to a full re-discovery.
        """
        if self.window_id is not None:
            window = mirror.window_by_id(self.window_id)
            if window is not None:
                try:
                    frame = mirror.capture_frame(window)
                    self.last_frame = frame
                    return frame
                except MirrorError:
                    pass  # fall through to re-resolve
            self.window_id = None

        window = self._resolve()
        self.window_id = window.window_id
        frame = mirror.capture_frame(window)
        self.last_frame = frame
        return frame

    @staticmethod
    def _resolve() -> mirror.WindowInfo:
        """Find the window, waking the app if it has parked its windows off-screen.

        When a mirroring session drops while the app is in the background, the
        app stops compositing its windows -- they vanish from the on-screen
        window list even though the process is alive and 'visible'. Activating
        it brings them back, so treat that as recovery rather than a hard error.
        """
        try:
            return mirror.find_window()
        except MirroringNotRunning:
            # The app exits by itself once a session ends. Relaunching is always
            # the right move -- there is nothing for a human to decide here.
            if not mirror.launch_app():
                raise
            time.sleep(2.0)
            return mirror.find_window()
        except WindowNotFound:
            pid = mirror.mirroring_pid()
            if pid is None:
                raise
            ax.activate(pid)
            time.sleep(0.8)
            return mirror.find_window()

    def live_frame(self) -> Frame:
        """A frame that is guaranteed to be the streamed device screen.

        Raises if the app is showing an interstitial instead of the phone, so
        callers never tap blindly into a 'Connect' dialog.
        """
        deadline = time.monotonic() + TRANSIENT_WAIT_S
        while True:
            frame = self.frame()
            if not ax.accessibility_ok():
                return frame
            overlay = ax.detect_overlay(frame.window.pid)
            if overlay is None:
                return frame
            # "Connecting to iPhone…" / "Resuming…" are handshake states that
            # clear on their own within a couple of seconds. Failing on them
            # would make every call after a reconnect spuriously error.
            transient = any(
                word in overlay.message.lower()
                for word in ("connecting", "resuming", "starting", "reconnecting")
            )
            if not transient or time.monotonic() >= deadline:
                raise MirroringPaused(overlay.message, overlay.buttons)
            time.sleep(0.25)


SESSION = Session()

# One recorder for the process: recording is inherently a single global mode.
RECORDER = recorder.Recorder()


@dataclass
class PendingDraft:
    """A composed-but-unsent message waiting on the user's go-ahead."""

    app: str  # "messages" | "whatsapp"
    recipient: str
    text: str
    contact_index: int = 1


# Set when a send_* tool drafts, cleared once the message actually goes.
PENDING: PendingDraft | None = None


def _focus_safe(fn):
    """Give keyboard focus back to the user's app once the tool finishes.

    Without this, the mirroring app stays frontmost after a call and anything
    the user types goes to the phone instead of their editor.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with ax.keep_focus(SESSION.window_id and mirror.mirroring_pid()):
            return fn(*args, **kwargs)

    return wrapper


def _png_content(image: PILImage.Image) -> ImageContent:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return ImageContent(
        type="image",
        data=base64.b64encode(buffer.getvalue()).decode("ascii"),
        mimeType="image/png",
    )


def _describe(frame: Frame, shown: PILImage.Image) -> str:
    return (
        f"iPhone screen ({frame.device_name}).\n"
        f"Coordinate space for tap/swipe: {frame.device_w} x {frame.device_h} "
        f"device points, origin top-left.\n"
        f"Image shown at {shown.width} x {shown.height} px "
        f"(downscaled {frame.device_w / shown.width:.2f}x from the coordinate "
        "space; use device points, not image pixels, for tap/swipe)."
    )


def _check(frame: Frame, x: float, y: float) -> None:
    if not (0 <= x <= frame.device_w and 0 <= y <= frame.device_h):
        raise CoordinatesOutOfRange(x, y, frame.device_w, frame.device_h)


@server.tool(
    description=(
        "Capture the mirrored iPhone screen. Returns the image plus the device "
        "coordinate space to use for tap() and swipe(). Call this before every "
        "interaction."
    )
)
def screenshot() -> list[TextContent | ImageContent]:
    frame = SESSION.live_frame()
    shown = mirror.downscale(frame.image)
    return [TextContent(type="text", text=_describe(frame, shown)), _png_content(shown)]


@server.tool(
    description=(
        "Tap the iPhone screen at a device point (see screenshot() for the "
        "coordinate space). Origin is the top-left of the phone screen."
    )
)
@_focus_safe
def tap(x: float, y: float) -> str:
    frame = SESSION.live_frame()
    _check(frame, x, y)
    gx, gy = inputs.tap(frame, x, y)
    return (
        f"Tapped device point ({x:g}, {y:g}) -> screen ({gx:.1f}, {gy:.1f}). "
        "Call wait_until_settled() then screenshot() to see the result."
    )


@server.tool(
    description=(
        "Swipe/drag on the iPhone screen from one device point to another. Use "
        "for scrolling, dismissing, and edge gestures. A longer duration_ms "
        "produces a slower drag; a short one flicks with momentum."
    )
)
@_focus_safe
def swipe(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    duration_ms: int = 300,
) -> str:
    frame = SESSION.live_frame()
    _check(frame, x1, y1)
    _check(frame, x2, y2)
    start, end = inputs.swipe(frame, x1, y1, x2, y2, duration_ms)
    return (
        f"Swiped ({x1:g}, {y1:g}) -> ({x2:g}, {y2:g}) over {duration_ms}ms "
        f"[screen {start[0]:.0f},{start[1]:.0f} -> {end[0]:.0f},{end[1]:.0f}]. "
        "Call wait_until_settled() then screenshot() to see the result."
    )


@server.tool(
    description=(
        "Type text into whatever field is focused on the iPhone. Focus a text "
        "field with tap() first. Handles unicode and emoji."
    )
)
@_focus_safe
def type_text(text: str) -> str:
    # Wait for the UI to stop moving first. Keystrokes sent while a field is
    # still animating into focus are silently swallowed -- the field ends up
    # focused but empty, which looks like typing "not working".
    flows.settle(SESSION, timeout_s=3.0, stable_for_s=0.3)
    frame = SESSION.live_frame()
    count = inputs.type_text(frame.window.pid, text)
    return f"Typed {count} character(s)."


@server.tool(
    description=(
        "Press a single named key on the iPhone: return, delete, escape, tab, "
        "space, or an arrow key (up/down/left/right)."
    )
)
@_focus_safe
def press_key(key: str) -> str:
    frame = SESSION.live_frame()
    name = inputs.press_key(frame.window.pid, key)
    return f"Pressed {name!r}."


@server.tool(description="Go to the iPhone Home Screen.")
@_focus_safe
def home() -> str:
    pid = SESSION.live_frame().window.pid
    flows.go_home(SESSION, pid)
    return "At the Home Screen."


@server.tool(description="Open the iPhone App Switcher.")
@_focus_safe
def app_switcher() -> str:
    pid = SESSION.live_frame().window.pid
    ax.press_menu_item(pid, ax.MENU_APP_SWITCHER)
    status, _ = flows.settle(SESSION, timeout_s=4.0)
    return f"Opened the App Switcher ({status})."


@server.tool(description="Open Spotlight search on the iPhone.")
@_focus_safe
def spotlight() -> str:
    pid = SESSION.live_frame().window.pid
    if not flows.open_spotlight(SESSION, pid):
        raise MirrorError(
            "Could not open Spotlight -- the menu command, Cmd-3 and the swipe "
            "gesture all left the screen unchanged."
        )
    return "Spotlight is open and focused."


@server.tool(
    description=(
        "Poll frames until the screen stops changing, so you observe a settled "
        "UI instead of a mid-animation one. Most composite tools already do this "
        "internally -- you only need it after a raw tap/swipe."
    )
)
def wait_until_settled(
    timeout_s: float = 5.0,
    stable_for_s: float = 0.35,
    threshold: float = 1.2,
) -> str:
    status, _ = flows.settle(SESSION, timeout_s, stable_for_s, threshold)
    return f"Screen {status}."


@server.tool(
    description=(
        "Report the mirroring session state: window geometry, device coordinate "
        "space, permissions, and whether the device is currently streaming. Use "
        "this to diagnose failures."
    )
)
def device_info() -> str:
    lines: list[str] = []
    screen_ok = bool(Quartz.CGPreflightScreenCaptureAccess())
    ax_ok = ax.accessibility_ok()
    lines.append(f"Host app needing permissions: {host_app()}")
    lines.append(f"Screen Recording granted: {screen_ok}")
    lines.append(f"Accessibility granted:   {ax_ok}")
    if not screen_ok:
        lines.append(str(ScreenRecordingDenied()))
        return "\n".join(lines)

    frame = SESSION.frame()
    overlay = ax.detect_overlay(frame.window.pid) if ax_ok else None
    window = frame.window
    lines += [
        f"Mirroring PID: {window.pid}, window ID: {window.window_id}",
        f"Window bounds: {window.width:g}x{window.height:g} "
        f"at ({window.x:g}, {window.y:g})",
        f"Device screen inside window: {frame.content_w:g}x{frame.content_h:g} pt "
        f"at offset ({frame.content_x:g}, {frame.content_y:g})",
        f"Capture scale: {frame.scale:g}x  (raw crop {frame.image.width}x"
        f"{frame.image.height} px)",
        f"Coordinate space: {frame.device_w}x{frame.device_h} pt ({frame.device_name})",
        f"Streaming: {overlay is None}",
    ]
    if overlay is not None:
        lines.append(f"Interstitial on screen: {overlay.message!r}")
        lines.append(f"Buttons available: {overlay.buttons}")
        lines.append("Use reconnect() to press Connect once the iPhone is locked.")
    if not ax_ok:
        lines.append(str(AccessibilityDenied()))
    return "\n".join(lines)


@server.tool(
    description=(
        "Resume a paused mirroring session by pressing its Connect button. "
        "Mirroring pauses whenever the physical iPhone is picked up; the phone "
        "must be locked and set down again for this to succeed."
    )
)
def reconnect() -> str:
    ax.ensure_accessibility()
    frame = SESSION.frame()
    pid = frame.window.pid
    overlay = ax.detect_overlay(pid)
    if overlay is None:
        return "Already streaming -- nothing to reconnect."
    # The interstitial is labelled differently depending on why streaming
    # stopped: "Connect" after the phone was picked up, "Resume" after the Mac
    # session idled out. Press whichever resume-style button is actually there.
    wanted = ("connect", "resume", "continue", "try again")
    button = next(
        (b for b in overlay.buttons if b.strip().lower() in wanted),
        None,
    ) or next(iter(overlay.buttons), None)
    if button is None:
        return (
            f"Mirroring is showing {overlay.message!r} with no button to press. "
            "Resolve it on the Mac, then retry."
        )
    ax.activate(pid)
    time.sleep(0.15)
    ax.press_button(pid, button)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if ax.detect_overlay(pid) is None:
            return "Reconnected -- the device is streaming again."
    still = ax.detect_overlay(pid)
    return (
        "Pressed Connect but the session has not resumed yet"
        + (f" (screen says {still.message!r})" if still else "")
        + ". If the iPhone is unlocked or in use, lock it and set it down, then "
        "retry."
    )



def _shot(
    frame_image: PILImage.Image, header: str, frame: Frame | None = None
) -> list[TextContent | ImageContent]:
    """Package a flow's final frame as text + image.

    Reuses the frame the flow already captured; re-capturing here just to read
    the coordinate space would cost an extra grab on every single call.
    """
    frame = frame or SESSION.last_frame or SESSION.live_frame()
    shown = mirror.downscale(frame_image)
    return [
        TextContent(type="text", text=header + "\n" + _describe(frame, shown)),
        _png_content(shown),
    ]


@server.tool(
    description=(
        "Open an app by name in one step: goes Home, opens Spotlight, types the "
        "name, and launches the top hit, returning the settled screen. Prefer "
        "this over hunting for an icon with screenshots and taps."
    )
)
@_focus_safe
def open_app(name: str) -> list[TextContent | ImageContent]:
    report, image = flows.open_app(SESSION, name)
    return _shot(image, report)


@server.tool(
    description=(
        "Open an app and run a search inside it in ONE call, with no "
        "intermediate screenshots: Home -> Spotlight -> launch -> Search tab -> "
        "search field -> type. Uses known per-app tab positions. Prefer this for "
        "any 'open X and search for Y' request. Override tab_index/tab_count if "
        "an app's Search tab is elsewhere."
    )
)
@_focus_safe
def search_in_app(
    app: str,
    query: str,
    tab_index: int | None = None,
    tab_count: int | None = None,
) -> list[TextContent | ImageContent]:
    report, image = flows.search_in_app(SESSION, app, query, tab_index, tab_count)
    return _shot(image, report)


@server.tool(
    description=(
        "Open the running Expo dev-server project on the phone, in one call: "
        "Safari -> the dev-server URL -> 'Expo Go' -> confirm the handoff -> "
        "wait for the JS bundle to build. Leave url unset to use this Mac's LAN "
        "address automatically (the phone cannot reach localhost -- that is the "
        "phone itself). Set use_dev_build=true to pick 'Development Build' "
        "instead of 'Expo Go'."
    )
)
@_focus_safe
def open_expo_app(
    url: str | None = None, use_dev_build: bool = False
) -> list[TextContent | ImageContent]:
    report, image = flows.open_expo_app(SESSION, url, use_dev_build)
    return _shot(image, report)


@server.tool(
    description=(
        "Send a WhatsApp message. Opens WhatsApp, starts a new chat, searches "
        "the recipient, opens that chat and types the body. By default it STOPS "
        "THERE and returns a screenshot. Show it to the user, ask whether to "
        "send, and on yes call confirm_send() rather than re-running this tool "
        "with send=true. WhatsApp does not rank exact name matches first, so if "
        "the chat header shows the wrong person, retry with contact_index=2, "
        "3, ... to pick a different search result."
    )
)
@_focus_safe
def send_whatsapp(
    recipient: str, text: str, contact_index: int = 1, send: bool = False
) -> list[TextContent | ImageContent]:
    global PENDING
    report, image = flows.send_whatsapp(SESSION, recipient, text, contact_index, send)
    PENDING = (
        PendingDraft("whatsapp", recipient, text, contact_index)
        if (not send and "Drafted" in report)
        else None
    )
    return _shot(image, report)


@server.tool(
    description=(
        "Send a text message through the built-in Messages app (iMessage/SMS). "
        "For WhatsApp use send_whatsapp instead. Opens Messages, starts a new message, resolves "
        "the recipient to a real contact, and types the body. It STOPS THERE "
        "and returns a screenshot. Show it to the user, ask whether to send, "
        "and on yes call confirm_send() -- do NOT call this tool again with "
        "send=true, which needlessly rebuilds the whole draft. Fails loudly if "
        "no contact matches the name."
    )
)
@_focus_safe
def send_message(
    recipient: str, text: str, send: bool = False
) -> list[TextContent | ImageContent]:
    global PENDING
    report, image = flows.send_message(SESSION, recipient, text, send)
    PENDING = (
        PendingDraft("messages", recipient, text)
        if (not send and "Drafted" in report)
        else None
    )
    return _shot(image, report)


@server.tool(
    description=(
        "Tap a text field and type into it in one step, waiting for the field to "
        "actually take focus first. Use this instead of tap() + type_text(), "
        "which can drop keystrokes into a still-animating field. Set submit=true "
        "to press Return afterwards."
    )
)
@_focus_safe
def tap_and_type(
    x: float, y: float, text: str, submit: bool = False
) -> list[TextContent | ImageContent]:
    frame = SESSION.live_frame()
    _check(frame, x, y)
    # Tool coordinates are device points (consistent with tap/swipe); flows work
    # in screen fractions so they stay correct on any iPhone size.
    status, image = flows.type_into(
        SESSION, x / frame.device_w, y / frame.device_h, text, submit
    )
    return _shot(image, f"Typed {text!r} into the field at ({x:g}, {y:g}) ({status}).")


@server.tool(
    description=(
        "Scroll the current screen. direction is down, up, left, or right; "
        "amount is a fraction of the screen (0.1-0.85). Returns the settled screen."
    )
)
@_focus_safe
def scroll(direction: str = "down", amount: float = 0.6) -> list[TextContent | ImageContent]:
    before = SESSION.frame().image
    flows.scroll(SESSION, direction, amount)
    status, image = flows.settle(SESSION, timeout_s=4.0)
    # Say whether it actually moved. Hitting the end of a list is legitimate, so
    # this is not an error -- but reporting "scrolled" when nothing moved is how
    # a caller ends up scrolling forever looking for something.
    moved = mirror.frame_difference(before, image)
    if moved <= flows.CHANGED:
        return _shot(
            image,
            f"Scroll {direction} did not move the screen (delta {moved:.2f}) -- "
            "already at the end of the content, or this view does not scroll.",
        )
    return _shot(image, f"Scrolled {direction} by {amount:g} ({status}).")


@server.tool(
    description=(
        "Go back one screen by tapping the app's top-left back chevron. Fails "
        "loudly if nothing changes, which usually means you are already at a "
        "root screen. (The iOS edge-swipe gesture does not work through "
        "mirroring.)"
    )
)
@_focus_safe
def go_back() -> list[TextContent | ImageContent]:
    flows.back(SESSION)
    status, image = flows.settle(SESSION, timeout_s=4.0)
    return _shot(image, f"Went back ({status}).")


@server.tool(
    description=(
        "Page through the Home Screen and return one screenshot per page in a "
        "single call. Use this to find where an app lives instead of swiping and "
        "screenshotting repeatedly. Stops early at the last page."
    )
)
@_focus_safe
def survey_home(max_pages: int = 4) -> list[TextContent | ImageContent]:
    pages = flows.survey_home(SESSION, max_pages)
    frame = SESSION.live_frame()
    out: list[TextContent | ImageContent] = [
        TextContent(
            type="text",
            text=(
                f"Home Screen: {len(pages)} page(s). Coordinate space "
                f"{frame.device_w} x {frame.device_h} device points. "
                "Note each image is one page, in order."
            ),
        )
    ]
    for label, image in pages:
        out.append(TextContent(type="text", text=label))
        out.append(_png_content(mirror.downscale(image)))
    return out


@server.tool(
    description=(
        "Send the message that is already drafted on screen, after the user has "
        "confirmed it. This is the fast path: it presses Send directly on the "
        "existing draft instead of rebuilding it, and only screenshots "
        "afterwards to prove the message went and went to the right person. If "
        "the tap does not register it rebuilds the draft and sends it in the "
        "same call, without asking again. Use this instead of calling a send_* "
        "tool a second time with send=true."
    )
)
@_focus_safe
def confirm_send() -> list[TextContent | ImageContent]:
    global PENDING
    if PENDING is None:
        raise MirrorError(
            "Nothing is drafted. Call send_message() or send_whatsapp() first, "
            "show the user the draft, and confirm_send() once they agree."
        )
    draft = PENDING

    # Straight to the send button -- the draft was already screenshotted and
    # approved, so a second pre-send capture would only add latency.
    sent, status, image = flows.tap_send(SESSION, draft.app)
    if sent:
        PENDING = None
        return _shot(
            image,
            f"Sent {draft.text!r} to {draft.recipient!r} on {draft.app} "
            f"({status}). Verified: the composer is empty.",
        )

    # The draft was still sitting in the composer, so the tap missed or the
    # screen had moved on. Rebuild and send in one go rather than bouncing back
    # to the user, who has already said yes.
    if draft.app == "whatsapp":
        report, image = flows.send_whatsapp(
            SESSION, draft.recipient, draft.text, draft.contact_index, send=True
        )
    else:
        report, image = flows.send_message(
            SESSION, draft.recipient, draft.text, send=True
        )
    PENDING = None
    return _shot(image, "Send did not register, so the draft was rebuilt. " + report)


@server.tool(
    description=(
        "List every piece of text on the iPhone screen with the exact device "
        "coordinates to tap it. Use this instead of guessing positions from a "
        "screenshot -- then tap(x, y), or just call tap_text(). Returns text "
        "only by default, which is far cheaper than an image; set "
        "include_image=true if you also need to see the screen."
    )
)
@_focus_safe
def describe_screen(include_image: bool = False) -> list[TextContent | ImageContent]:
    frame = SESSION.live_frame()
    elements = vision.recognize(frame.image, frame.device_w, frame.device_h)
    lines = [
        f"{len(elements)} text elements on screen. Coordinates are device "
        f"points in a {frame.device_w} x {frame.device_h} space -- pass them "
        "straight to tap(x, y).",
    ]
    lines += [
        f"  ({element.x:6.0f}, {element.y:6.0f})  {element.text}"
        for element in elements
    ]
    out: list[TextContent | ImageContent] = [
        TextContent(type="text", text="\n".join(lines))
    ]
    if include_image:
        out.append(_png_content(mirror.downscale(frame.image)))
    return out


@server.tool(
    description=(
        "Tap on-screen text by name, e.g. tap_text('Wallet'). Finds the text "
        "with OCR and taps its centre, so it needs no coordinates and survives "
        "layout changes. Prefers an exact label match over a longer string that "
        "merely contains it. Use occurrence=2, 3, ... when the same text "
        "appears more than once."
    )
)
@_focus_safe
def tap_text(text: str, occurrence: int = 1) -> str:
    frame = SESSION.live_frame()
    elements = vision.recognize(frame.image, frame.device_w, frame.device_h)
    matches = vision.find(elements, text)
    if not matches:
        sample = ", ".join(repr(e.text) for e in elements[:12])
        raise MirrorError(
            f"No on-screen text matching {text!r}. Visible text includes: "
            f"{sample}. Call describe_screen() to see everything."
        )
    index = max(1, occurrence) - 1
    if index >= len(matches):
        raise MirrorError(
            f"Only {len(matches)} match(es) for {text!r}, so occurrence="
            f"{occurrence} does not exist."
        )
    target = matches[index]
    gx, gy = inputs.tap(frame, target.x, target.y)
    others = f" ({len(matches)} matches; used #{index + 1})" if len(matches) > 1 else ""
    return (
        f"Tapped {target.text!r} at device ({target.x:.0f}, {target.y:.0f})"
        f" -> screen ({gx:.0f}, {gy:.0f}){others}."
    )


@server.tool(
    description=(
        "Press and hold at a device point. Use for context menus, previews, "
        "and picking up an icon to move it. Distinct from tap: hold_ms must "
        "clear iOS's ~500ms long-press threshold."
    )
)
@_focus_safe
def long_press(x: float, y: float, hold_ms: int = 700) -> str:
    frame = SESSION.live_frame()
    _check(frame, x, y)
    gx, gy = inputs.long_press(frame, x, y, hold_ms)
    return (
        f"Held ({x:g}, {y:g}) for {hold_ms}ms -> screen ({gx:.0f}, {gy:.0f}). "
        "Call wait_until_settled() then screenshot() to see the result."
    )


@server.tool(
    description="Double-tap at a device point, e.g. to zoom or to like."
)
@_focus_safe
def double_tap(x: float, y: float) -> str:
    frame = SESSION.live_frame()
    _check(frame, x, y)
    gx, gy = inputs.double_tap(frame, x, y)
    return f"Double-tapped ({x:g}, {y:g}) -> screen ({gx:.0f}, {gy:.0f})."


@server.tool(
    description=(
        "Drag an item from one device point to another: press and hold until "
        "it lifts, move, dwell at the destination, release. Use for reordering "
        "and drag-and-drop. For scrolling or flicking use swipe instead -- a "
        "swipe deliberately stays under the long-press threshold, a drag "
        "deliberately exceeds it."
    )
)
@_focus_safe
def drag(
    x1: float, y1: float, x2: float, y2: float, hold_ms: int = 700, move_ms: int = 900
) -> str:
    frame = SESSION.live_frame()
    _check(frame, x1, y1)
    _check(frame, x2, y2)
    start, end = inputs.drag(frame, x1, y1, x2, y2, hold_ms, move_ms)
    return (
        f"Dragged ({x1:g}, {y1:g}) -> ({x2:g}, {y2:g}) "
        f"[screen {start[0]:.0f},{start[1]:.0f} -> {end[0]:.0f},{end[1]:.0f}]. "
        "Call wait_until_settled() then screenshot() to see the result."
    )


@server.tool(
    description=(
        "Scroll until some text comes into view, then optionally tap it. OCR "
        "only sees what is rendered, so anything below the fold is invisible to "
        "describe_screen and tap_text -- use this to reach it. Stops early when "
        "the list stops moving."
    )
)
@_focus_safe
def scroll_to(
    text: str, direction: str = "down", tap: bool = False, max_scrolls: int = 10
) -> str:
    element, scrolls, frame = flows.scroll_to_text(SESSION, text, direction, max_scrolls)
    if element is None:
        raise MirrorError(
            f"Scrolled {direction} {scrolls} time(s) without finding {text!r}. "
            "It may be spelled differently, be an icon rather than text, or lie "
            "in the other direction."
        )
    where = f"at device ({element.x:.0f}, {element.y:.0f}) after {scrolls} scroll(s)"
    if not tap:
        return f"Found {element.text!r} {where}. Pass tap=true to tap it."
    gx, gy = inputs.tap(frame, element.x, element.y)
    return f"Found and tapped {element.text!r} {where} -> screen ({gx:.0f}, {gy:.0f})."


@server.tool(
    description=(
        "Open a URL on the phone through Safari. Works with web pages and with "
        "deep links such as exp:// or maps://, which hand off to another app "
        "after iOS asks for confirmation. Verifies the URL actually committed "
        "rather than assuming Return worked."
    )
)
@_focus_safe
def open_url(url: str) -> list[TextContent | ImageContent]:
    ok, report, image = flows.open_url(SESSION, url)
    if not ok:
        raise MirrorError(report)
    # Deep links raise a confirmation alert; dismissing it is the caller's
    # choice, so report it rather than tapping through silently.
    if flows._alert_showing(SESSION):
        report += (
            " iOS is asking whether to open it in another app -- tap 'Open' "
            "(around device x=0.85 of the width, y=0.51 of the height) to confirm."
        )
    return _shot(image, report)


@server.tool(
    description="Report whether the phone is currently portrait or landscape."
)
def get_orientation() -> str:
    frame = SESSION.live_frame()
    orientation = flows.device_orientation(SESSION)
    return (
        f"{orientation} -- mirrored screen is {frame.content_w:g}x"
        f"{frame.content_h:g} points, coordinate space {frame.device_w}x"
        f"{frame.device_h}."
    )


@server.tool(
    description=(
        "Wait until some text appears on screen, or disappears with gone=true. "
        "Use this instead of wait_until_settled when you know what you are "
        "waiting for -- it is more precise and usually faster, and it works on "
        "screens that never go still (spinners, autoplaying video)."
    )
)
@_focus_safe
def wait_for_text(text: str, timeout_s: float = 15.0, gone: bool = False) -> str:
    found, element, _frame = flows.wait_for_text(SESSION, text, timeout_s, gone)
    if gone:
        if found:
            return f"{text!r} is no longer on screen."
        raise MirrorError(
            f"{text!r} was still on screen after {timeout_s:g}s"
            + (f" (at {element.x:.0f}, {element.y:.0f})" if element else "")
            + "."
        )
    if found and element is not None:
        return (
            f"{element.text!r} appeared at device ({element.x:.0f}, "
            f"{element.y:.0f}) -- tap there, or use tap_text()."
        )
    raise MirrorError(
        f"{text!r} did not appear within {timeout_s:g}s. Call describe_screen() "
        "to see what is actually on screen."
    )


@server.tool(
    description=(
        "Back out to the current app's root screen by tapping its back chevron "
        "until nothing more changes. Use it after open_app when a flow needs a "
        "known starting point -- iOS resumes an app wherever it was last left, "
        "which is the usual reason a sequence of taps goes somewhere unexpected. "
        "(Force-quitting an app is not possible through mirroring.)"
    )
)
@_focus_safe
def go_to_root(max_steps: int = 5) -> list[TextContent | ImageContent]:
    steps, frame = flows.go_to_root(SESSION, max_steps)
    note = (
        f"Backed out {steps} screen(s) to the app root."
        if steps
        else "Already at the app root -- nothing to back out of."
    )
    return _shot(frame.image, note, frame)


@server.tool(
    description=(
        "Start recording what you do on the phone. Drive it by hand -- taps, "
        "swipes, typing, scrolling -- then call stop_recording(name) to save it "
        "as a replayable skill. Recording only watches; it never alters your "
        "input, and it ignores anything this server itself sends."
    )
)
def start_recording() -> str:
    ax.ensure_accessibility()
    RECORDER.start()
    return (
        "Recording. Drive the phone by hand, then call stop_recording('a-name'). "
        "Only actions on the mirrored screen are captured."
    )


@server.tool(
    description=(
        "Stop recording and save what was captured as a named skill. Set app to "
        "the app it should open first (e.g. 'Instagram'), so replaying it does "
        "not depend on that app already being frontmost."
    )
)
def stop_recording(name: str, app: str | None = None, description: str = "") -> str:
    steps = RECORDER.stop()
    if not steps:
        return (
            "Recorded nothing -- no input landed on the mirrored screen. The "
            "skill was not saved."
        )
    frame = SESSION.live_frame()
    skill = skills.Skill(
        name=skills.normalise(name),
        steps=steps,
        app=app,
        description=description,
        device=f"{frame.device_w}x{frame.device_h}",
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    path = skills.save(skill)
    return f"Saved to {path}.\n{skill.summary()}"


@server.tool(description="List saved skills and what each one does.")
def list_skills() -> str:
    saved = skills.load_all()
    if not saved:
        return (
            f"No skills saved yet (looking in {skills.skills_dir()}). Use "
            "start_recording() to make one."
        )
    return "\n\n".join(skill.summary() for skill in saved)


@server.tool(description="Show a saved skill's steps without running it.")
def get_skill(name: str) -> str:
    return skills.load(name).summary()


@server.tool(
    description=(
        "Replay a saved skill. Opens its app first if it has one, runs each "
        "step, and reports any step that changed nothing on screen -- which is "
        "how a skill that has drifted out of date shows up."
    )
)
@_focus_safe
def run_skill(name: str) -> list[TextContent | ImageContent]:
    skill = skills.load(name)
    report, image = skills.replay(SESSION, skill)
    return _shot(image, report)


@server.tool(description="Delete a saved skill.")
def delete_skill(name: str) -> str:
    return (
        f"Deleted skill {name!r}." if skills.delete(name)
        else f"No skill named {name!r}."
    )


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
