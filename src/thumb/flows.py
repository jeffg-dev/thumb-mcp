"""Shortcuts -- the behaviour half of the shortcut system.

Driving a phone one primitive at a time is slow and error-prone: every tap needs
a screenshot to aim it, every screenshot is a round trip, and a mis-aimed tap
costs several more. But most real intents ("open Instagram", "search for X",
"scroll down") have a *known-good* sequence that never needs to look at pixels.

Each flow here encodes one such sequence end to end and returns a single settled
screenshot, so the caller sees one image instead of five.

Layout constants live in :mod:`landmarks`; this module holds only the steps.

Adding a new shortcut
---------------------
1. If it needs a new position, add it to ``landmarks`` (as a screen fraction).
2. Write a function here taking ``session`` first and returning
   ``(report, image)`` -- use :func:`settle` so it returns a stable frame.
3. Register a thin tool wrapper in ``server`` with ``@_flow_tool``.

Anything that changes the screen must be *verified*, not assumed: menu commands
and taps both no-op silently, and a flow that carries on regardless ends up
typing into the wrong screen.
"""

from __future__ import annotations

import subprocess
import time

from PIL import Image as PILImage
from PIL import ImageStat

from . import ax, inputs, landmarks, mirror, vision
from .errors import MirrorError

# A frame delta above this means the screen genuinely changed, not just noise
# from a cursor blink or a live-updating clock.
CHANGED = 3.0


# --------------------------------------------------------------------------
# Primitives shared by every flow
# --------------------------------------------------------------------------

def settle(
    session,
    timeout_s: float = 5.0,
    stable_for_s: float = 0.35,
    threshold: float = 1.2,
    poll_s: float = 0.09,
) -> tuple[str, PILImage.Image]:
    """Poll until the screen stops changing. Returns (summary, final frame)."""
    deadline = time.monotonic() + max(0.1, timeout_s)
    previous = session.frame().image
    stable_since: float | None = None
    polls = 1

    while time.monotonic() < deadline:
        time.sleep(poll_s)
        current = session.frame().image
        polls += 1
        difference = mirror.frame_difference(previous, current)
        previous = current
        now = time.monotonic()
        if difference <= threshold:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stable_for_s:
                return f"settled after {polls} frames", current
        else:
            stable_since = None
    return f"still changing after {timeout_s:g}s ({polls} frames)", previous


def _band_brightness(image, top: float, bottom: float) -> float:
    """Mean luminance of a horizontal band -- used to spot dimming overlays."""
    height = image.height
    box = (0, int(height * top), image.width, int(height * bottom))
    return ImageStat.Stat(image.crop(box).convert("L")).mean[0]


def assert_changed(session, before, what: str, threshold: float = CHANGED) -> float:
    """Fail loudly when an action that must change the screen did not.

    Silent no-ops are the worst failure mode here: the tool reports success, the
    caller keeps driving a screen it never left, and the damage surfaces several
    steps later. Three tools shipped in this state before this helper existed --
    vertical scroll, Spotlight's Return, and the handoff-dialog check.
    """
    delta = mirror.frame_difference(before, session.frame().image)
    if delta <= threshold:
        raise MirrorError(
            f"{what} did not change the screen (frame delta {delta:.2f}). "
            "The action did not reach the phone, or the control was not where "
            "it was expected."
        )
    return delta


def _changed_since(session, before: PILImage.Image, threshold: float = CHANGED) -> bool:
    return mirror.frame_difference(before, session.frame().image) > threshold


def tap_at(session, fx: float, fy: float):
    """Tap a point given as screen fractions."""
    frame = session.live_frame()
    inputs.tap(frame, frame.device_w * fx, frame.device_h * fy)
    return frame


def swipe_between(session, x1, y1, x2, y2, duration_ms: int = 280):
    """Swipe between two points given as screen fractions."""
    frame = session.live_frame()
    inputs.swipe(
        frame,
        frame.device_w * x1, frame.device_h * y1,
        frame.device_w * x2, frame.device_h * y2,
        duration_ms,
    )
    return frame


def clear_field(pid: int, max_chars: int = 30) -> None:
    """Empty a text field with repeated backspaces.

    Cmd-A is the obvious way and it does not work: iOS does not honour the
    synthesised Command flag here, so the event arrives as a literal "a".
    Backspace needs no modifier and is a no-op on an already-empty field.
    """
    inputs.press_key_repeat(pid, "delete", max_chars, delay_s=0.012)


