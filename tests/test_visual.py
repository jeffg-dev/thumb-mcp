from PIL import Image, ImageDraw
from conftest import FakeSession
from thumb import visual, flows


def content():
    image = Image.new('RGB', (400, 800), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    for y in range(200, 600, 50):
        draw.rectangle((30, y, 200, y+10), fill='white')
    return image


def test_blank_detection_ignores_status_bar_but_sees_content():
    blank = Image.new('RGB', (400, 800), (20, 20, 20))
    ImageDraw.Draw(blank).rectangle((30, 20, 300, 40), fill='white')
    assert visual.is_blank(blank)
    assert not visual.is_blank(content())


def test_change_metric_ignores_clock_and_detects_small_app_change():
    before = content()
    clock = before.copy()
    ImageDraw.Draw(clock).rectangle((30, 20, 300, 40), fill='white')
    assert visual.changed_fraction(before, clock) == 0
    changed = before.copy()
    ImageDraw.Draw(changed).rectangle((250, 400, 290, 420), fill='white')
    assert visual.changed_fraction(before, changed) > .001


def test_blank_transition_does_not_count_as_settled():
    before = content()
    blank = Image.new('RGB', before.size, (20, 20, 20))
    after = before.copy()
    ImageDraw.Draw(after).rectangle((250, 400, 290, 420), fill='white')
    session = FakeSession([blank]*5 + [after]*10)
    status, result = flows.settle(session, timeout_s=1, poll_s=.01, stable_for_s=.01, before=before)
    assert 'settled' in status
    assert result == after


def test_persistent_blank_is_reported_not_claimed_ready():
    before = content()
    blank = Image.new('RGB', before.size, (20, 20, 20))
    status, _ = flows.settle(FakeSession([blank]), timeout_s=.1, poll_s=.01, before=before)
    assert 'blank or loading' in status
