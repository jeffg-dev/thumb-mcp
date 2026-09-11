# Performance measurements

Measured 2026-09-11 on the user's Mac and mirrored iPhone 16 Pro, in Alexa
Device Settings. These are small live samples, not latency guarantees.

| Measurement | Prototype | New core |
|---|---:|---:|
| Exposed tools | 38 | 9 |
| Serialized tool catalog characters | 45,454 | 4,037 |
| Cold compact snapshot | — | 0.397 s |
| Repeated compact snapshots | — | 0.108–0.114 s (four reads) |
| OCR across five identical observations | repeated recognition | 1 call, 4 cache hits |
| Snapshot response characters | — | 396 |
| MCP scroll, including observation | image response | 2.25 s; 0 images |
| MCP find, one upward scroll | multiple operations possible | 1.98 s; 0 images |

The tool catalog decreased by about 91% in characters. This includes removing
large inferred output schemas: the prototype SDK configuration automatically
duplicated annotated results as structured output. Core registration explicitly
disables that duplication.

These are character counts, not token counts or billed-cost measurements.
Actual model costs depend on the host's tool discovery, prompt caching, image
processing, and conversation history. No host billing telemetry was available.

Navigation testing also exposed an early-stability race: an app can display an
unchanged old screen before reacting to input. Action settling now waits for an
initial visible change before declaring stability; a regression test covers a
delayed response. Visual stability is still not semantic completion.

Use `uv run python scripts/benchmark.py` for read-only measurements on the current
screen. It emits no screen text or images. `device_info()` includes the last five
tool timings and OCR counters; timings remain in a bounded in-memory buffer.

Final live scroll/find measurements include OpenCV analysis and the control
banner. The banner's standalone test confirmed show/hide acknowledgements and
unchanged foreground application. Home/App Switcher/Spotlight have device-free
routing tests; this run did not exercise every app-specific launch flow live.

Validation: 226 tests, including actual MCP stdio handshakes for both profiles,
stale-reference rejection, OCR reuse, blank transitions, delayed input response,
and banner cleanup. Wheel and source distribution build successfully.
