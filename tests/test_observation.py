from conftest import make_frame, solid, with_block
import pytest
from thumb import observation, vision
from thumb.errors import MirrorError


@pytest.fixture
def observations(monkeypatch):
    monkeypatch.setattr(vision, 'recognize', lambda *args: [vision.TextElement('Settings', 100, 150, 70, 20, .99)])
    return observation.Observations()


def test_unchanged_screen_reuses_ocr_and_refs(observations):
    frame = make_frame()
    first = observations.read(frame)
    assert observations.read(frame) is first
    assert observations.ocr_calls == 1
    assert observations.cache_hits == 1
    assert observations.target(frame, first.screen, '@1').text == 'Settings'


def test_clock_changes_do_not_invalidate_app_targets(observations):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (0, 0, 100, 20), (255, 255, 255)))
    assert observations.read(changed).screen == first.screen


def test_target_content_change_rejects_stale_ref_with_new_snapshot(observations):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (145, 220, 165, 240), (255, 255, 255)))
    with pytest.raises(MirrorError, match='Stale.*no input sent') as error:
        observations.target(changed, first.screen, '@1')
    assert observations.latest.screen in str(error.value)
    assert '@1' in str(error.value)


def test_action_invalidation_requires_new_snapshot_even_if_pixels_same(observations):
    frame = make_frame()
    first = observations.read(frame)
    observations.invalidate()
    with pytest.raises(MirrorError, match='Stale'):
        observations.target(frame, first.screen, '@1')
    assert observations.ocr_calls == 1


@pytest.mark.parametrize('ref', ['@0', '@-1', '@2', '1', '@abc'])
def test_invalid_ref_rejected(observations, ref):
    frame = make_frame()
    first = observations.read(frame)
    with pytest.raises(MirrorError, match='Unknown target'):
        observations.target(frame, first.screen, ref)


def test_cache_is_bounded_and_filter_preserves_refs(observations):
    for i in range(6):
        observations.read(make_frame(solid(colour=(i, i, i))))
    assert len(observations.cache) == 4
    assert '@1' in observations.latest.render('settings')
    assert '@1' not in observations.latest.render('missing')


def test_tiny_unrelated_render_change_does_not_require_retry(observations):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (400, 600, 403, 610), (255, 255, 255)))
    # Even if another observation was requested, the original ID still owns
    # its original references until input or a material change invalidates it.
    newer = observations.read(changed)
    assert newer.screen != first.screen
    assert observations.target(changed, first.screen, '@1').text == 'Settings'


def test_refs_keep_their_labels_when_later_ocr_order_changes(observations, monkeypatch):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (400, 600, 403, 610), (255, 255, 255)))
    monkeypatch.setattr(vision, 'recognize', lambda *a: [vision.TextElement('Other', 200, 300, 20, 20, .9)] + list(first.elements))
    second = observations.read(changed)
    assert second.elements[0].text == 'Other'
    assert observations.target(changed, first.screen, '@1').text == 'Settings'
    assert observations.target(changed, second.screen, '@2').text == 'Settings'


def test_scroll_or_navigation_rejects_old_screen(observations):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (10, 200, 590, 1100), (255, 255, 255)))
    with pytest.raises(MirrorError, match='Stale'):
        observations.target(changed, first.screen, '@1')


def test_small_switch_change_near_coordinate_is_rejected(observations):
    frame = make_frame()
    first = observations.read(frame)
    # Device (300,400), translated into this test frame's native pixels.
    changed = make_frame(with_block(frame.image, (450, 600, 475, 620), (255, 255, 255)))
    with pytest.raises(MirrorError, match='Stale'):
        observations.validate_point(changed, first.screen, (300, 400))


def test_window_motion_uses_new_position_without_invalidating_targets(observations):
    from dataclasses import replace
    frame = make_frame()
    first = observations.read(frame)
    moved = replace(frame, window=replace(frame.window, x=100, y=200))
    assert observations.target(moved, first.screen, '@1').text == 'Settings'
    assert moved.to_global(100,150) != frame.to_global(100,150)
