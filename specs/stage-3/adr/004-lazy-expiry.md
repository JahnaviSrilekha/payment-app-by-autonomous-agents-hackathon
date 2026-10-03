# ADR-004: Lazy (computed) expiry for authorizations, no background sweep

## Context

An authorization's remainder must release the instant `expires_at` passes (R159), "even if
no request occurred at the deadline." Two designs can satisfy this:

1. A background thread/timer that periodically scans `authorizations` and flips `status`
   from `open` to `expired`, writing the mutation under `STATE_LOCK` when it fires.
2. A pure function `effective_status(auth, now)` called at every read or write that needs an
   authorization's status, with no stored mutation for the expiry transition — only `captured`
   and `voided` are ever written.

## Options

- **Background sweep (1).** Needs a second thread (or a scheduled callback) contending for
  `STATE_LOCK`, a sweep interval that trades timeliness against lock contention, and careful
  handling so a sweep never races a concurrent capture/void of the same row. Adds a second
  concurrency actor to reason about, on top of the one-lock-per-request model stage-1 design
  §3 already established and that this stage otherwise keeps unchanged.
- **Lazy computation (2).** No second actor: every code path that currently reads `status`
  (GET /me's `held`, GET /authorizations, capture's and void's not-open/expired checks) calls
  the same pure function instead of reading a stored field. Correctness at the exact deadline
  follows from "now" being read at the moment of the call, inside the same lock acquisition
  that uses the result — no window where a stale "open" is read and acted on.

## Decision

Lazy computation (2). It keeps the stage-1 concurrency model exactly as it is (every request
is still the only actor touching `STATE_LOCK`), requires no sweep interval to tune, and
trivially survives export/import and process restart without replaying missed sweep ticks —
`expires_at` is the only fact that needs to survive, and it already does (it is a plain
field in the exported `Authorization`).

## Consequences

- `GET /authorizations?status=expired` and `GET /authorizations?status=open` must apply
  `effective_status`, not the stored field, before filtering (R182) — a naive
  `row.status == filter` would return stale results for a row whose deadline has passed with
  no intervening write.
- `held()`/`available()` (design §11) must always pass the current wall-clock time, read
  once per request (not once per row) for internal consistency within a single response.
- Stored `status` is a strictly smaller state space in practice than the four-value enum the
  spec describes at the API boundary — `"expired"` never appears as a *stored* value, only as
  a *computed* view. Seeded fixtures that set `status: "expired"` directly (§M) are accepted
  and stored as given (the fixture's seeded status is authoritative input, not recomputed) —
  lazy computation only generates `"expired"` from a stored `"open"` past its deadline; a
  seeded `"expired"` row stays `"expired"` regardless of whether `expires_at` is actually in
  the past (the model section allows seeded expiry times "in the past or future").