def type_into(session, fx: float, fy: float, text: str, submit: bool = False):
    """Focus a field, wait for focus to land, clear it, then type.

    The settle between tapping and typing is load-bearing: a field that is still
    animating into focus silently swallows keystrokes and ends up focused but
    empty.
    """
    tap_at(session, fx, fy)
    settle(session, timeout_s=4.0, stable_for_s=0.3)
    pid = session.live_frame().window.pid
    clear_field(pid)
    inputs.type_text(pid, text)
    if submit:
        settle(session, timeout_s=3.0, stable_for_s=0.25)
        inputs.press_key(pid, "return")
    return settle(session, timeout_s=6.0, stable_for_s=0.4)


# --------------------------------------------------------------------------
# Verified navigation
# --------------------------------------------------------------------------

def go_home(session, pid: int) -> bool:
    """Go to the Home Screen. Already-home is indistinguishable and also fine."""
    before = session.frame().image
    ax.press_menu_item(pid, ax.MENU_HOME)
    settle(session, timeout_s=3.0)
    if not _changed_since(session, before):
        inputs.press_command_key(pid, "1")  # keyboard fallback
        settle(session, timeout_s=3.0)
    return True


def open_spotlight(session, pid: int) -> bool:
    """Open Spotlight, confirming it is actually on screen.

    The View > Spotlight menu item is not reliable -- pressed straight after
    Home it frequently no-ops while the Home Screen is still animating, and the
    caller then types into the Home Screen, which does nothing. So verify, and
    fall back to Cmd-3 and then to the swipe-down gesture.
    """
    for attempt in range(3):
        before = session.frame().image
        if attempt == 0:
            ax.press_menu_item(pid, ax.MENU_SPOTLIGHT)
        elif attempt == 1:
            inputs.press_command_key(pid, "3")
        else:
            swipe_between(session, 0.5, 0.30, 0.5, 0.72, duration_ms=280)
        settle(session, timeout_s=3.0, stable_for_s=0.4)
        if _changed_since(session, before):
            time.sleep(0.25)  # let the field take keyboard focus
            return True
    return False


# --------------------------------------------------------------------------
# Gestures
# --------------------------------------------------------------------------

SCROLL_VECTORS = {
    # Scrolling *down* (revealing content below) means dragging *up*.
    "down": lambda a: (0.5, 0.5 + a / 2, 0.5, 0.5 - a / 2),
    "up": lambda a: (0.5, 0.5 - a / 2, 0.5, 0.5 + a / 2),
    "left": lambda a: (0.5 + a / 2, 0.5, 0.5 - a / 2, 0.5),
    "right": lambda a: (0.5 - a / 2, 0.5, 0.5 + a / 2, 0.5),
}


def scroll(session, direction: str = "down", amount: float = 0.6):
    """Scroll the content area by roughly a fraction of the screen.

    Vertical scrolling goes through wheel events, not a drag: a click-drag
    simply does not scroll iOS lists through mirroring. Horizontal paging *is* a
    drag, because that is a swipe gesture rather than a scroll.
    """
    key = direction.strip().lower()
    if key not in SCROLL_VECTORS:
        raise MirrorError(
            f"Unknown scroll direction {direction!r}. Use down, up, left, or right."
        )
    amount = max(0.05, min(0.85, amount))

    if key in ("down", "up"):
        frame = session.live_frame()
        # ~1px of wheel travel per device point, negative to move down the list.
        pixels = int(frame.device_h * amount)
        inputs.scroll_wheel(
            frame,
            frame.device_w * 0.5,
            frame.device_h * 0.5,
            -pixels if key == "down" else pixels,
        )
        return
    swipe_between(session, *SCROLL_VECTORS[key](amount), duration_ms=260)


def back(session):
    """Go back by tapping the app's own back chevron, top-left.

    The iOS edge-swipe-to-go-back gesture does NOT register through mirroring:
    a drag starting at the screen edge does nothing, and a real edge swipe would
    have to begin off-screen, which is unreachable here. Nearly every iOS screen
    with a back action puts a chevron in the same top-left spot, so tap that
    instead -- and verify, since on a root screen there is nothing to go back to.
    """
    before = session.frame().image
    tap_at(session, *landmarks.BACK_CHEVRON)
    settle(session, timeout_s=4.0, stable_for_s=0.3)
    return assert_changed(session, before, "go_back")


def home_page(session, direction: str = "next"):
    """Page between Home Screen pages, starting from empty space above the dock."""
    y = landmarks.HOME_EMPTY_BAND
    if direction == "next":
        swipe_between(session, 0.86, y, 0.14, y, duration_ms=260)
    else:
        swipe_between(session, 0.14, y, 0.86, y, duration_ms=260)


# --------------------------------------------------------------------------
# Composite shortcuts
# --------------------------------------------------------------------------

