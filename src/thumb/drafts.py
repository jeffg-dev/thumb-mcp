"""DRAFT flows -- not wired into the MCP server.

Nothing in this module is registered as a tool, so the assistant cannot call it.
It is kept because the groundwork is real and worth resuming, not because it
works.

------------------------------------------------------------------------------
Blinkit (grocery delivery) -- STATUS: DRAFT, NOT RELIABLE
------------------------------------------------------------------------------

What works:

* Launching Blinkit and reaching its search screen.
* ``find_add_buttons`` -- locating the green ADD pills by colour. This is the
  good part: Blinkit's grid shifts with the search-suggestion dropdown, injected
  ad tiles and the filter row, so fixed coordinates fail, while colour detection
  correctly finds the first tile across layouts.
* ``blinkit_sheet_open`` -- detecting the pack-size chooser by how much it dims
  the screen (~102 with the sheet up vs ~240 without: clean separation).
* Stopping at the cart. The flow never places an order or pays.

Why it is a draft:

* **Taps land during loading.** Results render behind a skeleton; a tap that
  arrives early opens a product detail page instead of adding to the cart. This
  is what makes it report "added" with an empty cart.
* **No confirmation that anything was added.** Each step assumes success rather
  than checking the cart count actually incremented.
* **Screen positions are ambiguous across screens.** The back-chevron spot is
  the delivery-location selector on the home screen and the "PAY USING" row on
  checkout -- next to Place Order, which is a bad place to tap blind.

To finish it:

1. Replace every ``settle`` before a tap with a wait on the *condition*, the way
   ``flows.wait_for_band`` fixed the messaging flows -- e.g. wait until
   ``find_add_buttons`` returns a stable set before tapping one.
2. Read the "View cart / N Items" pill and assert N increased after each add,
   instead of trusting the tap.
3. Give the unwind a screen-specific route rather than a shared back-chevron
   coordinate, since that position means different things per screen.

Until then, driving Blinkit is a supervised, screenshot-by-screenshot job.
"""

from __future__ import annotations

import time

from PIL import ImageStat

from . import inputs, landmarks
from .flows import (
    _band_brightness,
    _changed_since,
    clear_field,
    open_app,
    settle,
    tap_at,
)


# --------------------------------------------------------------------------
# Blinkit (grocery delivery)
# --------------------------------------------------------------------------
#
# Deliberately stops at the cart. Adding items is reversible; placing an order
# spends real money, so the flow hands the user a filled cart and lets them pay.

