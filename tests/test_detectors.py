"""Visual detectors, pinned against synthetic screens.

These replaced hard-coded coordinates precisely because layouts move, so the
tests describe the *shape* being detected rather than any one app's geometry.
"""

from PIL import Image, ImageDraw

from thumb import drafts


def screen_with_add_buttons(positions, size=(604, 1310)):
    """A dark screen with white, green-outlined pills -- Blinkit's ADD button."""
    image = Image.new("RGB", size, (250, 250, 250))
    draw = ImageDraw.Draw(image)
    for cx, cy in positions:
        box = [cx - 50, cy - 30, cx + 50, cy + 30]
        draw.rectangle(box, fill=(255, 255, 255), outline=(12, 131, 31), width=6)
    return image


def test_finds_every_add_button():
    found = drafts.find_add_buttons(screen_with_add_buttons([(160, 800), (350, 800), (540, 800)]))
    assert len(found) == 3


def test_orders_buttons_left_to_right_within_a_row():
    found = drafts.find_add_buttons(screen_with_add_buttons([(540, 800), (160, 800), (350, 800)]))
    xs = [x for x, _y in found]
    assert xs == sorted(xs)


def test_top_row_comes_before_a_lower_row():
    found = drafts.find_add_buttons(screen_with_add_buttons([(160, 1100), (160, 700)]))
    assert found[0][1] < found[1][1]


def test_ignores_a_screen_with_no_buttons():
    assert drafts.find_add_buttons(Image.new("RGB", (604, 1310), (250, 250, 250))) == []


def test_ignores_green_that_is_not_a_white_pill():
    """A solid green block is not a button; the interior must be white."""
    image = Image.new("RGB", (604, 1310), (250, 250, 250))
    ImageDraw.Draw(image).rectangle([100, 700, 220, 780], fill=(12, 131, 31))
    assert drafts.find_add_buttons(image) == []


def test_positions_are_returned_as_screen_fractions():
    found = drafts.find_add_buttons(screen_with_add_buttons([(302, 655)]))
    assert len(found) == 1
    x, y = found[0]
    assert 0.0 < x < 1.0 and 0.0 < y < 1.0
    assert abs(x - 0.5) < 0.06 and abs(y - 0.5) < 0.06