def open_app(session, name: str, timeout_s: float = 8.0):
    """Home -> Spotlight -> type -> launch the top hit.

    Spotlight beats hunting for an icon: one deterministic path regardless of
    which Home Screen page the app lives on, no pixel search, and Return opens
    the top hit directly.
    """
    app = landmarks.canonical_name(name)
    frame = session.live_frame()
    pid = frame.window.pid

    # Focus the window first: menu items fire even when it is not frontmost, so
    # the screen can change while keyboard focus is still on the user's editor,
    # and every keystroke we then send lands nowhere.
    inputs.focus(pid)

    go_home(session, pid)
    if not open_spotlight(session, pid):
        return (
            "Could not open Spotlight on the phone -- the View menu command, "
            "Cmd-3 and the swipe gesture all left the screen unchanged. Check "
            "that the iPhone Mirroring window is visible and connected.",
            session.frame().image,
        )

    # Capture the empty-Spotlight reference *after* clearing, not before.
    # Spotlight retains the previous query, so a "blank" snapshot taken first
    # still shows the old results -- retyping the same app then produces an
    # identical screen and the did-the-typing-land check false-negatives.
    clear_field(pid)
    settle(session, timeout_s=2.0, stable_for_s=0.25)
    blank = session.frame().image
    inputs.type_text(pid, app)
    settle(session, timeout_s=3.0, stable_for_s=0.3)
    results = session.frame().image

    # If the query never reached the field the results are unchanged, and Return
    # would launch whatever the *stale* query matched. Retry once with focus
    # re-asserted rather than acting on a screen we did not cause.
    if mirror.frame_difference(blank, results) < CHANGED:
        inputs.focus(pid)
        clear_field(pid)
        settle(session, timeout_s=2.0, stable_for_s=0.25)
        blank = session.frame().image
        inputs.type_text(pid, app)
        settle(session, timeout_s=3.0, stable_for_s=0.3)
        results = session.frame().image
        if mirror.frame_difference(blank, results) < CHANGED:
            return (
                f"Could not type {app!r} into Spotlight -- keystrokes are not "
                "reaching the phone. Check the mirroring window is visible.",
                results,
            )

    # Tap the Top Hit rather than pressing Return. Return frequently does NOT
    # launch the highlighted app -- Spotlight just sits there with the query
    # typed -- and the caller then drives a screen it never actually left.
    # Tapping the icon is unambiguous.
    tap_at(session, *landmarks.SPOTLIGHT_TOP_HIT)
    # Cap the post-launch wait: media-heavy apps (an autoplaying feed) never go
    # fully still, and the app is usable long before the timeout expires.
    status, final = settle(session, timeout_s=min(timeout_s, 4.5), stable_for_s=0.35)

    launched = mirror.frame_difference(results, final) > 8
    if not launched:
        # Fall back to Return, in case the Top Hit row was laid out differently.
        inputs.press_key(pid, "return")
        status, final = settle(session, timeout_s=min(timeout_s, 4.5), stable_for_s=0.35)
        launched = mirror.frame_difference(results, final) > 8
    report = (
        f"Opened {app!r} via Spotlight ({status})."
        if launched
        else f"Searched {app!r} in Spotlight but the app never opened "
        f"({status}) -- it may not be installed, or the top hit was not an app. "
        "Check the screenshot."
    )
    return report, final


def search_in_app(
    session,
    app: str,
    query: str,
    tab_index: int | None = None,
    tab_count: int | None = None,
):
    """Open an app, go to its Search tab, and run a query -- no screenshots.

    The whole path comes from the landmark table rather than from pixels. Only
    the final screen is captured, because that is the only frame worth showing.
    """
    profile, known = landmarks.profile_for(app)
    slot, count = profile.search_tab
    if tab_index is not None:
        slot = tab_index
    if tab_count is not None:
        count = tab_count

    report, image = open_app(session, app)
    if "Could not" in report:
        return report, image

    tap_at(session, *landmarks.nav_slot(slot, count))
    settle(session, timeout_s=4.0, stable_for_s=0.3)

    status, image = type_into(session, *profile.search_field, query)
    layout = "known layout" if known else "generic layout"
    return f"{report} Searched {query!r} in {profile.name} ({layout}, {status}).", image


def survey_home(session, max_pages: int = 4):
    """Page across the Home Screen, collecting one screenshot per page.

    Returns [(label, image)] so the caller sees the whole Home Screen in one
    round trip instead of a swipe-and-screenshot loop. Stops early once a page
    stops changing, i.e. the last page was already reached.
    """
    frame = session.live_frame()
    go_home(session, frame.window.pid)
    _, current = settle(session, timeout_s=4.0)

    pages: list[tuple[str, PILImage.Image]] = [("page 1", current)]
    for index in range(2, max(2, max_pages) + 1):
        home_page(session, "next")
        _, current = settle(session, timeout_s=4.0)
        if mirror.frame_difference(pages[-1][1], current) < 2.0:
            break
        pages.append((f"page {index}", current))
    return pages


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
#
# Composed from small named steps rather than one long script, so each piece is
# testable and reusable: open_compose -> pick_contact -> draft_message ->
# send_message. Sending is deliberately opt-in; see send_message.

