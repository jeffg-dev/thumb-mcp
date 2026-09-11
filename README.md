# thumb-mcp

Manage a physical iPhone through macOS iPhone Mirroring, with a compact,
text-first MCP interface. Development happens directly on
[`jeffg-dev/thumb-mcp:dev`](https://github.com/jeffg-dev/thumb-mcp/tree/dev).
`origin` is this fork; `upstream` is the original `ishan-crd/thumb-mcp` project.

## Setup

Requires a Mac with iPhone Mirroring, a compatible paired iPhone, Python 3.11+,
and `uv`. Open iPhone Mirroring and connect the phone, then:

```sh
uv sync --dev
uv run thumb-mcp
```

For Claude Desktop, use a single MCP entry pointing to this checkout:

```json
{
  "mcpServers": {
    "thumb": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/jeffg/git/thumb-mcp", "run", "thumb-mcp"]
    }
  }
}
```

Restart the MCP host after updating this checkout so it reloads code and tools.
Version 0.2 deliberately replaces the prototype API; old tool calls are not
compatible.

## Efficient interaction

Start with `snapshot()`. It returns labels and references, not an image:

```text
screen a1b2c3-s1 · 402x874 · OCR text targets
@1 'Device Settings'
@2 'Bluetooth Devices'
@3 'Wifi Network'
```

Then call:

```text
act(action="tap", screen="a1b2c3-s1", ref="@2")
```

The server checks a fresh frame, taps, waits for visible change and stability,
and returns the resulting snapshot. No separate wait or screenshot call is
needed. A timeout reports no visible change or a still-changing screen;
settled pixels do not prove an app operation succeeded. Read the returned
observation to confirm the intended result.

References identify OCR text regions, not guaranteed buttons. They are valid
only for their issuing screen ID. Up to eight issued observations are retained,
so a later read cannot silently renumber an older screen's refs. Small unrelated
rendering changes are tolerated. Navigation, scrolling, changed pixels around
the target, a new action, or a server restart invalidate old targets. A stale-reference error includes a fresh snapshot and sends no
input. The top 6% status bar is excluded from references and cache invalidation
so clock/battery changes do not trigger OCR. Fresh window geometry is still used
when the Mac window moves.

Use `screenshot()` for icons, switches, or ambiguous layouts. It returns the
image, screen ID, and real OCR refs. **One returned image pixel equals one tap
coordinate**: a 402×874 device returns a 402×874 image with no padding or offset.
Pass the coordinates read from that image directly; never subtract a calibration
offset. If the host visually resizes an image, use its reported native dimensions.
Coordinate taps still validate the observed screen and the target neighborhood:

```text
act(action="tap", screen="a1b2c3-s2", point=[374,74])
```

## Core tools

| Tool | Purpose |
|---|---|
| `snapshot(query="", response="text")` | Read the current screen; optionally filter labels without renumbering refs |
| `act(action, ...)` | Tap, type, key, Home, back, App Switcher, or Spotlight; waits and observes |
| `scroll(direction="down", amount=0.6)` | Scroll and report movement with the resulting snapshot |
| `find(text, direction="down", max_scrolls=5, tap=False)` | Find text with bounded local scrolling; optionally tap a unique match |
| `gesture(kind, screen, start, end=None, duration_ms=300)` | Swipe, drag, long press, or double tap |
| `open_app(name)` | Launch an app via Spotlight and observe |
| `screenshot(response="both")` | Image at input resolution, plus valid refs; `response="text"` is a compact fallback |
| `device_info()` | Permissions, geometry, OCR counters, recent timings |
| `reconnect()` | Resume a paused mirroring session |

`act` arguments depend on its action:

- `tap`: `screen` plus exactly one of `ref` or `point`; or `text` to resolve a
  unique visible label directly without a screen ID. Missing/ambiguous text
  fails without input and returns actual refs.
- `type`: `text` to enter into the already focused field.
- `key`: key name in `text`, such as `return`, `delete`, or `escape`.
- `home` / `back` / `app_switcher` / `spotlight`: no target or text arguments.
  Use `open_app(name)` to launch or return to a named app.

Some hosts load only a subset of advertised tools. All nine core tools are
registered, but if `snapshot`/`find` are not loaded, use
`screenshot(response="text")` and `act(action="tap", text="Follow Up Mode")`.
Every tool response that includes an image also includes its valid refs.

Observation-producing tools accept `response="text"`, `"image"`, `"both"`, or
`"none"`. Text is the default; none returns only the action status while still
waiting/validating. Images are never included implicitly in core responses.
There is no duplicated JSON copy of text or image results in structured output.

`find` stops at a limit or lack of movement, returns an actionable error on
failure, and rejects ambiguous auto-taps. It checks the final frame for text
before declaring the end of a list. OCR and scrolling happen locally without
additional model turns. All tools share a serialized device session.

## Optional prototype workflows

Set `THUMB_TOOL_PROFILE=extended` in the MCP process environment to expose 13
additional app-specific, recording, and exploration tools (22 total):

```json
"env": {"THUMB_TOOL_PROFILE": "extended"}
```

These include `search_in_app`, `open_expo_app`, `open_url`, messaging drafts,
`confirm_send`, skill recording/replay, and `explore_app`. They remain prototype
workflows with app-specific assumptions; the generic core is preferred.
Their final image responses have also been converted to compact snapshots.
Recorded skills live in `~/.thumb/skills`, configurable with `THUMB_SKILLS_DIR`.

## Permissions and desktop control

Run `device_info()` for a best-effort permission-target executable path. Ordinary
terminal launches usually attribute permission to the terminal. Claude's
`disclaimer` helper changes this: its direct child, often Homebrew `uv`, is the
target rather than Claude.app. Paths are resolved through symlinks. Detection
uses ancestry, not an internal TCC attribution query.

Grant the reported target in System Settings → Privacy & Security:

- **Screen & System Audio Recording** for capture.
- **Accessibility** for input.

Restart the launching app after granting permissions. Homebrew upgrades may
change the executable path and require updating the grant.

Input uses the shared desktop event stream. Gestures temporarily bring Mirroring
forward and borrow the cursor; avoid typing or moving the mouse during an action.
A non-activating, click-through banner says “Thumb is controlling your computer
— please wait” during control operations and hides afterward, including on errors.
It does not block your input. Set `THUMB_CONTROL_BANNER=0` to disable it.
The helper exits when the server closes its pipe; `device_info()` reports banner
availability. If the helper cannot start, input remains available without a banner.
Core actions restore focus afterward, and scrolling restores the cursor even on
failure. Process-targeted background scrolling did not work in live testing.

Scrolling requires a real cursor warp **and** a mouse-moved event to notify
Mirroring's hover tracking, followed by continuous pixel wheel events with
began/changed/ended phases. Vertical mouse drags do not scroll lists. Momentum is
explicitly disabled for bounded scrolls.

## Performance and development

Capture uses the system `screencapture` command with a 10-second subprocess
limit. The deprecated in-process capture API stalled for tens of seconds on the
test Mac; the CLI captured the same window in about 0.1 seconds. Settling has a
polling deadline, but an individual capture can run up to the capture timeout.

OCR uses Apple's local Vision framework, with a four-frame exact-content cache.
OpenCV measures changes in the app area and detects low-detail/blank transitions.
Settling waits through blank transitions rather than declaring them ready; these
are visual heuristics, not proof that a loading operation has completed.
All this analysis runs on the Mac without model calls.
No screenshots or OCR are sent to an external service by the server; requested
MCP results are delivered to the host model. Native streaming and text deltas
are deferred until measurement justifies the complexity.

```sh
uv run pytest -q
uv run python scripts/benchmark.py
```

The benchmark only observes the phone and logs timings, sizes, and counters,
not screen text. See [measurements](docs/performance.md) and the
[implementation plan](docs/optimization-plan.md).

Core screenshots always use the device coordinate dimensions. The old
`IPHONE_MIRROR_MAX_EDGE` setting does not resize core responses.
`THUMB_OCR_FAST=1` opts into faster, less accurate OCR. The default retains
accurate recognition because misread targets are more expensive than OCR time.

Implementation: `server.py` holds the core API, `runtime.py` session/diagnostics,
`observation.py` snapshots/cache, `visual.py` OpenCV analysis, `banner.py` the
control indicator, `inputs.py` Quartz events, `mirror.py` capture
and geometry, and `extras.py` optional workflows. CI tests core registration and
runs device-free regression tests on macOS.
