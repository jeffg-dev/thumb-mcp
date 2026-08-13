"""Window discovery, frame capture, and device-point <-> screen-point mapping.

The mirroring window is a rounded, transparent-bordered window: the streamed
device screen sits inside a fixed chrome inset (measured at 8pt left/right,
38pt top, 8pt bottom on macOS 15/26). Rather than trust those constants, every
capture re-derives the content rect from the frame's alpha channel -- the
chrome is fully transparent, the device screen is fully opaque -- and only falls
back to the constants if that detection looks implausible.

Nothing about the transform is cached. Window bounds, zoom level, and the
content rect are recomputed on every single call, because the user can move,
resize, or re-zoom the window at any moment.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import Quartz
from PIL import Image as PILImage
from PIL import ImageChops, ImageStat

from .errors import (
    MirroringNotConnected,
    CaptureFailed,
    MirroringNotRunning,
    ScreenRecordingDenied,
    WindowNotFound,
)

OWNER_NAME = "iPhone Mirroring"

# Chrome inset in window points (left, top, right, bottom). Only a fallback --
# alpha detection is authoritative. Constant across every zoom level.
FALLBACK_INSETS = (8.0, 38.0, 8.0, 8.0)

# Known iPhone logical (point) resolutions, portrait. Used to give the tap
# coordinate space a stable, human-recognisable size that does not change when
# the user zooms the window.
KNOWN_DEVICES: list[tuple[int, int, str]] = [
    (320, 568, "iPhone SE (1st gen) / 5s"),
    (375, 667, "iPhone 6/7/8 / SE (2nd-3rd gen)"),
    (414, 736, "iPhone 6/7/8 Plus"),
    (375, 812, "iPhone X/XS/11 Pro / 12-13 mini"),
    (414, 896, "iPhone XR/XS Max/11/11 Pro Max"),
    (390, 844, "iPhone 12/12 Pro/13/13 Pro/14"),
    (428, 926, "iPhone 12/13 Pro Max / 14 Plus"),
    (393, 852, "iPhone 14 Pro/15/15 Pro/16"),
    (430, 932, "iPhone 14 Pro Max/15 Plus/15 Pro Max/16 Plus"),
    (402, 874, "iPhone 16 Pro"),
    (440, 956, "iPhone 16 Pro Max"),
]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    pid: int
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Frame:
    """One captured mirroring frame, cropped to just the device screen."""

    image: PILImage.Image  # RGB, native capture resolution, chrome removed
    window: WindowInfo
    scale: float  # capture pixels per window point (2.0 on Retina)
    content_x: float  # device screen offset inside the window, in points
    content_y: float
    content_w: float  # device screen size inside the window, in points
    content_h: float
    device_w: int  # nominal device coordinate space
    device_h: int
    device_name: str

    def to_global(self, x: float, y: float) -> tuple[float, float]:
        """Device point -> global screen point (top-left origin, as CGEvent uses)."""
        gx = self.window.x + self.content_x + x * (self.content_w / self.device_w)
        gy = self.window.y + self.content_y + y * (self.content_h / self.device_h)
        return gx, gy


def ensure_screen_recording() -> None:
    if not Quartz.CGPreflightScreenCaptureAccess():
        raise ScreenRecordingDenied()


def find_window() -> WindowInfo:
    """Locate the mirroring window. Never cached by callers across failures."""
    on_screen = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    every = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )

    def owned(windows) -> list:
        return [w for w in (windows or []) if w.get("kCGWindowOwnerName") == OWNER_NAME]

    if not owned(every):
        raise MirroringNotRunning()

    candidates = []
    for w in owned(on_screen):
        if w.get("kCGWindowLayer") != 0:
            continue  # menu-bar extras, popovers, tooltips
        bounds = w.get("kCGWindowBounds") or {}
        width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
        if width < 120 or height < 200:
            continue  # not the mirror surface
        candidates.append((width * height, w, bounds))

    # The mirror surface is titled exactly "iPhone Mirroring". The setup sheet is
    # titled "Welcome to iPhone Mirroring" and is *larger*, so picking by area
    # alone would happily aim taps at the onboarding dialog.
    exact = [c for c in candidates if (c[1].get("kCGWindowName") or "") == OWNER_NAME]
    if exact:
        _, win, bounds = max(exact, key=lambda c: c[0])
    else:
        titles = " | ".join(
            (c[1].get("kCGWindowName") or "?") for c in candidates
        )
        if any("welcome" in (c[1].get("kCGWindowName") or "").lower() for c in candidates):
            raise MirroringNotConnected()
        # Titles are unavailable without Screen Recording; fall back to geometry.
        portrait = [c for c in candidates if float(c[2]["Height"]) > float(c[2]["Width"])]
        if not portrait:
            if any((w.get("kCGWindowName") or "") == OWNER_NAME for w in owned(every)):
                raise WindowNotFound(
                    "its mirroring window exists but is off-screen (minimised, "
                    "hidden, or on another Space)"
                )
            raise WindowNotFound(
                f"no window looks like the mirror surface (saw: {titles or 'none'})"
            )
        _, win, bounds = max(portrait, key=lambda c: c[0])
    return WindowInfo(
        window_id=int(win["kCGWindowNumber"]),
        pid=int(win["kCGWindowOwnerPID"]),
        x=float(bounds["X"]),
        y=float(bounds["Y"]),
        width=float(bounds["Width"]),
        height=float(bounds["Height"]),
    )


def launch_app(timeout_s: float = 12.0) -> bool:
    """Launch iPhone Mirroring and wait for it to come up.

    The app exits on its own after a session ends, so a long-running server
    otherwise dies with "not running" and needs a human to reopen it.
    """
    try:
        subprocess.run(["open", "-a", "iPhone Mirroring"], capture_output=True,
                       timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if mirroring_pid() is not None:
            return True
    return False


def mirroring_pid() -> int | None:
    """PID of the iPhone Mirroring app, whether or not it has an on-screen window."""
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in infos or []:
        if w.get("kCGWindowOwnerName") == OWNER_NAME:
            return int(w["kCGWindowOwnerPID"])
    return None


def window_by_id(window_id: int) -> WindowInfo | None:
    """Re-read a known window's *current* bounds, or None if it is gone.

    The window ID is stable for the life of a mirroring session, but the bounds
    are not: the user can drag or re-zoom the window between any two calls. So
    the ID is worth caching and the geometry never is.
    """
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionIncludingWindow, window_id
    )
    for w in infos or []:
        if int(w.get("kCGWindowNumber", -1)) != window_id:
            continue
        if w.get("kCGWindowOwnerName") != OWNER_NAME:
            return None
        bounds = w.get("kCGWindowBounds") or {}
        width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
        if width < 120 or height < 200:
            return None
        return WindowInfo(
            window_id=window_id,
            pid=int(w["kCGWindowOwnerPID"]),
            x=float(bounds["X"]),
            y=float(bounds["Y"]),
            width=width,
            height=height,
        )
    return None


def _cgimage_to_pil(img) -> PILImage.Image:
    width = Quartz.CGImageGetWidth(img)
    height = Quartz.CGImageGetHeight(img)
    stride = Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    if data is None:
        raise CaptureFailed("the window image had no backing pixel data")
    pil = PILImage.frombuffer(
        "RGBA", (stride // 4, height), bytes(data), "raw", "BGRA", 0, 1
    )
    return pil.crop((0, 0, width, height))


def _capture_cgimage(window_id: int) -> PILImage.Image | None:
    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming
        | Quartz.kCGWindowImageBestResolution,
    )
    if img is None:
        return None
    return _cgimage_to_pil(img)


def _capture_screencapture_cli(window_id: int) -> PILImage.Image | None:
    """Fallback for hosts where the (deprecated) CGWindowList capture is blocked."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = handle.name
    try:
        proc = subprocess.run(
            ["screencapture", "-x", "-o", f"-l{window_id}", path],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0 or not os.path.getsize(path):
            return None
        with PILImage.open(path) as opened:
            return opened.convert("RGBA")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _content_bbox(rgba: PILImage.Image, scale: float) -> tuple[int, int, int, int]:
    """Device-screen bbox in capture pixels, from the opaque region."""
    width, height = rgba.size
    alpha = rgba.getchannel("A").point(lambda v: 255 if v > 200 else 0)
    box = alpha.getbbox()
    if box is not None:
        left, top, right, bottom = box
        # Sanity-check: the screen must dominate the window and stay portrait-ish.
        if (right - left) >= width * 0.5 and (bottom - top) >= height * 0.5:
            return left, top, right, bottom
    inset_l, inset_t, inset_r, inset_b = FALLBACK_INSETS
    return (
        round(inset_l * scale),
        round(inset_t * scale),
        width - round(inset_r * scale),
        height - round(inset_b * scale),
    )


def _identify_device(content_w: float, content_h: float) -> tuple[int, int, str]:
    """Pick the nominal coordinate space for the streamed screen.

    At maximum zoom the app renders 1:1, so an exact point match identifies the
    device outright. Otherwise the aspect ratio picks the closest known device.

    Only self-consistency matters here: screenshot() advertises this space and
    tap() maps from it, so a near-miss on the model still lands taps exactly
    where they were aimed -- it just labels the space slightly differently.
    """
    override = os.environ.get("IPHONE_MIRROR_DEVICE_SIZE", "").lower().strip()
    if "x" in override:
        try:
            w_str, h_str = override.split("x", 1)
            return int(w_str), int(h_str), "configured via IPHONE_MIRROR_DEVICE_SIZE"
        except ValueError:
            pass

    for dev_w, dev_h, name in KNOWN_DEVICES:
        if abs(content_w - dev_w) <= 2 and abs(content_h - dev_h) <= 2:
            return dev_w, dev_h, name

    if content_h <= 0:
        raise CaptureFailed("the mirrored screen had zero height")
    aspect = content_w / content_h
    dev_w, dev_h, name = min(
        KNOWN_DEVICES, key=lambda d: abs((d[0] / d[1]) - aspect)
    )
    return dev_w, dev_h, name


def capture_frame(window: WindowInfo | None = None) -> Frame:
    """Capture the mirroring window and crop it to the device screen.

    Resolves the window if not given. Callers that cache a WindowInfo should
    retry with ``window=None`` on CaptureFailed -- the window ID changes every
    time mirroring reconnects.
    """
    ensure_screen_recording()
    win = window or find_window()

    rgba = _capture_cgimage(win.window_id) or _capture_screencapture_cli(win.window_id)
    if rgba is None:
        raise CaptureFailed(
            f"window {win.window_id} returned no image (it likely closed or "
            "mirroring reconnected)"
        )

    width, _ = rgba.size
    if win.width <= 0:
        raise CaptureFailed("the mirroring window reported zero width")
    scale = width / win.width

    left, top, right, bottom = _content_bbox(rgba, scale)
    screen = rgba.crop((left, top, right, bottom)).convert("RGB")

    content_x, content_y = left / scale, top / scale
    content_w, content_h = (right - left) / scale, (bottom - top) / scale
    device_w, device_h, device_name = _identify_device(content_w, content_h)

    return Frame(
        image=screen,
        window=win,
        scale=scale,
        content_x=content_x,
        content_y=content_y,
        content_w=content_w,
        content_h=content_h,
        device_w=device_w,
        device_h=device_h,
        device_name=device_name,
    )


def downscale(image: PILImage.Image, max_edge: int | None = None) -> PILImage.Image:
    """Shrink so the longest edge is at most ``max_edge`` px, to keep tokens sane.

    A mirrored phone is tall and narrow: capping the *long* edge is what actually
    bounds the token cost. Never upscales.
    """
    limit = max_edge or _env_int("IPHONE_MIRROR_MAX_EDGE", 1024)
    longest = max(image.size)
    if longest <= limit:
        return image
    ratio = limit / longest
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, PILImage.LANCZOS)


def frame_difference(a: PILImage.Image, b: PILImage.Image) -> float:
    """Mean per-pixel difference (0..255) between two frames, size-normalised."""
    small = (96, 96)
    ga = a.convert("L").resize(small, PILImage.BILINEAR)
    gb = b.convert("L").resize(small, PILImage.BILINEAR)
    return ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0]
