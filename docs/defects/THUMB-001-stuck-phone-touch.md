# THUMB-001: Mirroring touch input stuck while keyboard and video work

Observed on 2026-09-11 with a mirrored iPhone 16 Pro. Status: confirmed user
incident; underlying OS/input-state cause unknown. Mitigated in thumb-mcp 0.2.2.

## Symptoms

- Live screenshots and OCR continue working.
- Screen Recording and Accessibility permissions are granted.
- Home, Spotlight, and typing work.
- Synthetic taps on a known Settings search result do nothing.
- Repeated synthetic taps return `no visible change`.

## Evidence and limits

The developer reproduced synthetic tap failure. Window bounds and coordinate
mapping were checked against a capture of the visible desktop rectangle. A
listen-only event tap confirmed mouse-down/up were routed to the correct
Mirroring window and process. Explicit focus, alternate posting destinations,
click pressure, window-routing fields, and cursor warping did not restore input.
Gracefully restarting only the Mac Mirroring app also did not resolve it.

The user reported restored input after interacting with the physical phone.
An independent manual-tap result before the reset was not recorded. This establishes
a phone/Mirroring session-state problem in this incident, not a proven defect in
a particular Apple component. A scroll-phase interaction was investigated but
not established as the cause. Do not change coordinates or disable permissions
based solely on these symptoms.

## Confirmed recovery

1. Stop iPhone Mirroring.
2. Pick up and unlock the physical iPhone.
3. Interact with it directly.
4. Lock it and set it down; the user returned it to its charger.
5. Resume Mirroring. Confirm a manual tap works.

Charging was part of the observed sequence, not established as a requirement.

## MCP handling

A single ineffective tap returns an advisory. Two ineffective tap-like actions
pause further tap-like input in the core API and return an informational recovery
message, not an MCP error or another automatically repeated tap. The response
asks for a manual check because noninteractive targets can produce the same
visual symptom. Successful keyboard actions do not clear the touch warning.
Read-only observations and keyboard navigation remain available.

After the user confirms touch input works, `reconnect(input_recovered=true)`
clears the pause if Mirroring is streaming. A normal reconnect does not clear it;
a failed reconnect never clears it. Get a fresh snapshot before continuing.

The detector is a per-server in-memory heuristic, not OS-level diagnosis. Server
restart clears its history. Prototype extended workflows are not covered by the
core tap circuit breaker. Do not intentionally reproduce a stuck physical-phone
state for automated testing: regression tests simulate failed taps, verify no
further input is posted, and exercise the recovery/reset behavior.
