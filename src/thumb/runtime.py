"""Shared device session and diagnostics; no MCP registration."""
from __future__ import annotations
import time
from dataclasses import dataclass
import Quartz
from . import ax, mirror
from .mirror import Frame
from .errors import (AccessibilityDenied, MirrorError, MirroringNotRunning,
    MirroringPaused, ScreenRecordingDenied, WindowNotFound, host_app)
TRANSIENT_WAIT_S = 12.0

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

def device_info() -> str:
    lines: list[str] = []
    screen_ok = bool(Quartz.CGPreflightScreenCaptureAccess())
    ax_ok = ax.accessibility_ok()
    lines.append(f"Permission target executable (best-effort): {host_app()}")
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




