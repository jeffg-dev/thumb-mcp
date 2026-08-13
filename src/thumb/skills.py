"""Saved skills: a recorded flow, stored as steps, replayed later.

A skill is deliberately just a list of device-point steps plus the app it was
recorded in. Nothing is captured as pixels or absolute screen coordinates, so a
skill recorded on a small window replays on a zoomed one, and on a differently
sized phone.

Replay verifies rather than assumes: it settles between steps and reports which
ones changed the screen, because a step that silently did nothing is the failure
mode worth surfacing.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import flows, inputs, mirror
from .errors import MirrorError
from .recorder import Step

SAFE_NAME = re.compile(r"[^a-z0-9_-]+")


def skills_dir() -> Path:
    """Where skills live. Override with THUMB_SKILLS_DIR."""
    configured = os.environ.get("THUMB_SKILLS_DIR")
    path = Path(configured) if configured else Path.home() / ".thumb" / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalise(name: str) -> str:
    slug = SAFE_NAME.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise MirrorError(f"{name!r} is not a usable skill name.")
    return slug


@dataclass
class Skill:
    name: str
    steps: list[Step]
    app: str | None = None
    description: str = ""
    device: str = ""
    created: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "app": self.app,
            "description": self.description,
            "device": self.device,
            "created": self.created,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        return cls(
            name=data["name"],
            steps=[Step.from_dict(step) for step in data.get("steps", [])],
            app=data.get("app"),
            description=data.get("description", ""),
            device=data.get("device", ""),
            created=data.get("created", ""),
        )

    def summary(self) -> str:
        where = f" in {self.app}" if self.app else ""
        lines = [f"{self.name}{where} -- {len(self.steps)} step(s)"]
        if self.description:
            lines.append(f"  {self.description}")
        lines += [f"  {i + 1}. {s.describe()}" for i, s in enumerate(self.steps)]
        return "\n".join(lines)


def save(skill: Skill) -> Path:
    path = skills_dir() / f"{normalise(skill.name)}.json"
    path.write_text(json.dumps(skill.to_dict(), indent=2) + "\n")
    return path


def load(name: str) -> Skill:
    path = skills_dir() / f"{normalise(name)}.json"
    if not path.exists():
        known = ", ".join(s.name for s in load_all()) or "none saved yet"
        raise MirrorError(f"No skill named {name!r}. Available: {known}.")
    return Skill.from_dict(json.loads(path.read_text()))


def load_all() -> list[Skill]:
    skills = []
    for path in sorted(skills_dir().glob("*.json")):
        try:
            skills.append(Skill.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError):
            continue  # a corrupt file should not hide the rest
    return skills


def delete(name: str) -> bool:
    path = skills_dir() / f"{normalise(name)}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def replay(session, skill: Skill, settle_s: float = 3.0):
    """Run a skill's steps. Returns (report, frame).

    Reports per-step whether the screen actually moved. A skill that quietly
    stops working -- because the app changed, or it was recorded from a
    different starting screen -- shows up as a run of steps that changed
    nothing, rather than as a confident success.
    """
    if not skill.steps:
        return f"Skill {skill.name!r} has no steps.", session.live_frame().image

    if skill.app:
        report, _image = flows.open_app(session, skill.app)
        if "Could not" in report or "never opened" in report:
            return f"Could not open {skill.app!r} to run {skill.name!r}: {report}", \
                session.live_frame().image
        flows.settle(session, timeout_s=4.0, stable_for_s=0.3)
        # Start from the app's root. iOS reopens an app wherever it was left, so
        # without this a skill replays against whatever screen happened to be
        # showing -- and can appear to "work" purely because the app resumed on
        # the screen the recording ended on.
        flows.go_to_root(session)
        flows.settle(session, timeout_s=3.0, stable_for_s=0.3)

    inert: list[int] = []
    for number, step in enumerate(skill.steps, start=1):
        frame = session.live_frame()
        before = session.frame().image
        _run_step(session, frame, step)
        flows.settle(session, timeout_s=settle_s, stable_for_s=0.3)
        if mirror.frame_difference(before, session.frame().image) <= flows.CHANGED:
            inert.append(number)

    frame = session.live_frame()
    report = f"Ran {skill.name!r}: {len(skill.steps)} step(s)."
    if inert:
        listed = ", ".join(str(n) for n in inert)
        report += (
            f" Step(s) {listed} changed nothing on screen -- the app may have "
            "moved on since this was recorded, or it started from a different "
            "screen. Check the result."
        )
    return report, frame.image


def _run_step(session, frame, step: Step) -> None:
    action = step.action
    if action == "tap":
        inputs.tap(frame, step.x, step.y)
    elif action == "long_press":
        inputs.long_press(frame, step.x, step.y, step.duration_ms or 700)
    elif action == "swipe":
        inputs.swipe(frame, step.x, step.y, step.x2, step.y2, step.duration_ms or 300)
    elif action == "scroll":
        # Recorded wheel deltas are direction, not distance: replay a normal
        # scroll rather than trying to reproduce the exact flick.
        flows.scroll(session, "down" if (step.amount or 0) < 0 else "up", 0.55)
    elif action == "type_text":
        inputs.type_text(frame.window.pid, step.text or "")
    elif action == "press_key":
        inputs.press_key(frame.window.pid, step.key or "return")
    elif action == "wait":
        time.sleep(min(5.0, (step.duration_ms or 500) / 1000.0))
    else:
        raise MirrorError(f"Unknown step action {action!r} in a saved skill.")
