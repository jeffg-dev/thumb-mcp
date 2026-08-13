"""Gesture classification and skill storage."""

import json

import pytest

from thumb import skills
from thumb.errors import MirrorError
from thumb.recorder import RawEvent, Step, classify


def press(t0, x=100.0, y=200.0, hold=0.1, drag=None):
    """A down -> (drags) -> up sequence."""
    events = [RawEvent("down", t0, x, y)]
    for offset, (dx, dy) in enumerate(drag or [], start=1):
        events.append(RawEvent("drag", t0 + hold * offset / (len(drag) + 1), dx, dy))
    end = drag[-1] if drag else (x, y)
    events.append(RawEvent("up", t0 + hold, end[0], end[1]))
    return events


def test_short_still_press_is_a_tap():
    steps = classify(press(0.0, hold=0.08))
    assert [s.action for s in steps] == ["tap"]
    assert (steps[0].x, steps[0].y) == (100.0, 200.0)


def test_long_still_press_is_a_long_press():
    steps = classify(press(0.0, hold=0.9))
    assert steps[0].action == "long_press"
    assert steps[0].duration_ms >= 500


def test_press_with_movement_is_a_swipe_even_when_slow():
    """Movement wins over duration -- a slow drag is still a drag, not a hold."""
    steps = classify(press(0.0, hold=0.9, drag=[(100.0, 500.0)]))
    assert steps[0].action == "swipe"
    assert (steps[0].x2, steps[0].y2) == (100.0, 500.0)


def test_tiny_movement_still_counts_as_a_tap():
    """A finger never lands perfectly still; a few points of jitter is not a swipe."""
    steps = classify(press(0.0, hold=0.1, drag=[(103.0, 202.0)]))
    assert steps[0].action == "tap"


def test_consecutive_characters_become_one_type_step():
    events = [RawEvent("key", i * 0.05, text=c) for i, c in enumerate("hello")]
    steps = classify(events)
    assert [s.action for s in steps] == ["type_text"]
    assert steps[0].text == "hello"


def test_a_long_pause_splits_typing_into_separate_steps():
    events = [RawEvent("key", 0.0, text="a"), RawEvent("key", 5.0, text="b")]
    steps = classify(events)
    assert [s.text for s in steps] == ["a", "b"]


def test_named_keys_become_press_key_and_flush_pending_text():
    events = [RawEvent("key", 0.0, text="hi"), RawEvent("key", 0.1, keycode=36)]
    steps = classify(events)
    assert [s.action for s in steps] == ["type_text", "press_key"]
    assert steps[1].key == "return"


def test_unnamed_special_keys_are_dropped_rather_than_replayed_blindly():
    steps = classify([RawEvent("key", 0.0, text="", keycode=999)])
    assert steps == []


def test_a_burst_of_wheel_events_collapses_into_one_scroll():
    events = [RawEvent("scroll", i * 0.05, 100.0, 200.0, amount=-20) for i in range(6)]
    steps = classify(events)
    assert [s.action for s in steps] == ["scroll"]
    assert steps[0].amount == pytest.approx(-120)


def test_scroll_direction_is_preserved():
    down = classify([RawEvent("scroll", 0.0, amount=-40)])[0]
    up = classify([RawEvent("scroll", 0.0, amount=40)])[0]
    assert "down" in down.describe() and "up" in up.describe()


def test_a_realistic_mixed_flow_classifies_in_order():
    events = (
        press(0.0, x=50, y=60, hold=0.08)
        + [RawEvent("key", 1.0, text="h"), RawEvent("key", 1.05, text="i")]
        + press(2.0, x=200, y=700, hold=0.3, drag=[(200.0, 200.0)])
    )
    assert [s.action for s in classify(events)] == ["tap", "type_text", "swipe"]


# -- storage ---------------------------------------------------------------

@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setenv("THUMB_SKILLS_DIR", str(tmp_path))
    return tmp_path


def test_save_and_load_round_trips(skills_home):
    skill = skills.Skill(name="demo", steps=[Step("tap", x=1, y=2),
                                             Step("type_text", text="hi")],
                         app="Instagram", description="a demo")
    skills.save(skill)
    loaded = skills.load("demo")
    assert loaded.app == "Instagram"
    assert [s.action for s in loaded.steps] == ["tap", "type_text"]
    assert loaded.steps[1].text == "hi"


def test_names_are_normalised_so_they_are_safe_filenames(skills_home):
    skills.save(skills.Skill(name="Order My Coffee!", steps=[Step("tap", x=1, y=2)]))
    assert (skills_home / "order-my-coffee.json").exists()
    assert skills.load("order my coffee").steps


def test_loading_an_unknown_skill_lists_what_does_exist(skills_home):
    skills.save(skills.Skill(name="known", steps=[Step("tap", x=1, y=2)]))
    with pytest.raises(MirrorError, match="known"):
        skills.load("missing")


def test_an_unusable_name_is_rejected(skills_home):
    with pytest.raises(MirrorError):
        skills.normalise("!!!")


def test_a_corrupt_skill_file_does_not_hide_the_others(skills_home):
    skills.save(skills.Skill(name="good", steps=[Step("tap", x=1, y=2)]))
    (skills_home / "broken.json").write_text("{not json")
    assert [s.name for s in skills.load_all()] == ["good"]


def test_delete_reports_whether_anything_was_removed(skills_home):
    skills.save(skills.Skill(name="temp", steps=[Step("tap", x=1, y=2)]))
    assert skills.delete("temp") is True
    assert skills.delete("temp") is False


def test_summary_lists_the_steps_in_order(skills_home):
    skill = skills.Skill(name="s", steps=[Step("tap", x=10, y=20),
                                          Step("press_key", key="return")],
                         app="Messages")
    text = skill.summary()
    assert "Messages" in text and "tap (10, 20)" in text and "return" in text


def test_steps_serialise_without_empty_fields(skills_home):
    skills.save(skills.Skill(name="lean", steps=[Step("tap", x=1, y=2)]))
    raw = json.loads((skills_home / "lean.json").read_text())
    assert raw["steps"][0] == {"action": "tap", "x": 1, "y": 2}
