from thumb.input_health import TapHealth


def test_first_failure_is_advisory_second_pauses():
    health = TapHealth()
    assert 'do not repeat' in health.record('no visible change after 3s')
    assert not health.paused
    recovery = health.record('no visible change after 3s')
    assert health.paused
    assert 'physical iPhone' in recovery
    assert 'unlock' in recovery
    assert 'input_recovered=true' in recovery


def test_only_successful_tap_or_explicit_reset_clears_failures():
    health = TapHealth()
    health.record('no visible change after 3s')
    health.record('still changing after 3s')
    assert health.no_effect == 1
    health.record('blank or loading after 3s')
    assert health.no_effect == 1
    health.record('settled after 4 frames')
    assert health.no_effect == 0
    health.no_effect = 2
    health.reset()
    assert not health.paused
