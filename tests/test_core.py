"""Exercise the registered MCP surface and action contracts without phone input."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from conftest import make_frame, solid, with_block
from thumb import server as core, vision
from thumb.errors import MirrorError
from thumb.observation import Observations


@pytest.fixture
def setup(monkeypatch):
    frame = make_frame()
    session = SimpleNamespace(last_frame=frame, live_frame=lambda: frame)
    monkeypatch.setattr(core, 'SESSION', session)
    monkeypatch.setattr(core, 'OBS', Observations())
    monkeypatch.setattr(core, 'control', lambda *a: nullcontext())
    monkeypatch.setattr(core.vision, 'recognize', lambda *a: [vision.TextElement('Settings', 100, 200, 60, 20, .99)])
    monkeypatch.setattr(core.flows, 'settle', lambda *a, **k: ('settled', session.last_frame.image))
    return session


def test_default_profile_has_nine_tools():
    names = {t.name for t in asyncio.run(core.server.list_tools())}
    assert names == {'snapshot', 'act', 'scroll', 'find', 'gesture', 'open_app', 'screenshot', 'device_info', 'reconnect'}


@pytest.mark.parametrize('mode,images', [('text', 0), ('none', 0), ('image', 1), ('both', 1)])
def test_snapshot_modes(setup, mode, images):
    result = core.snapshot(response=mode)
    assert sum(item.type == 'image' for item in result) == images
    if mode == 'text':
        assert '@1' in result[0].text
    if mode == 'none':
        assert core.OBS.ocr_calls == 0


def test_ref_tap_returns_new_observation_in_one_call(setup, monkeypatch):
    calls = []
    monkeypatch.setattr(core.inputs, 'tap', lambda *args: calls.append(args))
    core.snapshot()
    screen = core.OBS.latest.screen
    result = core.act('tap', screen=screen, ref='@1')
    assert calls[0][1:] == (100, 200)
    assert core.OBS.latest.screen != screen
    assert '@1' in result[0].text
    assert len(result) == 1


def test_stale_ref_never_posts_input(setup, monkeypatch):
    calls = []
    monkeypatch.setattr(core.inputs, 'tap', lambda *a: calls.append(a))
    core.snapshot()
    screen = core.OBS.latest.screen
    setup.live_frame = lambda: make_frame(with_block(setup.last_frame.image, (100, 200, 110, 210), (255, 255, 255)))
    with pytest.raises(MirrorError, match='Stale'):
        core.act('tap', screen=screen, ref='@1')
    assert not calls


@pytest.mark.parametrize('kwargs', [
    {'action': 'tap'}, {'action': 'tap', 'screen': 's1', 'ref': '@1', 'point': (1, 2)},
    {'action': 'home', 'text': 'ignored'}, {'action': 'key', 'text': 'unsupported'},
    {'action': 'type'}, {'action': 'home', 'response': 'invalid'},
])
def test_invalid_actions_fail_before_capture(setup, kwargs):
    setup.live_frame = lambda: pytest.fail('Should not capture or act')
    with pytest.raises(MirrorError):
        core.act(**kwargs)


def test_scroll_reuses_pre_action_frame_and_reports_no_movement(setup, monkeypatch):
    calls = []
    monkeypatch.setattr(core.flows, 'scroll', lambda *a, **k: calls.append(k))
    result = core.scroll()
    assert calls[0]['frame'] is setup.last_frame
    assert 'no movement' in result[0].text
    assert '@1' in result[0].text


def test_find_checks_zero_limit_without_scrolling(setup, monkeypatch):
    monkeypatch.setattr(core.flows, 'scroll', lambda *a, **k: pytest.fail('Unexpected input'))
    assert 'found 1' in core.find('Settings', max_scrolls=0)[0].text
    with pytest.raises(MirrorError, match='after 0 scrolls'):
        core.find('Missing', max_scrolls=0)


def test_find_never_taps_ambiguous_text(setup, monkeypatch):
    item = vision.TextElement('Settings', 100, 200, 60, 20, .99)
    monkeypatch.setattr(core.vision, 'recognize', lambda *a: [item, item])
    monkeypatch.setattr(core.inputs, 'tap', lambda *a: pytest.fail('Unexpected input'))
    with pytest.raises(MirrorError, match='Ambiguous'):
        core.find('Settings', tap=True)


def test_mcp_call_serializes_text_and_schema_rejects_invalid_mode(setup):
    async def check():
        result = await core.server.call_tool('snapshot', {'response': 'text'})
        assert not result.is_error
        assert result.structured_content is None
        assert any(getattr(c, 'text', '').find('@1') >= 0 for c in result.content)
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError, match='response'):
            await core.server.call_tool('snapshot', {'response': 'invalid'})
    asyncio.run(check())


@pytest.mark.parametrize('action', ['home', 'app_switcher', 'spotlight'])
def test_common_navigation_runs_locally_and_returns_observation(setup, monkeypatch, action):
    calls = []
    monkeypatch.setattr(core.flows, 'go_home', lambda *a: calls.append('home'))
    monkeypatch.setattr(core.ax, 'press_menu_item', lambda *a: calls.append('app_switcher'))
    monkeypatch.setattr(core.flows, 'open_spotlight', lambda *a: calls.append('spotlight') or True)
    result = core.act(action)
    assert calls == [action]
    assert '@1' in result[0].text
