# ADR-006: Append-only revisions plus one `balance_view` function for every historical read

## Context

Stage 3 needs the service to answer "what was the balance as of effective time T" and "what
was known at recorded time K" (R198-290), while never changing the original payment or its
original idempotent response (R241), and while keeping corrections atomic with the money
they move (R234, R239). The handover's technical direction anticipated this: "keep payments
as an append-only ledger whose entries carry both effective and recorded times, so one
balance function can answer both questions" — stage 1/2 already stores every payment with a
single timestamp; stage 3 must split that into effective/recorded without disturbing
anything that already works.

## Options

1. **Mutate `payment.amount` in place on correction, keep a separate audit log.** Simplest
   write path, but violates R241 directly (the original payment must "remain unchanged") and
   makes "what was known at K" unanswerable without reconstructing state from the audit log
   anyway — so the audit log would have to be the real source of truth regardless, making the
   mutable field redundant and a source of drift.
2. **Append-only `revisions` list per payment (revision 1 = original); one pure
   `balance_view(user, as_of, known_at)` function reads it for every current/historical
   query; the live incrementally-maintained `User.balance` stays the fast path for ordinary
   affordability checks.**
3. **Fully event-sourced ledger (replace `User.balance` entirely; always recompute from
   scratch).** Conceptually the cleanest single source of truth, but recomputes a sum over
   every payment on every `GET /me` with no params, which the spec explicitly says is the
   *common* case (most calls carry no `as_of`/`known_at`) — a correctness-neutral but
   needless performance regression versus keeping the live field stage-1/2 already maintains.

## Decision

Option 2. `payment.revisions` is the one place correction history lives; `balance_view` (design
§18) is the one function that turns a `(user, as_of, known_at)` triple into a balance,
called by `GET /me`, `GET /statement`, and the correction handler's own
`historical_overdraft` sweep — never reimplemented per-endpoint. The live `User.balance`
field is kept, updated in the same critical section as every correction (R234), and used for
the *current*, *fully-known* view and every affordability check, with `balance_view(user,
now, now)` defined to equal it by construction — so there is exactly one code path for the
ordinary case and one for the historical case, and they are provably consistent rather than
independently maintained.

## Consequences

- Every endpoint that returns a payment continues to show `payment.amount`/`created_at` as
  originally recorded (R241) automatically, because nothing ever writes those fields after
  creation — only `revisions` grows.
- `GET /statement` entries are the only place a "selected" (possibly corrected) amount is
  shown, by deliberately overlaying the selected revision onto a copy of the payment inside
  the statement response, never onto the stored payment.
- A bug in `select_revision` or `balance_view` is a single point of failure for every
  historical read — acceptable, because it is also the single point the developer's unit
  tests and the reviewer's stress probe need to target, rather than auditing N independent
  implementations for the same invariant.
- `balance_view`'s cost is O(payments touching that user) per historical call; acceptable at
  this stage's scale (in-memory, 2 vCPU, up to 50 in-flight requests) and explicitly not
  optimized further (no secondary index), since the spec gives no volume requirement that
  would justify the added complexity.