def _band_detail(image, top: float, bottom: float) -> float:
    """How much visual content a horizontal band holds (0 = flat empty space).

    Used instead of a before/after diff to decide whether a suggestion list
    appeared. A single matching row barely moves a whole-frame difference, so
    the differential test produced false "no matches"; asking whether the band
    contains *anything* is absolute and does not depend on what was there
    before.
    """
    height = image.height
    box = (0, int(height * top), image.width, int(height * bottom))
    return ImageStat.Stat(image.crop(box).convert("L")).stddev[0]


# Empty dark space measures well under 1; a single contact row is several times
# this. Set between the two.
BAND_HAS_CONTENT = 3.0


def wait_for_band(session, top, bottom, want_content: bool, timeout_s: float = 6.0):
    """Poll until a band is (or stops being) populated. Returns (ok, frame).

    Waiting for the *condition we actually care about* beats settling and hoping.
    Contact suggestions render noticeably after the keystrokes land, so a
    settle() returns on a stable-but-empty screen and the following tap hits
    nothing; this returns the moment the list appears, so it is both faster and
    not a race.
    """
    deadline = time.monotonic() + timeout_s
    image = session.frame().image
    while True:
        image = session.frame().image
        if (_band_detail(image, top, bottom) >= BAND_HAS_CONTENT) == want_content:
            return True, image
        if time.monotonic() >= deadline:
            return False, image
        time.sleep(0.1)


def open_compose(session):
    """Open Messages and bring up the New Message sheet."""
    report, image = open_app(session, "Messages")
    if "Could not" in report:
        return False, report, image
    # Messages paints its list progressively; the capped launch settle can
    # return while the screen is still blank.
    settle(session, timeout_s=4.0, stable_for_s=0.35)

    # iOS resumes an app wherever it was left -- often inside a conversation, or
    # on a half-filled compose sheet. Get back to the conversation list before
    # doing anything, or the compose tap lands in a conversation and the
    # recipient ends up typed into the message body.
    #
    # Do NOT use Escape to unwind: inside an iOS app it acts as "go back", and a
    # couple of presses drop clean out to the Home Screen -- leaving the app
    # entirely and then trying to navigate back in.
    #
    # Just tap Compose. If Messages resumed inside a conversation the tap does
    # nothing, so back out once with the chevron and retry. Self-correcting,
    # needs no guess about which screen we started on, and never leaves the app.
    image = session.frame().image
    for attempt in range(2):
        before = session.frame().image
        tap_at(session, *landmarks.MSG_COMPOSE_BUTTON)
        _, image = settle(session, timeout_s=4.0)
        if _changed_since(session, before):
            break
        if attempt == 0:
            tap_at(session, *landmarks.MSG_BACK_BUTTON)
            settle(session, timeout_s=3.0, stable_for_s=0.25)
    else:
        return False, "Tapped Compose but the New Message sheet never appeared.", image
    # The sheet animates in over the list; wait for it to actually be blank
    # rather than judging it mid-transition.
    top_band, bottom_band = landmarks.MSG_SUGGESTION_BAND
    _, image = wait_for_band(
        session, top_band, bottom_band, want_content=False, timeout_s=3.0
    )

    # A fresh New Message sheet is blank below the "To:" field. A conversation
    # view is full of bubbles. Distinguishing them matters enormously: in a
    # conversation, the "To:" tap does nothing and keyboard focus stays on the
    # message body, so the recipient name gets typed into the message itself.
    top, bottom = landmarks.MSG_SUGGESTION_BAND
    if _band_detail(image, top, bottom) >= BAND_HAS_CONTENT:
        return (
            False,
            "Expected a blank New Message sheet but the screen has content "
            "below the To: field -- Messages is probably showing a conversation, "
            "not the compose sheet. Aborting rather than typing into it.",
            image,
        )
    return True, "New Message sheet open.", image


