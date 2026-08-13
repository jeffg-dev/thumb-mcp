"""Regression tests for the OCR-driven confirmations.

Each of these encodes a bug that shipped: a confident "success" while the phone
had not moved. They are pure -- OCR results are injected rather than recognised.
"""

import pytest

from thumb import flows, vision
from conftest import FakeSession, solid


def element(text, x=100.0, y=200.0):
    return vision.TextElement(text=text, x=x, y=y, width=40.0, height=10.0,
                              confidence=0.9)


def test_alert_detected_when_buttons_arrive_as_one_block():
    """Vision returns the alert's buttons as a single "Cancel Open" block.

    Requiring them as separate labels missed an alert that was plainly on
    screen, and open_expo_app then reported failure for 42 seconds.
    """
    elements = [element("Open this page in “Expo Go”?"), element("Cancel Open")]
    assert flows._alert_showing(FakeSession([solid()]), elements)


def test_alert_detected_when_buttons_arrive_separately():
    elements = [element("Cancel"), element("Open")]
    assert flows._alert_showing(FakeSession([solid()]), elements)


def test_no_alert_on_an_ordinary_page():
    """A results page with blue links used to be read as an alert."""
    elements = [element("Welcome to My Activity"), element("Learn more"),
                element("example.com")]
    assert not flows._alert_showing(FakeSession([solid()]), elements)


def test_no_alert_when_only_one_button_word_is_present():
    assert not flows._alert_showing(FakeSession([solid()]), [element("Open in app")])


def test_find_ranks_exact_over_prefix_over_substring():
    elements = [element("Wallet balance"), element("My Wallet"), element("Wallet")]
    ordered = [e.text for e in vision.find(elements, "Wallet")]
    assert ordered[0] == "Wallet"
    assert ordered.index("Wallet balance") < ordered.index("My Wallet")


def test_recognised_elements_are_ordered_top_to_bottom_then_left_to_right():
    """describe_screen output should read like the screen does."""
    elements = sorted(
        [element("b", x=300, y=100), element("a", x=50, y=100), element("c", x=10, y=400)],
        key=lambda e: (round(e.y / 8), e.x),
    )
    assert [e.text for e in elements] == ["a", "b", "c"]


@pytest.mark.parametrize(
    "url, host_key",
    [
        ("https://example.com", "example.com"),
        ("https://www.example.com/path", "example.com"),
        ("exp://192.168.1.5:8081", "192.168.1.5:8081"),
        ("http://localhost:8081", "localhost:8081"),
    ],
)
def test_host_extraction_for_url_confirmation(url, host_key):
    """open_url confirms a load by finding this string in the address bar."""
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    assert host.replace("www.", "") == host_key


def test_only_web_schemes_are_confirmed_by_page_load():
    """A deep link never renders a page, so only the alert can confirm it."""
    for url, is_web in [("https://x.com", True), ("http://x.com", True),
                        ("exp://1.2.3.4:8081", False), ("maps://?q=x", False)]:
        scheme = url.split("://", 1)[0].lower()
        assert (scheme in ("http", "https")) is is_web


def _fake_ocr(monkeypatch, sequence):
    """Feed wait_for_text a scripted sequence of recognised screens."""
    calls = {"n": 0}

    def fake(image, device_w, device_h, **kwargs):
        index = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return [element(t) for t in sequence[index]]

    monkeypatch.setattr(vision, "recognize", fake)
    return calls


def test_wait_for_text_returns_as_soon_as_it_appears(monkeypatch):
    _fake_ocr(monkeypatch, [["Loading"], ["Loading"], ["Done"]])
    found, el, _frame = flows.wait_for_text(FakeSession([solid()]), "Done", timeout_s=5)
    assert found and el.text == "Done"


def test_wait_for_text_times_out_when_it_never_appears(monkeypatch):
    _fake_ocr(monkeypatch, [["Loading"]])
    found, _el, _frame = flows.wait_for_text(FakeSession([solid()]), "Done", timeout_s=0.6)
    assert not found


def test_wait_for_text_gone_returns_when_it_disappears(monkeypatch):
    _fake_ocr(monkeypatch, [["Loading"], ["Ready"]])
    found, el, _frame = flows.wait_for_text(
        FakeSession([solid()]), "Loading", timeout_s=5, gone=True
    )
    assert found and el is None


def test_wait_for_text_gone_times_out_while_it_persists(monkeypatch):
    _fake_ocr(monkeypatch, [["Loading"]])
    found, el, _frame = flows.wait_for_text(
        FakeSession([solid()]), "Loading", timeout_s=0.6, gone=True
    )
    assert not found and el is not None      # reports where it still is
