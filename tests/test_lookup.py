"""Text ranking, keycode mapping, and landmark maths."""

import pytest

from thumb import inputs, landmarks, vision


def element(text, x=100.0, y=200.0, confidence=0.9):
    return vision.TextElement(text=text, x=x, y=y, width=40.0, height=10.0,
                              confidence=confidence)


def test_find_prefers_an_exact_label_over_a_longer_containing_string():
    """Tapping "Wallet" must not hit "Wallet balance and more"."""
    elements = [element("Wallet balance and more", y=100), element("Wallet", y=300)]
    assert vision.find(elements, "Wallet")[0].text == "Wallet"


def test_find_ranks_prefix_above_substring():
    elements = [element("My Wallet", y=100), element("Wallet Settings", y=300)]
    assert vision.find(elements, "Wallet")[0].text == "Wallet Settings"


def test_find_is_case_insensitive_and_ignores_surrounding_space():
    assert vision.find([element("  WALLET ")], "wallet")


def test_find_returns_nothing_when_absent():
    assert vision.find([element("Settings")], "Bluetooth") == []


def test_find_returns_every_match_so_occurrence_can_choose():
    elements = [element("Send", y=100), element("Send", y=400)]
    matches = vision.find(elements, "Send")
    assert len(matches) == 2
    assert matches[0].y < matches[1].y     # reading order


@pytest.mark.parametrize(
    "char, shifted",
    [("a", False), ("A", True), ("z", False), ("1", False), ("!", True),
     (":", True), ("/", False), ("?", True), (" ", False), (".", False)],
)
def test_char_keycodes_map_with_the_right_shift_state(char, shifted):
    mapping = inputs._char_key(char)
    assert mapping is not None, f"no keycode for {char!r}"
    assert mapping[1] is shifted


def test_letters_are_not_all_keycode_zero():
    """Keycode 0 is the physical A key.

    Typing used to send every character as keycode 0 with a unicode payload,
    which iPhone Mirroring drops -- so "instagram" arrived as "aaaaaaaaa".
    """
    codes = {inputs._char_key(c)[0] for c in "instagram"}
    assert len(codes) > 1
    assert inputs._char_key("i")[0] != inputs._char_key("n")[0]


def test_uppercase_shares_the_keycode_of_its_lowercase():
    assert inputs._char_key("A")[0] == inputs._char_key("a")[0]


def test_unmappable_characters_report_none_for_the_unicode_fallback():
    assert inputs._char_key("😀") is None


def test_nav_slots_are_ordered_and_inside_the_tab_bar():
    slots = [landmarks.nav_slot(i, 5) for i in range(1, 6)]
    xs = [x for x, _y in slots]
    assert xs == sorted(xs)
    assert all(0.0 < x < 1.0 for x in xs)
    assert all(y == landmarks.NAV_Y for _x, y in slots)


def test_nav_slot_matches_the_measured_instagram_search_tab():
    x, _y = landmarks.nav_slot(4, 5)
    assert x == pytest.approx(0.666, abs=0.01)   # measured 0.666 on device


def test_nav_slot_clamps_out_of_range_indices():
    assert landmarks.nav_slot(0, 5) == landmarks.nav_slot(1, 5)
    assert landmarks.nav_slot(99, 5) == landmarks.nav_slot(5, 5)


def test_single_slot_navigation_centres():
    assert landmarks.nav_slot(1, 1)[0] == 0.5


def test_whatsapp_result_rows_step_down_by_the_measured_pitch():
    first = landmarks.wa_result(1)
    third = landmarks.wa_result(3)
    assert third[1] - first[1] == pytest.approx(2 * landmarks.WA_RESULT_PITCH)
    assert third[1] == pytest.approx(0.357, abs=0.005)  # measured for "Rohit"


@pytest.mark.parametrize(
    "spoken, canonical",
    [("insta", "Instagram"), ("ig", "Instagram"), ("Instagram", "Instagram"),
     ("wa", "WhatsApp"), ("whatsapp", "WhatsApp"), ("yt", "YouTube"),
     ("twitter", "X"), ("sms", "Messages")],
)
def test_app_aliases_resolve_to_the_launchable_name(spoken, canonical):
    profile, known = landmarks.profile_for(spoken)
    assert known and profile.name == canonical
    assert landmarks.canonical_name(spoken) == canonical


def test_unknown_apps_fall_back_rather_than_failing():
    profile, known = landmarks.profile_for("some app that does not exist")
    assert not known
    assert profile.search_tab == (4, 5)          # generic iOS layout
    assert landmarks.canonical_name("Foo") == "Foo"   # typed into Spotlight as-is
