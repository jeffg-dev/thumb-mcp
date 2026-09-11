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


def test_small_content_change_rejects_stale_ref_with_new_snapshot(observations):
    frame = make_frame()
    first = observations.read(frame)
    changed = make_frame(with_block(frame.image, (100, 200, 102, 202), (255, 255, 255)))
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
