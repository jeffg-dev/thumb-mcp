"""Optional app-specific prototype workflows. Not part of the core MCP profile."""
from __future__ import annotations
import functools
import time
from dataclasses import dataclass
from mcp.types import ImageContent, TextContent
from PIL import Image as PILImage
from . import ax, explore, flows, mirror, recorder, skills
from .errors import MirrorError
from .mirror import Frame
from .runtime import SESSION

RECORDER = recorder.Recorder()
render_result = None  # Injected by the registering server, including python -m.


@dataclass
class PendingDraft:
    """A composed-but-unsent message waiting on the user's go-ahead."""

    app: str  # "messages" | "whatsapp"
    recipient: str
    text: str
    contact_index: int = 1

PENDING: PendingDraft | None = None

def _focus_safe(fn):
    """Give keyboard focus back to the user's app once the tool finishes.

    Without this, the mirroring app stays frontmost after a call and anything
    the user types goes to the phone instead of their editor.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with ax.keep_focus(SESSION.window_id and mirror.mirroring_pid()):
            return fn(*args, **kwargs)

    return wrapper

def _shot(frame_image, header: str, frame=None):
    from dataclasses import replace
    frame = frame or SESSION.last_frame or SESSION.live_frame()
    if render_result is None:
        raise MirrorError('Extended workflows are not registered.')
    return render_result(replace(frame, image=frame_image), header)


@_focus_safe
def search_in_app(
    app: str,
    query: str,
    tab_index: int | None = None,
    tab_count: int | None = None,
) -> list[TextContent | ImageContent]:
    report, image = flows.search_in_app(SESSION, app, query, tab_index, tab_count)
    return _shot(image, report)

@_focus_safe
def open_expo_app(
    url: str | None = None, use_dev_build: bool = False
) -> list[TextContent | ImageContent]:
    report, image = flows.open_expo_app(SESSION, url, use_dev_build)
    return _shot(image, report)

@_focus_safe
def send_whatsapp(
    recipient: str, text: str, contact_index: int = 1, send: bool = False
) -> list[TextContent | ImageContent]:
    global PENDING
    report, image = flows.send_whatsapp(SESSION, recipient, text, contact_index, send)
    PENDING = (
        PendingDraft("whatsapp", recipient, text, contact_index)
        if (not send and "Drafted" in report)
        else None
    )
    return _shot(image, report)

@_focus_safe
def send_message(
    recipient: str, text: str, send: bool = False
) -> list[TextContent | ImageContent]:
    global PENDING
    report, image = flows.send_message(SESSION, recipient, text, send)
    PENDING = (
        PendingDraft("messages", recipient, text)
        if (not send and "Drafted" in report)
        else None
    )
    return _shot(image, report)

@_focus_safe
def confirm_send() -> list[TextContent | ImageContent]:
    global PENDING
    if PENDING is None:
        raise MirrorError(
            "Nothing is drafted. Call send_message() or send_whatsapp() first, "
            "show the user the draft, and confirm_send() once they agree."
        )
    draft = PENDING

    # Straight to the send button -- the draft was already screenshotted and
    # approved, so a second pre-send capture would only add latency.
    sent, status, image = flows.tap_send(SESSION, draft.app)
    if sent:
        PENDING = None
        return _shot(
            image,
            f"Sent {draft.text!r} to {draft.recipient!r} on {draft.app} "
            f"({status}). Verified: the composer is empty.",
        )

    # The draft was still sitting in the composer, so the tap missed or the
    # screen had moved on. Rebuild and send in one go rather than bouncing back
    # to the user, who has already said yes.
    if draft.app == "whatsapp":
        report, image = flows.send_whatsapp(
            SESSION, draft.recipient, draft.text, draft.contact_index, send=True
        )
    else:
        report, image = flows.send_message(
            SESSION, draft.recipient, draft.text, send=True
        )
    PENDING = None
    return _shot(image, "Send did not register, so the draft was rebuilt. " + report)

@_focus_safe
def open_url(url: str) -> list[TextContent | ImageContent]:
    ok, report, image = flows.open_url(SESSION, url)
    if not ok:
        raise MirrorError(report)
    # Deep links raise a confirmation alert; dismissing it is the caller's
    # choice, so report it rather than tapping through silently.
    if flows._alert_showing(SESSION):
        report += (
            " iOS is asking whether to open it in another app -- tap 'Open' "
            "(around device x=0.85 of the width, y=0.51 of the height) to confirm."
        )
    return _shot(image, report)

def start_recording() -> str:
    ax.ensure_accessibility()
    RECORDER.start()
    return (
        "Recording. Drive the phone by hand, then call stop_recording('a-name'). "
        "Only actions on the mirrored screen are captured."
    )

def stop_recording(name: str, app: str | None = None, description: str = "") -> str:
    steps = RECORDER.stop()
    if not steps:
        return (
            "Recorded nothing -- no input landed on the mirrored screen. The "
            "skill was not saved."
        )
    frame = SESSION.live_frame()
    skill = skills.Skill(
        name=skills.normalise(name),
        steps=steps,
        app=app,
        description=description,
        device=f"{frame.device_w}x{frame.device_h}",
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    path = skills.save(skill)
    return f"Saved to {path}.\n{skill.summary()}"

def list_skills() -> str:
    saved = skills.load_all()
    if not saved:
        return (
            f"No skills saved yet (looking in {skills.skills_dir()}). Use "
            "start_recording() to make one."
        )
    return "\n\n".join(skill.summary() for skill in saved)

def get_skill(name: str) -> str:
    return skills.load(name).summary()

@_focus_safe
def run_skill(name: str) -> list[TextContent | ImageContent]:
    skill = skills.load(name)
    report, image = skills.replay(SESSION, skill)
    return _shot(image, report)

def delete_skill(name: str) -> str:
    return (
        f"Deleted skill {name!r}." if skills.delete(name)
        else f"No skill named {name!r}."
    )

@_focus_safe
def explore_app(
    app: str, max_screens: int = 8, max_actions: int = 25, max_depth: int = 3
) -> str:
    graph = explore.explore(SESSION, app, max_screens, max_actions, max_depth)
    return graph.render()