def pick_contact(session, recipient: str):
    """Type a name into "To:" and select the first matching contact.

    Returns (ok, report, image). Fails loudly when nothing matches: Messages
    leaves the typed text sitting in the field as a raw address, and sending to
    that would go to the wrong place -- or nowhere.
    """
    top, bottom = landmarks.MSG_SUGGESTION_BAND
    if _band_detail(session.frame().image, top, bottom) >= BAND_HAS_CONTENT:
        return (
            False,
            "Not on a blank New Message sheet -- there is already content below "
            "the To: field. Refusing to type a recipient here, because it would "
            "go into the message body instead.",
            session.frame().image,
        )

    tap_at(session, *landmarks.MSG_TO_FIELD)
    settle(session, timeout_s=3.0, stable_for_s=0.25)
    pid = session.live_frame().window.pid
    clear_field(pid)
    inputs.type_text(pid, recipient)

    found, typed = wait_for_band(session, top, bottom, want_content=True, timeout_s=6.0)
    if not found:
        return (
            False,
            f"No contact matching {recipient!r} -- the suggestion list stayed "
            "empty, so there is nobody to send to. Check the name, or add the "
            "contact on the phone.",
            typed,
        )

    tap_at(session, *landmarks.MSG_FIRST_CONTACT)
    _, image = settle(session, timeout_s=4.0, stable_for_s=0.3)
    return (
        True,
        f"Selected the first contact matching {recipient!r} (there may be "
        "several; the confirmation screenshot shows which one).",
        image,
    )


def draft_message(session, recipient: str, text: str):
    """Open Messages, pick the recipient, and type the body -- without sending."""
    ok, report, image = open_compose(session)
    if not ok:
        return False, report, image

    ok, report, image = pick_contact(session, recipient)
    if not ok:
        return False, report, image

    tap_at(session, *landmarks.MSG_BODY_FIELD)
    settle(session, timeout_s=3.0, stable_for_s=0.25)
    pid = session.live_frame().window.pid
    # iOS keeps a per-conversation draft. Without clearing, a leftover body from
    # an earlier aborted run gets the new text appended to it and sent as one
    # garbled message.
    clear_field(pid, 60)
    inputs.type_text(pid, text)
    status, image = settle(session, timeout_s=4.0, stable_for_s=0.3)
    return True, f"Drafted {text!r} to {recipient!r} ({status}).", image


def send_message(session, recipient: str, text: str, send: bool = False):
    """Draft a message and, only if ``send`` is true, actually send it.

    Sending a text is irreversible and goes to a real person, so the default is
    to stop at the drafted state and hand back a screenshot showing exactly who
    the recipient resolved to and what the body says. The caller confirms, then
    re-runs with send=True.
    """
    ok, report, image = draft_message(session, recipient, text)
    if not ok:
        return report, image
    if not send:
        return (
            report + " NOT SENT -- check the recipient and text in this "
            "screenshot, then call again with send=true to send it.",
            image,
        )

    before = session.frame().image
    tap_at(session, *landmarks.MSG_SEND_BUTTON)
    status, image = settle(session, timeout_s=5.0, stable_for_s=0.35)
    if not _changed_since(session, before):
        return (
            f"Pressed Send but the screen did not change ({status}) -- the "
            "message may not have gone. Check the screenshot.",
            image,
        )
    return f"Sent {text!r} to {recipient!r} ({status}).", image


# --------------------------------------------------------------------------
# Confirm-and-send
# --------------------------------------------------------------------------
#
# Re-running the whole draft flow just to press Send wastes ~19s and, worse,
# rebuilds state that was already correct. Once a draft is on screen the send
# button is at a known place, so confirming is a single tap. Verification comes
# *after* the tap, not before -- the pre-send screenshot was already taken and
# approved by the user.

SEND_BUTTONS = {
    "messages": landmarks.MSG_SEND_BUTTON,
    "whatsapp": landmarks.WA_SEND_BUTTON,
}


def send_button_armed(session, app: str) -> bool:
    """True while an unsent draft is still sitting in the composer.

    Both apps swap the send control for a grey mic/audio glyph once the message
    goes, so a saturated blue/green pixel at the send position means the draft
    is still pending -- an exact, OCR-free "did it actually send" check.
    """
    spot = SEND_BUTTONS[app]
    image = session.frame().image.convert("RGB")
    x = int(image.width * spot[0])
    y = int(image.height * spot[1])
    x = max(0, min(image.width - 1, x))
    y = max(0, min(image.height - 1, y))
    # Sample a small patch: the glyph is a circle, so one pixel is fragile.
    hits = 0
    for dx in (-6, 0, 6):
        for dy in (-6, 0, 6):
            px = max(0, min(image.width - 1, x + dx))
            py = max(0, min(image.height - 1, y + dy))
            r, g, b = image.getpixel((px, py))
            green = g > 110 and g - r > 40 and g - b > 30
            blue = b > 140 and b - r > 60 and b - g > 20
            if green or blue:
                hits += 1
    return hits >= 3


def tap_send(session, app: str):
    """Press Send on an on-screen draft and confirm it left the composer."""
    tap_at(session, *SEND_BUTTONS[app])
    status, image = settle(session, timeout_s=5.0, stable_for_s=0.35)
    return (not send_button_armed(session, app)), status, image


# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------
#
# Same shape as the Messages sender -- open -> pick recipient -> draft -> send,
# with sending opt-in -- but WhatsApp's chrome is entirely different, so it gets
# its own steps rather than a shared "generic messenger" abstraction that would
# fit neither app well.

