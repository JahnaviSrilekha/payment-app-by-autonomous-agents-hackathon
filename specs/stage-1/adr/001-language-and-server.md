# ADR-001: language, HTTP server and concurrency model

## Context

The track's technical direction mandates "Python 3.12 standard library unless the
coordinator records a better reason." Stage 1 needs: up to 50 concurrent in-flight HTTP
requests, a request queue of at least 256, no outbound network at runtime, a single critical
section per write, and password hashing kept outside that section.

## Options

1. `http.server.ThreadingHTTPServer` (stdlib) + one `threading.Lock` guarding all state.
2. `asyncio` + a single event loop (stdlib) handling all requests cooperatively.
3. A third-party ASGI framework (FastAPI/Starlette) for ergonomics.

## Decision

Option 1: `http.server.ThreadingHTTPServer`, `request_queue_size = 256` (satisfies the
backlog requirement), one process-wide `threading.Lock` ("`STATE_LOCK`") held for the full
duration of every state-touching handler except the password-hashing portions of
signup/login (design.md §3).

## Reasoning

- Stays within the mandated stdlib-only default; no justification is strong enough to
  deviate (option 3 would require network access only available at build time, adding
  risk for no behavioural benefit at this scale).
- `asyncio` (option 2) would also work and avoids OS threads, but stage 2's direction
  explicitly asks for in-page scripts driving a server-rendered UI with no framework
  dependency either way; a thread-per-connection model is simpler to reason about for the
  "one global lock" mental model the mandate describes literally, and Python's GIL means
  threads here are for I/O concurrency, not CPU parallelism — the lock is the only
  correctness mechanism needed.
- 50 concurrent in-flight requests as OS threads is negligible load; `ThreadingHTTPServer`
  spawns one thread per connection and that is fine at this scale within 2 vCPU / 2 GiB.

## Consequences

- All non-hashing handler logic executes serialized under `STATE_LOCK`; throughput under
  the 50-in-flight ceiling is bound by lock hold time, which is O(1) dict/list operations
  per request — well within the 5 s per-request budget.
- `hashlib.scrypt` calls in signup/login must never run while holding `STATE_LOCK` (see
  ADR-002); this is enforced by code review convention (only two handlers touch
  `hashlib`, and both are reviewed with the specific invariant in mind per task
  acceptance criteria).
- No async rewrite needed for later stages: the same lock model composes unchanged as
  state grows in stage 2-4.
