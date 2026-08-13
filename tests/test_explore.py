"""Exploration safety and screen identity.

The safety rules matter more than the graph: an explorer that taps everything
on a real phone sends messages, spends money and deletes things.
"""

import pytest

from thumb import explore, vision


def element(text, height=10.0):
    return vision.TextElement(text=text, x=100.0, y=200.0, width=40.0,
                              height=height, confidence=0.9)


@pytest.mark.parametrize(
    "label",
    ["Send", "Send Message", "Place Order", "Pay now", "Delete", "Remove App",
     "Log Out", "Sign Out", "Block", "Report", "Call", "FaceTime",
     "Confirm", "Buy now", "Subscribe", "Unfollow", "Forget This Network"],
)
def test_destructive_and_outward_facing_controls_are_never_tapped(label):
    assert not explore.is_safe(label)


@pytest.mark.parametrize(
    "label", ["Settings", "General", "Profile", "Wi-Fi", "About", "Search",
              "Notifications", "Display & Brightness"],
)
def test_ordinary_navigation_controls_are_allowed(label):
    assert explore.is_safe(label)


def test_matching_is_case_insensitive_and_substring_based():
    assert not explore.is_safe("SEND")
    assert not explore.is_safe("Tap to send now")


def test_empty_labels_are_not_safe():
    assert not explore.is_safe("   ")


def test_navigation_noise_is_skipped_as_useless():
    for label in ["Back", "Cancel", "Close", "Done", "Dismiss"]:
        assert not explore.is_useful(label)


def test_numbers_and_clocks_are_not_treated_as_controls():
    for label in ["8:42", "12", "9.99"]:
        assert not explore.is_useful(label)


def test_very_long_strings_are_not_controls():
    assert not explore.is_useful("x" * 60)


def test_useful_labels_pass():
    assert explore.is_useful("General")
    assert explore.is_useful("Wi-Fi")


def test_fingerprint_is_stable_for_the_same_text():
    a = [element("Settings"), element("Wi-Fi")]
    b = [element("Wi-Fi"), element("Settings")]      # order must not matter
    assert explore.fingerprint(a) == explore.fingerprint(b)


def test_fingerprint_differs_for_different_screens():
    a = [element("Settings"), element("Wi-Fi")]
    b = [element("Photos"), element("Albums")]
    assert explore.fingerprint(a) != explore.fingerprint(b)


def test_fingerprint_ignores_case_and_padding():
    assert explore.fingerprint([element(" Settings ")]) == explore.fingerprint(
        [element("settings")]
    )


def test_screen_label_picks_the_largest_text_as_the_title():
    elements = [element("a small note", height=8), element("Settings", height=30)]
    assert explore.screen_label(elements) == "Settings"


def test_screen_label_handles_a_screen_with_no_text():
    assert explore.screen_label([]) == "(no text)"


def test_map_renders_screens_edges_and_skipped_controls():
    graph = explore.Map(app="Demo")
    graph.screens["aaa"] = explore.Screen(id="aaa", label="Root", path=[], visited=True)
    graph.screens["bbb"] = explore.Screen(id="bbb", label="Child", path=["General"],
                                          visited=True)
    graph.edges.append(explore.Edge("aaa", "General", "bbb"))
    graph.skipped_unsafe.append("Delete")
    text = graph.render()
    assert "Root" in text and "Child" in text
    assert "'General' -> [bbb]" in text
    assert "Delete" in text


def positioned(text, y, height=10.0):
    return vision.TextElement(text=text, x=100.0, y=y, width=40.0,
                              height=height, confidence=0.9)


def test_screen_label_ignores_the_status_bar_clock():
    """Picking the largest text alone named a screen "9:05"."""
    elements = [positioned("9:05", y=25, height=28),
                positioned("Software Update", y=140, height=22)]
    assert explore.screen_label(elements, device_h=852) == "Software Update"


def test_screen_label_prefers_the_title_zone_over_large_body_text():
    elements = [positioned("General", y=120, height=20),
                positioned("A very prominent banner", y=600, height=40)]
    assert explore.screen_label(elements, device_h=852) == "General"


def test_screen_label_falls_back_when_nothing_is_in_the_title_zone():
    elements = [positioned("Body content", y=500, height=20)]
    assert explore.screen_label(elements, device_h=852) == "Body content"


def test_screen_label_ignores_pure_numbers_and_percentages():
    elements = [positioned("75%", y=100, height=40), positioned("Battery", y=120, height=20)]
    assert explore.screen_label(elements, device_h=852) == "Battery"


@pytest.mark.parametrize(
    "body",
    ["cannot be uploaded to iCloud because",
     "you don't have enough storage.",
     "Known networks will be joined automatically",
     "Manage your overall setup and preferences"],
)
def test_body_copy_is_not_mistaken_for_a_control(body):
    """An early run tapped sentences as though they were buttons."""
    assert not explore.is_useful(body)


@pytest.mark.parametrize(
    "control", ["General", "Wi-Fi", "Software Update", "Apple Account", "Add GSTIN"]
)
def test_real_control_labels_still_pass(control):
    assert explore.is_useful(control)


@pytest.mark.parametrize(
    "label",
    ["$ 75.00 a month", "₹499/mo", "£2.99 per month", "Upgrade to iCloud+",
     "Start Free Trial", "200 GB plan"],
)
def test_pricing_and_plan_controls_are_never_tapped(label):
    """A live crawl tapped "$ 75.00 a month" -- one tap from a subscription."""
    assert not explore.is_safe(label)


def test_plain_storage_sizes_without_a_plan_context_are_still_allowed():
    assert explore.is_safe("50 GB")     # a label alone is not a purchase