def open_whatsapp_new_chat(session):
    """Open WhatsApp and bring up the "New chat" sheet."""
    report, image = open_app(session, "WhatsApp")
    if "Could not" in report:
        return False, report, image
    settle(session, timeout_s=4.0, stable_for_s=0.35)

    # Opening via Spotlight lands directly on the Chats list, so just tap "+".
    # Do NOT press Escape here: inside an iOS app Escape acts as "go back", and
    # repeated presses drop clean out to the Home Screen -- which is what made
    # the old version leave WhatsApp and then try to navigate back into it.
    #
    # If the app happened to resume inside a conversation the "+" tap does
    # nothing, so back out once with the chevron and retry. Self-correcting, and
    # it never leaves the app.
    for attempt in range(2):
        before = session.frame().image
        tap_at(session, *landmarks.WA_NEW_CHAT_BUTTON)
        _, image = settle(session, timeout_s=4.0)
        if _changed_since(session, before):
            return True, "WhatsApp New chat sheet open.", image
        if attempt == 0:
            tap_at(session, *landmarks.WA_BACK_BUTTON)
            settle(session, timeout_s=3.0, stable_for_s=0.25)
    return False, "Tapped New chat but the sheet never appeared.", session.frame().image


def pick_whatsapp_contact(session, recipient: str, index: int = 1):
    """Search the New chat sheet and open the index-th matching contact.

    ``index`` exists because WhatsApp does not put the exact match first --
    searching "Rohit" lists "Rohit sir COA" above plain "Rohit". Defaulting to
    row 1 and sending blind would message the wrong person, so callers confirm
    from the draft screenshot and bump the index if needed.
    """
    tap_at(session, *landmarks.WA_SEARCH_FIELD)
    settle(session, timeout_s=3.0, stable_for_s=0.25)
    pid = session.live_frame().window.pid
    clear_field(pid)
    inputs.type_text(pid, recipient)

    top, bottom = landmarks.WA_RESULT_BAND
    found, image = wait_for_band(session, top, bottom, want_content=True, timeout_s=6.0)
    if not found:
        return (
            False,
            f"No WhatsApp contact matching {recipient!r} -- the result list "
            "stayed empty.",
            image,
        )

    tap_at(session, *landmarks.wa_result(index))
    _, image = settle(session, timeout_s=5.0, stable_for_s=0.3)
    return (
        True,
        f"Opened WhatsApp result #{index} for {recipient!r} (WhatsApp does not "
        "rank exact matches first -- check the chat header in the screenshot).",
        image,
    )


def draft_whatsapp(session, recipient: str, text: str, index: int = 1):
    """Open WhatsApp, pick the recipient, and type the message -- without sending."""
    ok, report, image = open_whatsapp_new_chat(session)
    if not ok:
        return False, report, image

    ok, report, image = pick_whatsapp_contact(session, recipient, index)
    if not ok:
        return False, report, image

    tap_at(session, *landmarks.WA_BODY_FIELD)
    settle(session, timeout_s=3.0, stable_for_s=0.25)
    pid = session.live_frame().window.pid
    clear_field(pid, 60)
    inputs.type_text(pid, text)
    status, image = settle(session, timeout_s=4.0, stable_for_s=0.3)
    return True, f"Drafted {text!r} to {recipient!r} on WhatsApp ({status}).", image


def send_whatsapp(
    session, recipient: str, text: str, index: int = 1, send: bool = False
):
    """Draft a WhatsApp message and, only if ``send`` is true, send it."""
    ok, report, image = draft_whatsapp(session, recipient, text, index)
    if not ok:
        return report, image
    if not send:
        return (
            report + " NOT SENT -- confirm the chat header is the right person "
            "(WhatsApp does not rank exact matches first; pass contact_index to "
            "pick a different row), then call again with send=true.",
            image,
        )

    before = session.frame().image
    tap_at(session, *landmarks.WA_SEND_BUTTON)
    status, image = settle(session, timeout_s=5.0, stable_for_s=0.35)
    if not _changed_since(session, before):
        return (
            f"Pressed Send but the screen did not change ({status}) -- the "
            "message may not have gone. Check the screenshot.",
            image,
        )
    return f"Sent {text!r} to {recipient!r} on WhatsApp ({status}).", image


# --------------------------------------------------------------------------
# Expo dev server
# --------------------------------------------------------------------------

