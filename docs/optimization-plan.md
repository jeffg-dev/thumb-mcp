# MCP optimization plan

1. Replace the prototype's default API with compact observations and actions
   that settle and return observations in one call. Images are explicit.
2. Add screen-bound OCR references, exact-content OCR caching, and conservative
   validation against a fresh frame before reference/coordinate taps.
3. Expose a small core profile; retain app-specific prototype tools only under
   an explicit extended profile. Share one session and serialize input.
4. Reuse captured frames across scroll, settle, and response construction.
   Keep bounded CLI capture; defer native streaming until profiling warrants it.
5. Add tests for stale references, cache invalidation, response modes, failures,
   tool schemas, and MCP calls. Benchmark live capture/OCR/action latency and
   output size without claiming model-specific token savings.
6. Document the new API, benchmark results, and migration. Push directly to dev.

Deferred: native streaming, inferred icon semantics, arbitrary batch execution,
text deltas (full compact snapshots are easier to recover from), and background
input (did not work in live testing).

Implemented additions from live testing/user feedback:
- OpenCV app-area change measurement and blank-transition detection.
- Non-activating control banner with bounded startup, error cleanup, and opt-out.
- Home, App Switcher, Spotlight and named app launch in the core API.
- Delayed-action and blank-loading regression tests; 226 tests passing.

Status: implemented and verified; final package build and stdio checks passed.
