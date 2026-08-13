"""Map an app's screens by exploring it, breadth-first.

Produces a navigation graph: which screens exist, and which control reaches
each one. Useful on its own for understanding an unfamiliar app, and as the
"screen correspondence" layer anything comparing a running app to a design
would need.

Safety comes first here, because an explorer that taps everything on a real
phone will send messages, spend money and delete things. Two rules:

* **Nothing on the deny list is ever tapped.** Destructive and outward-facing
  controls are skipped by label, and the list errs towards over-skipping -- a
  missed screen is cheap, a sent message is not.
* **Exploration is bounded.** Screens, actions and depth all have budgets, so a
  crawl cannot wander indefinitely through a real account.

Screens are identified by the *set of text on them* rather than by pixels, so a
live clock, a spinner or a changing feed does not make the same screen look new
on every visit.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from . import flows, inputs, vision

# Labels that must never be tapped. Matched as substrings, case-insensitively,
# against the recognised text of a control.
UNSAFE_LABELS = (
    # irreversible or outward-facing
    "send", "post", "publish", "share", "reply", "comment", "tweet",
    "pay", "buy", "order", "checkout", "purchase", "subscribe", "confirm",
    "place order", "book", "donate", "transfer", "withdraw",
    # destructive
    "delete", "remove", "erase", "clear", "reset", "uninstall", "trash",
    "block", "report", "unfriend", "unfollow", "leave", "archive",
    # account-level
    "log out", "logout", "sign out", "sign-out", "deactivate", "close account",
    # calls
    "call", "facetime", "dial", "answer",
    # settings that are a pain to undo
    "airplane", "forget this network", "restore", "factory",
    # upgrade and plan pickers -- selecting a tier is one tap from paying for it
    "upgrade", "plan", "a month", "a year", "/mo", "per month", "free trial",
    "try free", "start trial",
)

# Currency symbols anywhere in a label mean it is a price or a plan.
CURRENCY = ("$", "£", "€", "₹", "¥")

# Controls that just leave the screen, and so teach us nothing about structure.
SKIP_LABELS = ("back", "cancel", "close", "dismiss", "done")


def is_safe(label: str) -> bool:
    """Whether a control is safe to tap while exploring."""
    text = label.strip().lower()
    if not text:
        return False
    if any(symbol in text for symbol in CURRENCY):
        return False
    return not any(bad in text for bad in UNSAFE_LABELS)


# Controls are short labels. Body copy is long, wordy, and often punctuated --
# an early run happily tapped "cannot be uploaded to iCloud because" and
# "you don't have enough storage." as if they were buttons.
MAX_LABEL_CHARS = 28
MAX_LABEL_WORDS = 4


def is_useful(label: str) -> bool:
    text = label.strip()
    lowered = text.lower()
    if len(text) < 2 or len(text) > MAX_LABEL_CHARS:
        return False
    if len(text.split()) > MAX_LABEL_WORDS:
        return False
    if text.endswith((".", "?", "!", ",")):
        return False                       # a sentence, not a control
    if lowered.replace(":", "").replace(".", "").replace("%", "").isdigit():
        return False                       # clocks, counters, prices
    return not any(
        lowered == skip or lowered.startswith(skip) for skip in SKIP_LABELS
    )


def fingerprint(elements) -> str:
    """A stable id for a screen, from the text on it.

    Pixels are too sensitive -- a clock, a spinner or a live feed would make
    every visit look like a new screen. Text is stable enough, and the few
    volatile strings wash out because the whole set is hashed.
    """
    words = sorted({
        element.text.strip().lower()
        for element in elements
        if len(element.text.strip()) > 1
    })
    digest = hashlib.sha1("|".join(words).encode("utf-8")).hexdigest()
    return digest[:10]


# The status bar lives above this fraction of the screen; a title sits below it
# but still near the top.
STATUS_BAR_BELOW = 0.06
TITLE_ZONE_ABOVE = 0.22


def screen_label(elements, device_h: float | None = None) -> str:
    """A human name for a screen -- its title.

    Picking the largest text alone named a screen "9:05", because the status bar
    clock is large and sits above everything. So skip the status bar, prefer the
    title zone near the top, and ignore anything that is just numbers.
    """
    def usable(element) -> bool:
        text = element.text.strip()
        if len(text) < 3:
            return False
        stripped = text.replace(":", "").replace(".", "").replace("%", "")
        return not stripped.isdigit()

    candidates = [e for e in elements if usable(e)]
    if not candidates:
        return "(no text)"

    if device_h:
        below_status = [e for e in candidates if e.y > device_h * STATUS_BAR_BELOW]
        titles = [e for e in below_status if e.y < device_h * TITLE_ZONE_ABOVE]
        if titles:
            return max(titles, key=lambda e: e.height).text.strip()[:40]
        if below_status:
            candidates = below_status

    return max(candidates, key=lambda e: e.height).text.strip()[:40]


@dataclass
class Screen:
    id: str
    label: str
    path: list[str]                       # labels tapped to get here from root
    controls: list[str] = field(default_factory=list)
    visited: bool = False


@dataclass
class Edge:
    source: str
    label: str
    target: str


@dataclass
class Map:
    app: str
    screens: dict[str, Screen] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    actions_used: int = 0
    skipped_unsafe: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"{self.app}: {len(self.screens)} screen(s), {len(self.edges)} "
            f"transition(s), {self.actions_used} action(s) used.",
            "",
        ]
        for screen in self.screens.values():
            route = " > ".join(screen.path) if screen.path else "(root)"
            lines.append(f"[{screen.id}] {screen.label}")
            lines.append(f"    reached by: {route}")
            outgoing = [e for e in self.edges if e.source == screen.id]
            for edge in outgoing:
                target = self.screens.get(edge.target)
                name = target.label if target else edge.target
                lines.append(f"    - {edge.label!r} -> [{edge.target}] {name}")
            if not screen.visited:
                lines.append("    (not explored -- budget reached)")
            lines.append("")
        if self.skipped_unsafe:
            unique = sorted(set(self.skipped_unsafe))
            lines.append(
                "Skipped as unsafe to tap: " + ", ".join(repr(u) for u in unique[:15])
            )
        return "\n".join(lines).rstrip()


def _read(session):
    frame = session.live_frame()
    elements = vision.recognize(frame.image, frame.device_w, frame.device_h)
    return frame, elements


def _navigate(session, app: str, path: list[str]) -> bool:
    """Return to a screen by re-walking its path from the app root.

    The path is a list of *labels*, not coordinates, so it survives the app
    laying things out differently on the way back.
    """
    flows.go_to_root(session)
    flows.settle(session, timeout_s=3.0, stable_for_s=0.3)
    for label in path:
        frame, elements = _read(session)
        matches = vision.find(elements, label)
        if not matches:
            return False
        inputs.tap(frame, matches[0].x, matches[0].y)
        flows.settle(session, timeout_s=4.0, stable_for_s=0.3)
    return True


def explore(
    session,
    app: str,
    max_screens: int = 8,
    max_actions: int = 25,
    max_depth: int = 3,
):
    """Walk an app breadth-first and return a Map of what was found."""
    report, _image = flows.open_app(session, app)
    if "Could not" in report or "never opened" in report:
        raise RuntimeError(f"Could not open {app!r}: {report}")
    flows.settle(session, timeout_s=4.0, stable_for_s=0.35)
    flows.go_to_root(session)
    flows.settle(session, timeout_s=3.0, stable_for_s=0.3)

    # Wait for the app to actually render. Reading too early maps a blank frame
    # as the root screen, and the crawl then finds nothing to do.
    elements: list = []
    for _attempt in range(6):
        _frame, elements = _read(session)
        if any(is_useful(e.text) for e in elements):
            break
        time.sleep(0.8)
    if not elements:
        raise RuntimeError(
            f"{app!r} showed no readable text to explore -- it may still be "
            "loading, or the session may have dropped."
        )

    root = Screen(
        id=fingerprint(elements),
        label=screen_label(elements, _frame.device_h),
        path=[],
    )
    graph = Map(app=app, screens={root.id: root})
    queue: list[Screen] = [root]

    while queue and graph.actions_used < max_actions and len(graph.screens) < max_screens:
        screen = queue.pop(0)
        if screen.path and not _navigate(session, app, screen.path):
            continue  # could not get back here; skip rather than tap blindly
        if len(screen.path) >= max_depth:
            screen.visited = True
            continue

        _frame, elements = _read(session)
        screen.controls = [e.text.strip() for e in elements if is_useful(e.text)]

        for label in list(screen.controls):
            if graph.actions_used >= max_actions or len(graph.screens) >= max_screens:
                break
            if not is_safe(label):
                graph.skipped_unsafe.append(label)
                continue

            frame, current = _read(session)
            if fingerprint(current) != screen.id:
                # Drifted off the screen we were exploring; re-walk to it.
                if not _navigate(session, app, screen.path):
                    break
                frame, current = _read(session)

            matches = vision.find(current, label)
            if not matches:
                continue
            inputs.tap(frame, matches[0].x, matches[0].y)
            flows.settle(session, timeout_s=4.0, stable_for_s=0.3)
            graph.actions_used += 1

            _frame, after = _read(session)
            landed = fingerprint(after)
            if landed == screen.id:
                continue  # nothing happened, or a no-op control

            if landed not in graph.screens:
                graph.screens[landed] = Screen(
                    id=landed,
                    label=screen_label(after, _frame.device_h),
                    path=screen.path + [label],
                )
                queue.append(graph.screens[landed])
            graph.edges.append(Edge(screen.id, label, landed))

            if not _navigate(session, app, screen.path):
                break

        screen.visited = True

    return graph
