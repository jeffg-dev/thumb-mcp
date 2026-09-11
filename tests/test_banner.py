import pytest
from thumb import banner


def test_banner_hides_on_action_failure(monkeypatch):
    commands = []
    monkeypatch.setattr(banner, '_send', commands.append)
    with pytest.raises(RuntimeError):
        with banner.controlling():
            raise RuntimeError('input failed')
    assert commands == [b'show\n', b'hide\n']


def test_disabled_banner_does_not_launch(monkeypatch):
    monkeypatch.setenv('THUMB_CONTROL_BANNER', '0')
    monkeypatch.setattr(banner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Unexpected UI'))
    with banner.controlling():
        pass
    assert banner.status == 'disabled'
