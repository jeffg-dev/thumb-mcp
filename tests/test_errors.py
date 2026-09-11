"""Errors must name the exact fix, not just fail."""

import pytest

from thumb import errors


def test_permission_errors_name_the_exact_settings_pane():
    screen = str(errors.ScreenRecordingDenied())
    assert "Screen & System Audio Recording" in screen
    assert "Privacy & Security" in screen
    access = str(errors.AccessibilityDenied())
    assert "Accessibility" in access
    assert "Privacy & Security" in access


def test_screen_recording_error_mentions_the_relaunch_requirement():
    """macOS only applies a new Screen Recording grant on relaunch."""
    message = str(errors.ScreenRecordingDenied()).lower()
    assert "relaunch" in message or "quit" in message


def test_permission_errors_name_the_host_app_not_python():
    """TCC grants attach to the launching app, which is the usual confusion."""
    assert errors.host_app()
    assert errors.host_app() in str(errors.ScreenRecordingDenied())


def test_not_running_error_states_the_os_requirements():
    message = str(errors.MirroringNotRunning())
    assert "macOS 15" in message and "iOS 18" in message


def test_paused_error_quotes_the_on_screen_message_and_offers_reconnect():
    paused = errors.MirroringPaused("iPhone in Use", ["Connect"])
    message = str(paused)
    assert "iPhone in Use" in message
    assert "reconnect()" in message


def test_paused_error_without_a_connect_button_omits_the_hint():
    assert "reconnect()" not in str(errors.MirroringPaused("Timed Out", []))


def test_out_of_range_error_states_the_valid_bounds():
    message = str(errors.CoordinatesOutOfRange(9999, 9999, 393, 852))
    assert "393" in message and "852" in message
    assert "screenshot()" in message


def test_every_error_is_a_mirror_error():
    for cls in (errors.MirroringNotRunning, errors.WindowNotFound,
                errors.ScreenRecordingDenied, errors.AccessibilityDenied,
                errors.MirroringNotConnected):
        assert issubclass(cls, errors.MirrorError)


@pytest.mark.parametrize('ancestors,expected', [
    (['20 /opt/homebrew/bin/uv', '30 /Applications/Claude.app/Contents/Helpers/disclaimer'], '/opt/homebrew/Cellar/uv/test/bin/uv'),
    (['20 /opt/homebrew/bin/uv', '1 /Applications/Terminal.app/Contents/MacOS/Terminal'], '/Applications/Terminal.app/Contents/MacOS/Terminal'),
    (['20 /Applications/Claude.app/Contents/Helpers/disclaimer'], '/python'),
])
def test_permission_target_respects_disclaimer_boundary(monkeypatch, ancestors, expected):
    from types import SimpleNamespace
    responses = iter(ancestors)
    errors.host_app.cache_clear()
    monkeypatch.setattr(errors.os, 'getppid', lambda: 10)
    monkeypatch.setattr(errors.sys, 'executable', '/python')
    monkeypatch.setattr(errors.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=next(responses)))
    monkeypatch.setattr(errors.os.path, 'realpath', lambda p: '/opt/homebrew/Cellar/uv/test/bin/uv' if p == '/opt/homebrew/bin/uv' else p)
    try:
        assert errors.host_app() == expected
        assert expected in str(errors.ScreenRecordingDenied())
    finally:
        errors.host_app.cache_clear()