def lan_url(port: int = 8081, scheme: str = "exp") -> str | None:
    """This Mac's LAN URL for a dev server, e.g. http://192.168.1.5:8081.

    The phone cannot reach the Mac's ``localhost`` -- on the device that means
    the *phone*. A dev server has to be addressed by the Mac's LAN IP, so the
    common mistake of pasting http://localhost:8081 silently fails.
    """
    for interface in ("en0", "en1"):
        try:
            out = subprocess.run(["ipconfig", "getifaddr", interface],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out:
            return f"{scheme}://{out}:{port}"
    return None


def wait_for_brightness(session, top, bottom, minimum: float, timeout_s: float = 20.0):
    """Poll until a band gets at least this bright. Returns (ok, frame)."""
    deadline = time.monotonic() + timeout_s
    image = session.frame().image
    while True:
        image = session.frame().image
        if _band_brightness(image, top, bottom) >= minimum:
            return True, image
        if time.monotonic() >= deadline:
            return False, image
        time.sleep(0.2)


def screen_text(session) -> list:
    """Every recognised text element on the current screen."""
    frame = session.live_frame()
    return vision.recognize(frame.image, frame.device_w, frame.device_h)


def _alert_showing(session, elements=None) -> bool:
    """True while iOS's "Open this page in X?" alert is on screen.

    Reads the buttons rather than looking for a colour. Two earlier attempts
    keyed on appearance and both produced false positives: screen dimming fired
    on any dark page, and blue-pixel detection fired on any page with blue text
    in that region -- which reported a Google results page as an open alert.
    An alert is the only thing with both "Cancel" and "Open" on screen.
    """
    if elements is None:
        elements = screen_text(session)
    # Match across the joined text, not per element: Vision often returns the
    # alert's two buttons as one block ("Cancel Open"), so requiring them as
    # separate labels missed an alert that was plainly on screen.
    blob = " ".join(element.text.strip().lower() for element in elements)
    return "cancel" in blob and "open" in blob


def open_url(session, url: str, timeout_s: float = 15.0):
    """Open a URL on the phone through Safari. Returns (ok, report, frame).

    Handles any scheme: an https page renders, while a deep link such as
    ``exp://`` or ``maps://`` makes iOS raise its "Open this page in X?" alert.
    Both count as the URL having committed.

    Committing is verified rather than assumed. "The screen changed" is not
    enough -- opening Safari's suggestion dropdown changes the screen too, and
    an earlier version reported success while still sitting in that dropdown.
    """
    report, image = open_app(session, "Safari")
    if "Could not" in report or "never opened" in report:
        return False, report, image
    settle(session, timeout_s=4.0, stable_for_s=0.3)

    tap_at(session, *landmarks.SAFARI_URL_BAR)
    settle(session, timeout_s=4.0, stable_for_s=0.3)

    pid = session.live_frame().window.pid
    clear_field(pid, 60)
    inputs.type_text(pid, url)
    # Let Safari's inline autocomplete settle before committing, or Return can
    # be swallowed by the suggestion list.
    settle(session, timeout_s=3.0, stable_for_s=0.3)

    # Confirm by reading the screen. A deep link raises the confirm alert; a web
    # address ends up in Safari's address bar. Anything else means Return was
    # swallowed by the suggestion list, which is what used to pass unnoticed.
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    host_key = host.replace("www.", "")
    scheme = url.split("://", 1)[0].lower() if "://" in url else "https"
    # A deep link never renders a page -- the only success signal is the alert.
    # A web address lands in Safari's address bar, which sits at the bottom;
    # matching the host *anywhere* is not enough, because the suggestion
    # dropdown displays the text you just typed and would match immediately.
    web = scheme in ("http", "https")
    for _attempt in range(2):
        inputs.press_key(pid, "return")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            elements = screen_text(session)
            if _alert_showing(session, elements):
                return (True, f"Opened {url}; iOS is asking which app to use.",
                        session.frame().image)
            if web and host_key:
                frame = session.live_frame()
                bar = [
                    element for element in elements
                    if element.y > frame.device_h * landmarks.SAFARI_BAR_BAND
                ]
                if any(host_key in element.text.lower() for element in bar):
                    return True, f"Opened {url}.", session.frame().image
            time.sleep(0.3)
    return (
        False,
        f"Typed {url} but it never loaded -- {host!r} did not appear on screen "
        "and no app-handoff alert was raised. Safari probably stayed on its "
        "suggestion list; check the address and that the host is reachable "
        "from the phone.",
        session.frame().image,
    )


def device_orientation(session) -> str:
    """'portrait' or 'landscape', from the shape of the mirrored screen."""
    frame = session.live_frame()
    return "landscape" if frame.content_w > frame.content_h else "portrait"


def open_expo_app(session, url: str | None = None, use_dev_build: bool = False):
    """Safari -> dev-server URL -> open the project in Expo. One screenshot.

    Every intermediate step is verified from frames the flow already captures,
    so only the final loaded screen is handed back.
    """
    # exp:// is the deep link: Safari hands straight off to Expo Go, skipping
    # the dev-server page and its "Expo Go" button. http:// is only needed when
    # the caller wants to choose "Development Build" from that page.
    target = url or lan_url(scheme="http" if use_dev_build else "exp")
    if not target:
        return "Could not work out this Mac's LAN address for the dev server.", \
            session.frame().image
    if "localhost" in target or "127.0.0.1" in target:
        return (
            f"{target!r} will not work from the phone -- on the device localhost "
            "is the phone itself. Pass the Mac's LAN URL "
            f"(this Mac appears to be {lan_url()}).",
            session.frame().image,
        )

    ok, report, image = open_url(session, target)
    if not ok:
        return report, image

    # With http:// we land on the dev-server page and still have to choose how
    # to open the project. With exp:// the handoff dialog is already up.
    if not _alert_showing(session):
        settle(session, timeout_s=6.0, stable_for_s=0.4)
        button = (
            landmarks.EXPO_DEV_BUILD_BUTTON if use_dev_build
            else landmarks.EXPO_GO_BUTTON
        )
        tap_at(session, *button)
        settle(session, timeout_s=4.0, stable_for_s=0.3)

    # iOS only asks the first time it hands off to a given app, so check rather
    # than tapping a dialog that may not be there.
    # Wait for the alert rather than assuming it is already up: it can take a
    # moment to appear after the URL commits.
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not _alert_showing(session):
        time.sleep(0.2)
    if _alert_showing(session):
        tap_at(session, *landmarks.IOS_CONFIRM_OPEN)
        settle(session, timeout_s=4.0, stable_for_s=0.3)

    # Bundling takes a while; wait long and loosely for it to go quiet.
    status, image = settle(session, timeout_s=45.0, stable_for_s=0.8, threshold=1.6)
    which = "Development Build" if use_dev_build else "Expo Go"
    return f"Opened {target} in {which} ({status}).", image


# --------------------------------------------------------------------------
# Find things that are not on screen yet
# --------------------------------------------------------------------------

def go_to_root(session, max_steps: int = 5):
    """Back out to an app's root screen. Returns (steps, frame).

    iOS resumes an app exactly where it was left, which is the single most
    common cause of a flow going wrong: Messages reopening on a half-filled
    compose sheet, Blinkit deep in checkout, Settings on a sub-page. Every such
    flow was re-implementing "tap back until it stops moving".

    Force-quitting would be the thorough answer, and it is not available: the
    App Switcher's swipe-up card dismissal does not register through mirroring,
    the same way vertical drags do not scroll. Backing out is what is actually
    reachable.
    """
    steps = 0
    for _ in range(max(1, max_steps)):
        before = session.frame().image
        tap_at(session, *landmarks.BACK_CHEVRON)
        settle(session, timeout_s=3.0, stable_for_s=0.25)
        if not _changed_since(session, before):
            break
        steps += 1
    return steps, session.live_frame()


def wait_for_text(session, text: str, timeout_s: float = 15.0, gone: bool = False):
    """Wait until some text appears (or disappears). Returns (ok, element, frame).

    More precise than waiting for the screen to go still, and usually faster:
    "wait until 'Loading' goes away" is the actual condition, whereas stillness
    is a proxy that fails on anything animated -- an autoplaying feed never
    settles, and a spinner keeps a screen "busy" forever.
    """
    deadline = time.monotonic() + max(0.5, timeout_s)
    frame = session.live_frame()
    while True:
        frame = session.live_frame()
        elements = vision.recognize(frame.image, frame.device_w, frame.device_h)
        matches = vision.find(elements, text)
        if gone and not matches:
            return True, None, frame
        if not gone and matches:
            return True, matches[0], frame
        if time.monotonic() >= deadline:
            return False, (matches[0] if matches else None), frame
        time.sleep(0.25)


def scroll_to_text(session, text: str, direction: str = "down", max_scrolls: int = 10):
    """Scroll until some text is visible. Returns (element, scrolls, frame).

    OCR only sees what is rendered, so anything below the fold does not exist
    as far as tap_text is concerned -- looking for "General" in Settings simply
    fails until it is scrolled into view. This closes that gap.

    Stops early when the screen stops changing, which means the list has hit its
    end and further scrolling would just burn time.
    """
    for scrolls in range(max_scrolls + 1):
        frame = session.live_frame()
        elements = vision.recognize(frame.image, frame.device_w, frame.device_h)
        matches = vision.find(elements, text)
        if matches:
            return matches[0], scrolls, frame

        before = session.frame().image
        scroll(session, direction, 0.55)
        settle(session, timeout_s=4.0, stable_for_s=0.3)
        if not _changed_since(session, before, threshold=2.0):
            break  # reached the end of the list
    return None, scrolls, session.live_frame()
