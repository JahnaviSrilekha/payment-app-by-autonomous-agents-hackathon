# Pocketful — Stage 3 requirements

Source: `we-are-devs/pocketful/spec/stage-3.md`. Section references use the spec's own
headers (it has no numbered sections); `stage-1.md §N` refers to stage-1's numbered
sections. Every stage-1 requirement (R1–R103) and stage-2 requirement (R104–R192) stays in
force by reference and is not restated here; numbering continues from R193 so no stage's
ids collide. Assumptions continue from A14 (stages 1-2 used A1–A13).

Brand: "Pebble" remains the visible app name (header, every page title, signup/login copy)
per the human's original task; purely cosmetic, no spec identifier changes.

## A. Payment timestamps and seeding (header "Payment timestamps")

- **R193** (interface). "Every payment's `created_at` is an RFC 3339 instant with an offset
  identifying when it moved money. Every endpoint returning a payment includes it." Applies
  to every payment-bearing response: payments, activity, requests, authorizations/captures,
  statement entries.
- **R194** (behaviour). "`GET /activity` retains its existing ordering by this field" —
  ordering by `created_at`, unchanged from stage-1 §5.
- **R195** (behaviour). "Seeded payments may supply `created_at`; omission uses reset time,
  before subsequent API-created payments."
- **R196** (error, limit). "A seeded `created_at` in the future gives `422
  validation_failed` from `POST /_test/reset`, with no state change." Reset must validate
  the full fixture before applying any of it.
- **R197** (invariant). "A fixture's `balance` remains the balance after all seeded
  payments. Loading those payments must not change that balance." The seed loader must
  reconcile ledger entries with the given ending balance exactly, never recompute/drift it.

## B. `GET /me` as of an instant (header "GET /me as of an instant")

- **R198** (interface). `as_of` is an optional query parameter, an RFC 3339 instant with an
  offset.
- **R199** (error). "Anything else — a naive local time, a bare date, an empty value — is
  422 `validation_failed`."
- **R200** (compatibility). "Without temporal query parameters the response retains the
  existing money fields and reports current corrected values" — the no-`as_of`,
  no-`known_at` response is the live current view (already reflecting the latest revision
  of every payment).
- **R201** (behaviour). With `as_of`: `balance` is the caller's balance as it stood at that
  instant — after every payment of theirs with `created_at` at or before `as_of`, before
  every payment after it. A payment made at exactly `as_of` counts as having happened.
- **R202** (behaviour). An `as_of` at or after the latest payment returns the current
  balance.
- **R203** (behaviour). An `as_of` before the earliest payment returns the opening balance —
  what the wallet held before anything moved.
- **R204** (interface). The response carries `as_of` back exactly as given.

## C. `GET /statement` (header "GET /statement")

- **R205** (interface). `from` and `to` are optional; `from` defaults to the opening of the
  wallet, `to` defaults to now.
- **R206** (interface, compatibility). `limit` and `offset` behave exactly as in
  `GET /requests` (same defaults, bounds and error codes).
- **R207** (behaviour). Returns the payments the caller sent or received in the half-open
  window `[from, to)`, oldest first, each with the caller's balance immediately after it.
- **R208** (interface). Response shape: `opening_balance`, `entries[]` (each with `payment`,
  `delta`, `balance_after`), `closing_balance`, `has_more`.
- **R209** (behaviour). Entries are ordered by `created_at` ascending, then payment `id`
  ascending for ties (refined for revisions/`known_at` by R254).
- **R210** (behaviour). `opening_balance` is the balance immediately before `from`;
  `closing_balance` is the balance immediately before `to`.
- **R211** (invariant). `opening_balance` plus all `delta` values in the full window must
  equal `closing_balance`. A sent payment has a negative `delta`; a received payment has a
  positive `delta`.
- **R212** (invariant, limit). Pagination must not change an entry's `balance_after` or the
  window's opening/closing balances — these describe the full window regardless of `limit`
  and `offset`.
- **R213** (behaviour). Only payments sent or received by the caller appear, including when
  other payments are public — the activity-feed visibility rules do not apply to statements.

## D. Effective time, recorded time — the revision model (header "Effective time, recorded
time, and corrections")

- **R214** (behaviour). Every payment has a revision history; revision 1 has `amount` as
  originally paid and `effective_at = recorded_at = created_at`.
- **R215** (behaviour). A seeded payment's supplied `created_at` is also its original
  recorded/effective time; omission uses reset time.
- **R216** (invariant). Opening balances equal seeded ending balances minus the net effect of
  original seeded payments; corrections must not change those opening balances.
- **R217** (invariant). New accounts open at zero.
- **R218** (limit). Seeded history must be consistent and nonnegative — validated atomically
  at reset alongside R196.

## E. `POST /payments/{payment_id}/corrections` — request shape and validation

- **R219** (interface). Requires an idempotency key and must be called by the original
  sender.
- **R220** (error). An authenticated non-sender gets 403 `forbidden`.
- **R221** (error). An unknown payment gets 404.
- **R222** (interface). Body: `expected_revision`, `amount`, `effective_at`, `reason` — all
  four fields required.
- **R223** (limit). `expected_revision` is a positive integer.
- **R224** (limit). `amount` is an integer `0..1000000000`; zero reverses the entire payment.
- **R225** (limit). `reason` is a string of 1..200 characters.
- **R226** (limit). `effective_at` is an RFC 3339 instant with offset, not later than now.
- **R227** (error). Invalid input is 422 `validation_failed`.
- **R228** (behaviour). A correction changes neither the parties nor the visibility of the
  payment.
- **R229** (behaviour, interface). Success appends an immutable revision and returns 201
  with `payment_id`, `revision`, `amount`, `effective_at`, server-assigned `recorded_at`, and
  `reason`.
- **R230** (invariant). Recorded times for one payment strictly increase across revisions.
- **R231** (concurrency, error). A stale `expected_revision` gives 409 `stale_revision`.
- **R232** (retry). A successful replay (same key) returns that original revision with 200,
  even after newer revisions exist.
- **R233** (error). A different body under the same idempotency key gives 409
  `idempotency_key_reuse`.

## F. Correction money movement and error precedence

- **R234** (behaviour). The difference from the previous amount moves between the same two
  wallets, in the same atomic step.
- **R235** (behaviour). Increasing the amount debits the original sender; decreasing it
  debits the original receiver.
- **R236** (error, precedence). A currently unaffordable debit gives 409
  `insufficient_funds`.
- **R237** (error, precedence). Otherwise, if any user's corrected balance would be negative
  at any effective-time boundary, 409 `historical_overdraft`.
- **R238** (behaviour). Balances at a boundary include the combined effect of all movements
  at that instant.
- **R239** (invariant). Either failure (`insufficient_funds` or `historical_overdraft`)
  preserves balances, revision history, statements and idempotency state unchanged.
- **R240** (invariant). The sum of balances must equal the seeded total in every historical
  view.

## G. Read-side effects of corrections

- **R241** (invariant). The original payment and every original idempotent response remain
  unchanged.
- **R242** (behaviour). `GET /activity` continues to display the original payment; correction
  records are not new feed payments.
- **R243** (interface). `GET /payments/{payment_id}/revisions` returns
  `{"revisions": [...]}` in revision order, including revision 1 (`reason: ""`).
- **R244** (error). Only the two parties to the payment can read the revisions endpoint; a
  third party gets 404 even for a public payment.
- **R245** (error). No token is 401.

## H. `known_at` on `GET /me` and `GET /statement`

- **R246** (interface). Both accept optional `known_at`, an RFC 3339 instant with offset.
- **R247** (behaviour). For each payment, select its latest revision recorded at or before
  `known_at`; if none was yet recorded, that payment contributes nothing.
- **R248** (compatibility). Omission of `known_at` means everything known when the read
  begins.
- **R249** (behaviour). The selected revision is applied according to its effective time.
- **R250** (compatibility). `as_of` retains its inclusive meaning; a statement retains its
  half-open window, under `known_at` too.
- **R251** (behaviour). Both query instants (`as_of`/`from`/`to` and `known_at`) may be in
  the future.
- **R252** (error). Invalid or empty instants are 422.
- **R253** (interface). Echo supplied `known_at` exactly.
- **R254** (behaviour). Statement ordering is by selected `effective_at`, then payment id —
  this is the general rule; it reduces to R209's `created_at` ordering when no correction
  exists and revision 1 is selected, since `effective_at == created_at` there.
- **R255** (interface). Each statement entry retains `payment`, `delta`, `balance_after`, and
  adds the selected `revision`, `effective_at`, `recorded_at`.
- **R256** (behaviour). `payment.amount` is the selected amount for that statement.
- **R257** (behaviour). Zero-amount revisions still appear as entries with zero delta.
- **R258** (invariant). No correction is counted alongside the revision it replaces.
- **R259** (compatibility). With no corrections and no `known_at`, previous behavior is
  unchanged.

## I. Stable statement pagination (header "Stable statement pagination")

- **R260** (interface). Every first `GET /statement` response (not given a `snapshot`)
  additionally returns an opaque `snapshot` token.
- **R261** (behaviour). The snapshot freezes the caller's selected revisions, window,
  balances, entries and default `to` at that read.
- **R262** (interface). `GET /statement?snapshot=<token>&limit=&offset=` pages that exact
  frozen result, even after later payments or corrections.
- **R263** (error). Only `limit` and `offset` may accompany a snapshot; `from`, `to` or
  `known_at` with it gives 422 `validation_failed`.
- **R264** (error). An unknown token, another user's token, or a token from before reset
  gives 404 `not_found`.
- **R265** (limit). Tokens last until reset; no storage survival across container restarts
  is required.
- **R266** (invariant). Paging a snapshot changes neither balances nor entries; the final
  partial page and offsets beyond the end must report `has_more` correctly.
- **R267** (compatibility). Unrecognized query parameters remain ignored under stage-1's
  general rule.
- **R268** (concurrency). A correction may move a payment into or out of a fresh (non-
  snapshotted) statement window; existing snapshots remain unchanged during concurrent
  payments or corrections.
- **R269** (concurrency). Concurrent corrections using the same `expected_revision` cannot
  both succeed.

## J. Settlement history and cross-stage import (header "Settlement history")

- **R270** (compatibility). Stage-1 settlements retain their original receipts and privacy
  rules.
- **R271** (behaviour). Each member's original revision uses its shared `committed_at` as
  both `effective_at` and `recorded_at`.
- **R272** (error). Single-payment corrections reject settlement members with 422
  `linked_payment_immutable`.
- **R273** (compatibility). A stage-3 service must accept exports produced by the same
  team's stage-1 or stage-2 service.
- **R274** (compatibility). The ledger must import and account for authorizations and
  captures from an imported stage-2 export.
- **R275** (error). Captures are immutable linked payments: a correction of a capture gives
  422 `linked_payment_immutable`.

## K. Historical holds (header "Historical holds")

- **R276** (behaviour). For `GET /me?as_of=T&known_at=K`, all four money fields describe
  that same view: `balance = total`, `available = total - held`.
- **R277** (behaviour). A hold starts at authorization creation; a nonfinal capture reduces
  it at capture time; a final capture, void or expiry releases the remainder at that event's
  time.
- **R278** (behaviour). Expiry takes effect at `expires_at`.
- **R279** (behaviour). Events other than clock expiry are known at their server-assigned
  event time; once creation is known, the expiry deadline is known too.
- **R280** (behaviour). For queries beyond now, an open hold expires at its deadline.
- **R281** (behaviour). Without `as_of`, use the instant the request began.
- **R282** (interface). Authorizations expose `closed_at` (null while open; event time when
  closed).
- **R283** (behaviour). Historical `total` follows stage-3 effective/recorded-time rules.
- **R284** (error, precedence). A correction is rejected with 409 `historical_overdraft` if
  it makes either `total` or `available` negative at any past effective/event boundary,
  under the latest known revisions.
- **R285** (error, precedence). Current unaffordable debits still take precedence as
  `insufficient_funds`.
- **R286** (behaviour). Seeded open holds are assumed created at reset unless `created_at`
  is supplied.
- **R287** (limit). Seeded closed holds need not reconstruct a prior lifecycle.
- **R288** (behaviour). `GET /statement` still contains money movements only: authorization,
  release and expiry are not payments.
- **R289** (behaviour). Captures appear exactly once with their links.
- **R290** (invariant). Old snapshots remain unchanged after any lifecycle action or
  correction.

## Assumptions

- **A14**. "Opening of the wallet" (the default `from` for `GET /statement`) is an unbounded
  lower bound — the same reference point as `GET /me`'s pre-earliest-payment opening balance
  (R203) — not a distinct "account opened at" timestamp field, since the spec never defines
  one separately from "before anything moved."
- **A15**. Error precedence, established from stage-2's actual code (`authorizations.py`
  `capture_authorization`/`void_authorization`: lookup → ownership → field validation, all
  running inside the handler body the idempotency pipeline invokes after key resolution —
  see `payments.py`'s header comment "field validation runs here, after idempotency-key
  resolution"):
  for `POST /payments/{id}/corrections` — authenticate → resolve idempotency key
  (replay/reuse) → payment lookup (404 `not_found`) → sender check (403 `forbidden`) → field
  validation (422 `validation_failed`, R222-226) → structural immutability check (422
  `linked_payment_immutable`, R272/R275) → `stale_revision` (409) → `insufficient_funds`
  (409) → `historical_overdraft` (409, explicit spec order vs. `insufficient_funds`). For
  `GET /statement` with `snapshot` — query-combination validation (422, R263) before token
  resolution (404, R264).
- **A16**. `GET /statement`'s default `to` ("now") and the holds "instant the request began"
  (R281) both read the wall clock once at the start of request handling, so every money
  field and window computed within one request is internally consistent.
- **A17**. Sorting ties on selected `effective_at` are fully resolved by payment id (R254)
  because payment ids are unique; no further tiebreak is needed even across corrected
  payments.
- **A18**. `historical_overdraft`/conservation boundary-checking (R237-238, R284) is required
  only at the instants where some payment's `effective_at` falls, or at a hold lifecycle
  event (R277-279) — balance is piecewise constant between such events, so there is no
  continuous real-valued check to perform.
- **A19**. Snapshot tokens (R260) are server-generated opaque identifiers mapped to an
  in-memory frozen result record, not self-describing/signed — consistent with "no storage
  survival across container restarts is required" (R265) and the all-in-memory state model
  carried from stage 1.
- **A20**. Stage-3's own export bumps `format_version` to **3**, carrying `revisions` (full,
  per payment) and `base_balance` (per user) as stored fields, not re-derived at import —
  because a stage-3-to-stage-3 export/import round trip must preserve correction history
  (R241, R247 "none yet recorded → contributes nothing" would silently become wrong for a
  pre-import correction if only revision 1 survived import) and `base_balance` cannot be
  recovered from a corrected `balance` plus revision-1-only amounts without re-deriving it
  incorrectly. Stage-3 import still accepts `format_version` 1 or 2 (R273) by deriving
  `revisions`/`base_balance` fresh at import time exactly as reset does (correct for those
  formats, since they carry no correction history to lose) — the same "stage 2 bumped to
  `format_version: 2` ... stage 3 must accept both 1 and 2" pattern, one version further.
  `statement_snapshots` are never exported (R265 already permits their loss on restart).