def find_add_buttons(image, min_w: int = 60, min_h: int = 30):
    """Locate Blinkit's green-outlined ADD buttons, top-left first.

    Fixed tile coordinates do not survive Blinkit: the results grid shifts
    depending on whether the search-suggestion dropdown is showing, how many ad
    tiles are injected, and the filter row. Finding the buttons by their colour
    makes the flow independent of all that -- an ADD button is a white pill with
    a strongly green-dominant border.
    """
    im = image.convert("RGB")
    width, height = im.size
    px = im.load()

    columns: dict[int, list[int]] = {}
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r, g, b = px[x, y]
            if g > 95 and g - r > 35 and g - b > 35:
                columns.setdefault(x // 20, []).append(y)

    blobs = []
    for bucket, ys in columns.items():
        ys.sort()
        run = [ys[0]]
        for y in ys[1:]:
            if y - run[-1] <= 14:
                run.append(y)
            else:
                blobs.append((bucket, run[0], run[-1]))
                run = [y]
        blobs.append((bucket, run[0], run[-1]))

    merged: list[dict] = []
    for bucket, y0, y1 in sorted(blobs):
        for m in merged:
            if bucket <= m["x1"] + 1 and not (y1 < m["y0"] - 10 or y0 > m["y1"] + 10):
                m["x1"] = max(m["x1"], bucket)
                m["y0"] = min(m["y0"], y0)
                m["y1"] = max(m["y1"], y1)
                break
        else:
            merged.append({"x0": bucket, "x1": bucket, "y0": y0, "y1": y1})

    found = []
    for m in merged:
        left, right = m["x0"] * 20, m["x1"] * 20 + 20
        box_w, box_h = right - left, m["y1"] - m["y0"]
        if box_w < min_w or box_h < min_h or box_h > 120:
            continue
        cx, cy = (left + right) / 2, (m["y0"] + m["y1"]) / 2
        r, g, b = px[int(min(cx, width - 1)), int(min(cy, height - 1))]
        if r > 200 and g > 200 and b > 200:  # ADD pills are white inside
            found.append((cx / width, cy / height))

    # Group into rows before ordering: a tall button can split into two blobs,
    # and sorting on raw y would then rank a middle-column fragment above the
    # genuine first tile.
    found.sort(key=lambda t: (int(t[1] / 0.08), t[0]))
    deduped: list[tuple[float, float]] = []
    for x, y in found:
        if not any(abs(x - dx) < 0.05 and abs(y - dy) < 0.06 for dx, dy in deduped):
            deduped.append((x, y))
    return deduped


def blinkit_sheet_open(session) -> bool:
    """True when the pack-size variant sheet is covering the screen."""
    top, bottom = landmarks.BK_DIM_BAND
    return (
        _band_brightness(session.frame().image, top, bottom)
        < landmarks.BK_SHEET_DIM_MAX
    )


def open_blinkit_search(session):
    """Open Blinkit and get to its dedicated search screen."""
    report, image = open_app(session, "Blinkit")
    if "Could not" in report:
        return False, report, image
    settle(session, timeout_s=5.0, stable_for_s=0.35)

    # Blinkit resumes wherever it was left -- often deep in checkout. Back out
    # of that FIRST: the checkout page has no bottom nav, so what is normally
    # the Home tab is the "PAY USING" row, right next to Place Order. Only once
    # we are on a normal screen is it safe to touch the bottom of the display.
    for _ in range(3):
        previous = session.frame().image
        tap_at(session, *landmarks.BK_BACK_BUTTON)
        settle(session, timeout_s=3.0, stable_for_s=0.25)
        if not _changed_since(session, previous):
            break

    # Now the Home tab is safe, and it resets the page to the top so the search
    # bar sits at a known position.
    tap_at(session, *landmarks.BK_HOME_TAB)
    settle(session, timeout_s=4.0, stable_for_s=0.3)

    # The bar still shifts a little with scroll, so try both resting positions.
    for spot in (landmarks.BK_HOME_SEARCH_ALT, landmarks.BK_HOME_SEARCH):
        before = session.frame().image
        tap_at(session, *spot)
        _, image = settle(session, timeout_s=4.0, stable_for_s=0.3)
        if _changed_since(session, before):
            return True, "Blinkit search screen open.", image
    return False, "Could not open Blinkit's search screen.", session.frame().image


def blinkit_search(session, query: str):
    """Type a query into the Blinkit search field and run it."""
    tap_at(session, *landmarks.BK_SEARCH_FIELD)
    settle(session, timeout_s=3.0, stable_for_s=0.25)
    pid = session.live_frame().window.pid
    clear_field(pid, 40)
    settle(session, timeout_s=2.0, stable_for_s=0.2)
    before = session.frame().image
    inputs.type_text(pid, query)
    time.sleep(0.4)
    inputs.press_key(pid, "return")
    status, image = settle(session, timeout_s=6.0, stable_for_s=0.35)
    if not _changed_since(session, before):
        return False, f"Searching {query!r} on Blinkit changed nothing.", image
    return True, f"Searched {query!r} ({status}).", image


def blinkit_add_first(session, pack_index: int = 1):
    """Add the first search result, choosing a pack size if one is offered."""
    buttons = find_add_buttons(session.frame().image)
    if not buttons:
        return False, "no ADD button found on the results screen", session.frame().image

    tap_at(session, *buttons[0])
    _, image = settle(session, timeout_s=4.0, stable_for_s=0.3)

    if blinkit_sheet_open(session):
        # Multi-pack product: ADD only opened a chooser and nothing is in the
        # cart yet. The pack buttons are the ones inside the sheet, i.e. in the
        # lower half of the screen.
        pack_buttons = [b for b in find_add_buttons(image) if b[1] > 0.6]
        if not pack_buttons:
            tap_at(session, *landmarks.BK_VARIANT_CLOSE)
            settle(session, timeout_s=3.0, stable_for_s=0.25)
            return False, "pack chooser opened but no pack button found", image
        index = min(max(1, pack_index), len(pack_buttons)) - 1
        tap_at(session, *pack_buttons[index])
        settle(session, timeout_s=4.0, stable_for_s=0.3)
        tap_at(session, *landmarks.BK_VARIANT_CLOSE)
        _, image = settle(session, timeout_s=4.0, stable_for_s=0.3)
        return True, "added, smallest pack", image
    return True, "added", image


def blinkit_view_cart(session):
    """Open the cart page."""
    tap_at(session, *landmarks.BK_VIEW_CART)
    status, image = settle(session, timeout_s=6.0, stable_for_s=0.35)
    return f"Opened the cart ({status}).", image


def order_on_blinkit(session, items):
    """Add each item to the Blinkit cart and stop on the cart page.

    Never places the order. Adding to a cart is reversible; paying is not, so
    the flow deliberately ends with a filled cart for the user to review and pay
    for themselves.
    """
    if isinstance(items, str):
        items = [items]

    ok, report, image = open_blinkit_search(session)
    if not ok:
        return report, image

    added, failed = [], []
    for item in items:
        ok, report, image = blinkit_search(session, item)
        if not ok:
            failed.append(f"{item} (search failed)")
            continue
        ok, note, image = blinkit_add_first(session)
        (added if ok else failed).append(f"{item} ({note})" if ok else item)

    cart_report, image = blinkit_view_cart(session)
    lines = [f"Added to the Blinkit cart: {', '.join(added)}." if added else
             "Nothing was added to the cart."]
    if failed:
        lines.append(f"Could not add: {', '.join(failed)}.")
    lines.append(cart_report)
    lines.append(
        "Stopped at the cart -- the order has NOT been placed. Review the items "
        "and pay on the phone."
    )
    return " ".join(lines), image
