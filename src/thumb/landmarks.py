"""Where things are on screen -- the data half of the shortcut system.

Taking a screenshot just to locate a tab bar is the biggest single time sink in
driving a phone, so common targets live here as fixed positions instead. Every
coordinate is a **fraction of the device screen** (0..1, origin top-left), never
an absolute point, so the same landmark is correct on an iPhone SE and a Pro Max
alike.

This file is meant to be edited. Adding support for a new app is a matter of
adding one `AppProfile` below -- no flow logic changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Generic iOS chrome
# --------------------------------------------------------------------------

NAV_Y = 0.94                    # vertical centre of the bottom tab bar
NAV_SPAN = (0.165, 0.833)       # horizontal centres of the first and last tab
TOP_SEARCH_FIELD = (0.5, 0.10)  # search bar pinned below the status bar
SPOTLIGHT_TOP_HIT = (0.161, 0.172)  # first app icon under "Top Hit"
HOME_EMPTY_BAND = 0.82          # y of dead space between last icon row and dock

# Top-left back chevron. Near-universal across iOS apps, and the only reliable
# way back: the edge-swipe gesture does not register through mirroring.
BACK_CHEVRON = (0.086, 0.090)


def nav_slot(index: int, count: int = 5) -> tuple[float, float]:
    """Centre of the index-th bottom-tab item (1-based), as screen fractions."""
    if count <= 1:
        return 0.5, NAV_Y
    low, high = NAV_SPAN
    index = max(1, min(count, index))
    return low + (index - 1) * (high - low) / (count - 1), NAV_Y


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
#
# Messages has no bottom tab bar, and on iOS 18/26 its conversation-list search
# field sits at the *bottom*, not the top. Sending is driven from the compose
# sheet rather than from search: search only matches message *text*, so
# searching a name happily returns unrelated threads that merely mention it.

MSG_COMPOSE_BUTTON = (0.869, 0.952)   # pencil icon on the conversation list
MSG_TO_FIELD = (0.5, 0.196)           # "To:" field on the New Message sheet
MSG_FIRST_CONTACT = (0.5, 0.267)      # first contact suggestion under "To:"
MSG_BODY_FIELD = (0.57, 0.960)        # message input at the bottom
MSG_SEND_BUTTON = (0.864, 0.940)      # arrow inside the body pill's right edge
MSG_BACK_BUTTON = (0.086, 0.095)      # '<' in a conversation; 'Edit' on the list

# Vertical band the contact suggestions occupy; used to tell "matches found"
# from "no such contact" without OCR.
MSG_SUGGESTION_BAND = (0.24, 0.85)


# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------
#
# WhatsApp is laid out nothing like Messages: a 5-tab bar, a green "+" that
# opens a "New chat" sheet with its own search, and the send button replaces the
# mic inside the input row.
#
# One difference matters for safety: WhatsApp does NOT rank an exact name match
# first. Searching "Rohit" returns "Rohit sir COA", "rohit mummy", "Rohit",
# "Tiya Rohit Nepi" -- in that order. Taking row 1 messages the wrong person, so
# the sender exposes a row index and drafts rather than sending by default.

WA_NEW_CHAT_BUTTON = (0.904, 0.095)   # green "+"
WA_SEARCH_FIELD = (0.5, 0.179)        # "Name, number, @username"
WA_FIRST_RESULT = (0.5, 0.238)        # first row under "Contacts on WhatsApp"
WA_RESULT_PITCH = 0.0595              # vertical distance between result rows
WA_BODY_FIELD = (0.389, 0.930)
WA_SEND_BUTTON = (0.937, 0.930)       # green arrow, replaces the mic
WA_BACK_BUTTON = (0.086, 0.095)       # "<" in a chat; "..." on the chat list
WA_RESULT_BAND = (0.20, 0.85)


def wa_result(index: int) -> tuple[float, float]:
    """Centre of the index-th (1-based) WhatsApp search result row."""
    index = max(1, index)
    x, y = WA_FIRST_RESULT
    return x, y + (index - 1) * WA_RESULT_PITCH


# --------------------------------------------------------------------------
# Blinkit (grocery delivery) -- DRAFT, used only by thumb.drafts
# --------------------------------------------------------------------------
#
# These back an unfinished flow that is deliberately not registered as a tool.
# See thumb/drafts.py for what works, what does not, and what it needs.
#
# Two quirks drive the design of the Blinkit flow:
#
#  * The home screen's search bar shifts vertically as the page scrolls, so the
#    flow taps it and then verifies it reached the dedicated search screen,
#    where the field is reliably pinned to the top.
#  * Tapping ADD on a product with multiple pack sizes opens a variant sheet
#    instead of adding anything. The sheet dims the rest of the screen, which is
#    a clean way to detect it: the top band measures ~102 with the sheet up
#    versus ~240 without.

BK_BACK_BUTTON = (0.086, 0.090)       # '<' on checkout / product pages
BK_HOME_TAB = (0.157, 0.940)          # 'Home' in the bottom nav
BK_HOME_SEARCH = (0.5, 0.190)         # search bar on the home screen
BK_HOME_SEARCH_ALT = (0.5, 0.258)     # ...before the page scrolls
BK_SEARCH_FIELD = (0.5, 0.098)        # field on the dedicated search screen
BK_VARIANT_CLOSE = (0.5, 0.555)       # X that dismisses the variant sheet
BK_VIEW_CART = (0.5, 0.845)           # floating "View cart" pill

BK_DIM_BAND = (0.05, 0.45)            # region the variant sheet dims
BK_SHEET_DIM_MAX = 180.0              # brightness below this => sheet is open


# --------------------------------------------------------------------------
# Per-app layouts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AppProfile:
    """Where an app keeps the controls the shortcuts need.

    ``search_tab`` is (slot, total_slots) in the bottom tab bar; ``search_field``
    is where the search input sits once that tab is open. ``aliases`` are the
    other names a user might say ("insta" for Instagram).
    """

    name: str
    search_tab: tuple[int, int] = (4, 5)
    search_field: tuple[float, float] = TOP_SEARCH_FIELD
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Keep alphabetical. The key is the canonical, launchable app name -- it is what
# gets typed into Spotlight, so it must match the real app title.
APP_PROFILES: tuple[AppProfile, ...] = (
    AppProfile("App Store", search_tab=(5, 5), aliases=("appstore",)),
    AppProfile("Blinkit", search_tab=(1, 1), search_field=BK_SEARCH_FIELD,
               aliases=("blink it", "grofers")),
    AppProfile("Instagram", search_tab=(4, 5), aliases=("insta", "ig")),
    AppProfile("Maps", search_tab=(1, 1), search_field=(0.5, 0.88)),
    AppProfile("Messages", search_tab=(1, 1), search_field=(0.42, 0.94),
               aliases=("imessage", "text", "sms")),
    AppProfile("Spotify", search_tab=(2, 3), aliases=("spot",)),
    AppProfile("Threads", search_tab=(2, 5)),
    AppProfile("WhatsApp", search_tab=(4, 5), search_field=WA_SEARCH_FIELD,
               aliases=("wa", "whats app", "whatsap")),
    AppProfile("X", search_tab=(2, 5), aliases=("twitter",)),
    AppProfile("YouTube", search_tab=(2, 5), search_field=(0.5, 0.07),
               aliases=("yt",)),
)

# Used when an app has no profile: the most common iOS arrangement.
DEFAULT_PROFILE = AppProfile("generic", search_tab=(4, 5))

_BY_NAME: dict[str, AppProfile] = {}
for _profile in APP_PROFILES:
    _BY_NAME[_profile.name.lower()] = _profile
    for _alias in _profile.aliases:
        _BY_NAME[_alias.lower()] = _profile


def profile_for(app: str) -> tuple[AppProfile, bool]:
    """Look up an app's layout by name or alias.

    Returns (profile, is_known). Unknown apps fall back to the generic layout
    rather than failing, since the generic guess is usually right and the caller
    can override the tab position explicitly.
    """
    found = _BY_NAME.get(app.strip().lower())
    return (found, True) if found is not None else (DEFAULT_PROFILE, False)


def canonical_name(app: str) -> str:
    """Resolve an alias to the real app name to type into Spotlight."""
    profile, known = profile_for(app)
    return profile.name if known else app


# --------------------------------------------------------------------------
# Safari + Expo dev server
# --------------------------------------------------------------------------
#
# Safari on iOS 18/26 keeps its address bar at the *bottom*. The Expo dev-server
# page offers two ways to open a project, and iOS then asks for confirmation
# before handing off to another app -- that confirm dialog dims the page, which
# is how the flow knows whether it needs dismissing (measured 148 with the
# dialog up vs 246 without).

SAFARI_URL_BAR = (0.5, 0.932)
# Anything below this fraction of the screen is the address-bar row. Used to
# confirm a page loaded: the suggestion dropdown echoes the typed text higher
# up, so matching the host anywhere would pass while still in the dropdown.
SAFARI_BAR_BAND = 0.88
EXPO_DEV_BUILD_BUTTON = (0.5, 0.773)
EXPO_GO_BUTTON = (0.5, 0.830)
IOS_CONFIRM_OPEN = (0.845, 0.506)   # "Open" in the app-handoff dialog
EXPO_DIALOG_BAND = (0.10, 0.40)
# The alert is detected by reading its buttons (see flows._alert_showing), not
# by its appearance -- colour and dimming both produced false positives.
