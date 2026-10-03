# ADR-007: Statement snapshots are server-held frozen records, not re-derivable tokens

## Context

`GET /statement?snapshot=<token>` must page the *exact* result of the first call "even
after payments or corrections" (R262), and "existing snapshots remain unchanged during
concurrent payments or corrections" (R268, R290). The token itself carries no `from`/`to`/
`known_at` (supplying any of them alongside `snapshot` is 422, R263), so the token cannot be
a self-describing encoding of the query that gets *replayed* against live state — replaying
would reintroduce exactly the drift the feature exists to prevent.

## Options

1. **Self-describing token** (e.g. the query parameters encoded/signed into the token
   string), re-executed against current state on every paged read. Rejected immediately: it
   would reproduce R211/R254's live computation each time, which by definition changes if a
   later correction moves a payment into or out of the original window — the opposite of
   "existing snapshots remain unchanged" (R268).
2. **Server-held frozen record**: on the first (non-`snapshot`) call, compute the full
   entry list, `opening_balance`, `closing_balance`, and the resolved `to`/`known_at` once,
   store it keyed by a fresh opaque id in `service.statement_snapshots`, and have every
   paged read with that token slice the stored list — no recomputation, ever.

## Decision

Option 2. `statement_snapshots` is an in-memory `dict[token -> SnapshotRecord]` alongside
every other piece of `Service` state, covered by the same `STATE_LOCK` for both creation and
read, and cleared wholesale by reset (matching R265: "no storage survival across container
restarts is required", and giving "a token from before reset" its 404 for free, R264 — the
dict simply no longer contains it). Tokens use the same opaque-id scheme as every other id in
the service (ADR-003), prefix `snap_`; they are never parsed for meaning, only looked up.

## Consequences

- Memory grows by one record per first-page `GET /statement` call for the life of the
  container between resets; acceptable at this stage's scale and explicitly not addressed
  further (no eviction), matching R265's "no storage survival" framing — the dict is expected
  to reset along with everything else.
- Ownership (`SnapshotRecord.user_id`) must be checked before any paged read, else a caller
  could page another user's statement by guessing or reusing a leaked token — R264 ("another
  user's token ... gives 404") is the spec's own guard for exactly this.
- Because creation and every paged read happen inside the same single global lock as every
  other state access (ADR-001), "concurrent payments or corrections" can never interleave
  with a snapshot's creation in a way that produces a torn read — the snapshot is computed
  from one consistent, fully-locked view of state at the instant of its first call.
