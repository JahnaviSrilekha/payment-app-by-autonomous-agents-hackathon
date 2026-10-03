# Pocketful — Stage 4 requirements: refunds and batch corrections

Every requirement from `specs/stage-1/requirements.md`, `specs/stage-2/requirements.md` and
`specs/stage-3/requirements.md` stays in force by reference (R1-R290, A1-A20). This document
numbers only what stage 4 adds, continuing from R291/A21. Source: `we-are-devs/pocketful/spec/stage-4.md`
("Pocketful — Stage 4: refunds and batch corrections").

## Requirements

- **R291** (compatibility). All requirements from stages 1-3 continue to apply.
  > "All requirements from stages 1–3 continue to apply."
  AC: every stage-1/2/3 endpoint, error code and invariant keeps passing after stage-4 code
  is added.

- **R292** (invariant). Existing receipts and saved statements must remain available in
  their original form.
  > "Existing receipts and saved statements must remain available in their original form."
  AC: after any refund or correction batch, every prior payment response, activity-feed
  entry and previously-issued statement snapshot is byte-identical to before.

- **R293** (interface, limit). There are ten idempotent write paths total: stage 1's five,
  authorizations and captures from stage 2, corrections from stage 3, and refunds and
  correction batches in stage 4.
  > "There are ten idempotent write paths: stage 1's five, authorizations and captures from
  > stage 2, corrections from stage 3, and refunds and correction batches in this stage."
  AC: `POST /payments/{id}/refunds` and `POST /correction-batches` both require an
  `Idempotency-Key` header and follow the shared idempotent pipeline (replay/reuse/body-
  mismatch rules, stage-1 design §5).

### Refunds — `POST /payments/{payment_id}/refunds`

- **R294** (interface). Endpoint `POST /payments/{payment_id}/refunds`, body
  `{"amount": 200}`, requires an idempotency key.
  > "`POST /payments/{payment_id}/refunds`, body `{"amount": 200}`, requires an idempotency
  > key."
  AC: a request with no/empty `Idempotency-Key` header is 400 `missing_idempotency_key`
  before any body validation (shared pipeline).

- **R295** (error). Only the original receiver of the target payment may refund it, else 403
  `forbidden`.
  > "Only the original receiver may refund, else 403 `forbidden`"
  AC: a caller who is not `payment.to_user_id` gets 403 `forbidden`.

- **R296** (error). An unknown target payment is 404.
  > "unknown payment is 404."
  AC: `payment_id` not found gets 404 `not_found`.

- **R297** (behaviour, limit). The refund target may be a direct payment, request payment or
  capture, but never a refund.
  > "The target may be a direct payment, request payment or capture, but never a refund."
  AC: refunding a payment whose own `request_id` or `authorization_id` is set succeeds under
  the ordinary rules; refunding a payment that is itself a refund is rejected (R298).

- **R298** (error). Refunding a refund gives 422 `invalid_refund_target`.
  > "Refunds of refunds give 422 `invalid_refund_target`."
  AC: if `target.refund_of is not None`, 422 `invalid_refund_target`, no state change.

- **R299** (error). An invalid amount is 422 `validation_failed`.
  > "Invalid amount is 422 `validation_failed`."
  AC: non-integer, non-positive, or out-of-range (`> 1_000_000_000`) `amount` is 422
  `validation_failed` (same shape rule as every other amount field, stage-1 design §6).

- **R300** (error, limit). Refunds cumulatively may not exceed the payment's current
  corrected amount: 422 `refund_exceeds_payment`.
  > "Refunds cumulatively may not exceed the payment's current corrected amount: 422
  > `refund_exceeds_payment`."
  AC: if `amount + refunded_total(target) > target's latest revision amount`, 422
  `refund_exceeds_payment`, no state change. `refunded_total` sums the `amount` of every
  payment whose `refund_of == target.id` (refund payments are immutable, R304, so their
  `amount` never changes after creation).

- **R301** (interface, behaviour). A refund is a new payment in the opposite direction, with
  `refund_of` naming the target, `request_id: null`, `authorization_id: null`, and the
  original note/visibility.
  > "A refund is a new payment in the opposite direction, with `refund_of` naming the
  > target, `request_id: null`, `authorization_id: null`, and the original note/visibility."
  AC: the created payment has `from_user_id = target.to_user_id`,
  `to_user_id = target.from_user_id`, `amount` = the refund request's amount, `refund_of =
  target.id`, `request_id = null`, `authorization_id = null`, `note`/`visibility` copied
  from `target`.

- **R302** (interface). Return 201 with that payment on success; a replay returns 200 with
  the original body.
  > "Return 201 with that payment; replay returns 200 with the original body."

- **R303** (behaviour, error). A refund moves existing money from the receiver's (i.e. the
  original receiver's — the caller issuing the refund, `target.to_user_id`) available
  funds, or fails 409 `insufficient_funds`, atomically.
  > "It moves existing money from the receiver's available funds, or fails 409
  > `insufficient_funds`, atomically."
  AC: affordability is checked against `available(target.to_user_id)` (the refunder's
  balance minus their open holds), not bare `balance`; a failing check leaves every balance,
  record and idempotency key unchanged.

- **R304** (behaviour). Refunds never reopen a request or authorization, or restore a
  released hold.
  > "Refunds never reopen a request or authorization or restore a released hold."
  AC: refunding a captured-authorization payment or a paid request leaves the authorization
  `status`/`captured_amount` and the request `status` exactly as they were; no hold amount
  changes.

- **R305** (interface). Every other (non-refund) payment has `refund_of: null`.
  > "Other payments have `refund_of: null`."
  AC: every payment created by stage-1/2/3 paths, and every stage-1/2/3 payment imported
  from an older export, exposes `refund_of: null`.

### Corrections — extensions for refunds

- **R306** (compatibility). Stage-3 corrections remain available for ordinary direct/request
  payments.
  > "Stage-3 corrections remain available for ordinary direct/request payments."

- **R307** (error, limit). Captures and refund payments cannot themselves be corrected: 422
  `linked_payment_immutable`.
  > "Captures and refund payments cannot themselves be corrected: 422
  > `linked_payment_immutable`."
  AC: `POST /payments/{id}/corrections` on a payment with `authorization_id is not None` or
  `refund_of is not None` is 422 `linked_payment_immutable` (extends R272/R275's existing
  check by one more disjunct).

- **R308** (error, limit). A correction cannot reduce a payment below its already-refunded
  amount: 422 `refund_exceeds_payment`.
  > "A correction cannot reduce a payment below its already-refunded amount: 422
  > `refund_exceeds_payment`."
  AC: if the correction's `amount < refunded_total(payment)`, 422 `refund_exceeds_payment`,
  no state change — including inside a correction batch's per-item validation.

- **R309** (behaviour). Correction debits are checked against available funds.
  > "Correction debits are checked against available funds."
  AC: unchanged from stage-3 (R236/R285): the debtor's `available`, not bare `balance`, is
  compared against the correction's `abs(delta)`.

### Batch corrections — `POST /correction-batches`

- **R310** (interface). `POST /correction-batches` requires a settlement operator and an
  idempotency key, with the same 401/403 rules as settlements.
  > "`POST /correction-batches` requires a settlement operator and an idempotency key, with
  > the same 401/403 rules as settlements."
  AC: an unauthenticated caller is 401; a caller not in `settlement_operator_ids` is 403
  `forbidden` (positioned per A26, after per-item validation — settlements.py's real-code
  precedent).

- **R311** (interface). Body shape: `{"corrections": [{"payment_id", "expected_revision",
  "amount", "effective_at", "reason"}, ...]}`.
  > (JSON example in the spec, two-item `corrections` array of ordinary-correction-shaped
  > objects)

- **R312** (error, limit). `corrections` contains 1..32 objects with distinct `payment_id`s,
  else 422 `validation_failed`.
  > "corrections contains 1..32 objects with distinct payment_ids, else 422
  > `validation_failed`."

- **R313** (behaviour). Every item has the ordinary correction fields and validation.
  > "Every item has the ordinary correction fields and validation."
  AC: each item's `expected_revision`/`amount`/`reason`/`effective_at` follow exactly
  R222-R227's shape rules (stage-3), applied per item.

- **R314** (error). An unknown payment in any item is 404; a stale `expected_revision` is 409
  `stale_revision`.
  > "Unknown payment is 404; a stale expected revision is 409 `stale_revision`."

- **R315** (behaviour, limit). The operator may correct ordinary, request and settlement
  payments in a batch, but captures and refunds remain immutable.
  > "The operator may correct ordinary, request and settlement payments, but captures and
  > refunds remain immutable."
  AC: a batch item targeting a payment with `authorization_id is not None` or `refund_of is
  not None` is 422 `linked_payment_immutable`; a batch item targeting a `settlement_id is not
  None` payment is otherwise eligible (unlike the single-correction endpoint, R307).

- **R316** (error, limit). Correcting any settlement member requires including every member
  of that settlement in the same batch, else 422 `incomplete_settlement`.
  > "Correcting any settlement member requires including every member of that settlement,
  > else 422 `incomplete_settlement`."

- **R317** (error, limit). Members of one settlement being corrected together must have
  identical effective instants (offset spellings may differ), else 422 `validation_failed`.
  > "Members of one settlement must have identical effective instants (offset spellings may
  > differ), else 422 `validation_failed`."
  AC: comparison is by parsed instant equality (`state.parse_rfc3339` gives the same aware
  UTC datetime for `+00:00`, `Z`, or any equivalent offset), not string equality.

- **R318** (compatibility). Ordinary single-payment corrections remain available for
  nonmembers (payments with no `settlement_id`).
  > "Ordinary single-payment corrections remain available for nonmembers."

- **R319** (behaviour). Unknown fields in a batch item are ignored.
  > "Unknown fields are ignored."

- **R320** (error, precedence). Error precedence: item errors in input order, settlement
  completeness, resulting current available funds, then historical total and available
  funds at every effective/event boundary.
  > "Error precedence is: item errors in input order, settlement completeness, resulting
  > current available funds, then historical total and available funds at every
  > effective/event boundary."
  AC: see design.md §27/A26 for the exact phase ordering derived from this sentence plus the
  settlements.py real-code precedent.

- **R321** (interface). The existing codes apply: `linked_payment_immutable`,
  `refund_exceeds_payment`, `insufficient_funds`, `historical_overdraft`.
  > "The existing codes apply: `linked_payment_immutable`, `refund_exceeds_payment`,
  > `insufficient_funds`, `historical_overdraft`."

- **R322** (behaviour). Affordability is determined by the combined effect of all proposed
  revisions in the batch, not leg-by-leg.
  > "Affordability is determined by the combined effect of all proposed revisions."

- **R323** (invariant). A rejected batch leaves history, balances and idempotency records
  unchanged.
  > "A rejected batch leaves history, balances and idempotency records unchanged."

- **R324** (interface). Return 201 with `correction_batch_id`, `recorded_at` and `revisions`
  in input order.
  > "Return 201 with `correction_batch_id`, `recorded_at` and `revisions` in input order."

- **R325** (behaviour, interface). All new revisions in a batch share one `recorded_at`,
  strictly later than the previous `recorded_at` of every member; each revision also exposes
  `correction_batch_id`.
  > "All new revisions share recorded_at, strictly later than the previous recorded_at of
  > every member; each revision also exposes correction_batch_id."
  AC: a single-item correction (via `POST /payments/{id}/corrections`) exposes
  `correction_batch_id: null`.

- **R326** (error). Effective times in a batch item cannot be later than now.
  > "Effective times cannot be later than now."

- **R327** (invariant). Original payments and receipts never change.
  > "Original payments and receipts never change."

- **R328** (behaviour). Original payment and settlement retries return their original
  bodies, unaffected by later batch corrections.
  > "Original payment and settlement retries return their original bodies."

- **R329** (behaviour). New statements reflect the new revisions; earlier snapshot tokens
  continue to page their frozen entries.
  > "New statements reflect the new revisions; earlier snapshot tokens continue to page
  > their frozen entries."

- **R330** (interface, behaviour). Replays of `POST /correction-batches` return the original
  batch response with 200.
  > "Replays return the original batch response with 200."

- **R331** (interface, limit). This adds one idempotent write path (the tenth).
  > "This adds one idempotent write path."

### Closing rules

- **R332** (behaviour). A settlement payment may be refunded under the existing refund
  rules, but refunds never change settlement membership.
  > "A settlement payment may be refunded under the existing refund rules, but refunds never
  > change settlement membership."
  AC: refunding a settlement-member payment does not add the resulting refund payment to
  that `settlement_id`, and does not alter the original members' `settlement_id`.

- **R333** (concurrency). Concurrent corrections (single or batch) sharing any expected
  payment revision cannot both succeed.
  > "Concurrent corrections sharing any expected payment revision cannot both succeed."
  AC: two concurrent requests (single/single, single/batch, or batch/batch) that each expect
  the same payment's current revision number: at most one commits; the other observes the
  post-commit revision number and gets 409 `stale_revision`.

- **R334** (compatibility). A stage-4 service must accept exports produced by the same
  team's stages 1-3, retaining settlement membership, corrections and snapshots.
  > "A stage-4 service must accept exports produced by the same team's stages 1–3, retaining
  > settlement membership, corrections and snapshots."
  AC: importing a `format_version` 1, 2 or 3 export (as already defined by stages 1-3)
  continues to work unchanged under stage-4 code, with every settlement grouping and
  correction history preserved exactly as stage-3 already guarantees (see A29 for what
  "snapshots" means here).

## Assumptions

- **A21**. Error precedence for `POST /payments/{payment_id}/refunds`, established the same
  way as A15 (real-code precedent over sentence order — the spec text lists `forbidden`
  before `not_found`, but stage-1/2/3's actual handlers always resolve the resource first):
  authenticate -> resolve idempotency key (replay/reuse) -> payment lookup (404 `not_found`)
  -> ownership check (403 `forbidden`, caller must be `target.to_user_id`) -> `amount` field
  shape (422 `validation_failed`) -> refund-of-refund check (422 `invalid_refund_target`) ->
  `refund_exceeds_payment` (422) -> `insufficient_funds` (409, against the refunder's
  `available`). This keeps the established "field validation, then structural/content 422s,
  then 409s" shape from stage-3's correction handler (A15).

- **A22**. `refunded_total(payment)` sums the `amount` field (not a revision lookup) of
  every payment whose `refund_of == payment.id`, because refund payments are themselves
  immutable (R307) and so never acquire a second revision — their `amount` at creation is
  permanent. "The payment's current corrected amount" (R300) is
  `payment.revisions[-1]["amount"]` (the existing `balance_view`/correction machinery's
  notion of "current", ADR-006), not the original `payment.amount`.

- **A23**. A payment with `request_id` set (an ordinary request-pay) or `authorization_id`
  set (a capture) is a valid refund target under R297 ("direct payment, request payment or
  capture") with no special-casing beyond the ordinary rules — only `refund_of is not None`
  (R298) excludes a target. Refunding a capture does not touch the parent authorization
  (R304); refunding a paid request does not reopen the request (R304).

- **A24**. The single-payment correction endpoint's `linked_payment_immutable` check (stage-3
  design §19 step 4) becomes `settlement_id is not None or authorization_id is not None or
  refund_of is not None` (R307 adds the third disjunct). The batch endpoint's per-item
  immutability check is the same test **without** the `settlement_id` disjunct (R315):
  `authorization_id is not None or refund_of is not None` — batches are the only path that
  may correct a settlement member (R316/R318).

- **A25**. `refund_exceeds_payment` for a correction (single or batch item, R308) is a
  content-dependent 422 check (it needs the stored `refunded_total`, not just the payment's
  static linkage), but it is placed in the same "422s before 409s" bucket the real
  stage-3 code already uses (field validation 422 -> `linked_payment_immutable` 422 ->
  [new] `refund_exceeds_payment` 422 -> `stale_revision` 409 -> `insufficient_funds` 409 ->
  `historical_overdraft` 409) — immediately after `linked_payment_immutable`, since both are
  422s that depend on the target payment's own history rather than on the caller-supplied
  `expected_revision`.

- **A26**. `POST /correction-batches`'s four-phase error precedence (R320), derived from the
  spec sentence plus `settlements.py`'s real-code precedent (entry-shape validation runs
  entirely, including every entry's own 404/422 checks, before the operator-permission
  check, which itself runs before the collective affordability check — `create_settlement`):
  1. Request-shape: `corrections` is an array of 1..32 objects with distinct `payment_id`s
     (422 `validation_failed`, R312).
  2. Per-item validation, items visited in input order, each item fully validated (field
     shape 422 -> payment lookup 404 -> `linked_payment_immutable` 422 ->
     `refund_exceeds_payment` 422 -> `stale_revision` 409 -> `effective_at` not later than
     now 422) before moving to the next item; the first failing item's error is returned.
  3. Operator permission (403 `forbidden`, R310) — after every item is individually valid,
     mirroring `create_settlement`'s real ordering.
  4. Settlement completeness (R316/R317): for every `settlement_id` touched by any item
     (groups visited in the order their first member appears), every member of that
     settlement must be present in the batch (422 `incomplete_settlement`) and every present
     member's `effective_at` for that settlement must parse to the same instant (422
     `validation_failed`).
  5. Combined `insufficient_funds` (409): net per-user delta of every proposed revision,
     checked the same collective way `settlements._check_affordability` already checks
     settlement legs (R322).
  6. Combined `historical_overdraft` (409): the existing single-correction sweep
     (`corrections._sweep_historical_overdraft`, stage-3 design §19 step 8) generalized to
     evaluate every boundary instant touched by any party in the batch, with **all**
     tentative revisions of the batch appended at once (R320's final phase), discarding all
     of them together on any violation (R323).

- **A27**. A batch's single shared `recorded_at` (R325) is computed once as the maximum,
  over every targeted payment, of that payment's `next_recorded_at`-style candidate (stage-3
  `corrections.next_recorded_at`: wall-clock now truncated to seconds, bumped to one second
  past that payment's own latest `recorded_at` if not already strictly later) — the maximum
  of several values each already strictly greater than its own payment's prior
  `recorded_at` is still strictly greater than every one of them, so one shared timestamp
  satisfies R325 for every member without a second pass.

- **A28**. `format_version` bumps to **4**. Two new stored fields must survive a
  stage-4-to-stage-4 export/import round trip because neither is re-derivable after the
  fact: `Payment.refund_of` (needed for R298/R300/R307/R308's correctness — without it a
  re-imported refund payment would be indistinguishable from an ordinary one) and
  `Revision.correction_batch_id` (needed for R325's interface contract on
  `GET /payments/{id}/revisions`, R243). Import accepts `format_version` 1, 2, 3 or 4; for
  any version below 4, every payment defaults `refund_of = null` and every revision defaults
  `correction_batch_id = null` (the same defaulting pattern A20 already established for
  `revisions`/`base_balance` on formats 1-2). Two consequences of this assumption are
  directly testable and are not independent requirements (tester's completeness review,
  tagged `design-29`): the format-4 round trip (a stage-4 export, re-imported, preserves
  every payment's `refund_of` and every revision's `correction_batch_id` exactly) and the
  two per-field import validations design §29 adds — a non-null `refund_of` must name a
  payment present in the same export (else 422 `validation_failed`, nothing imported) and a
  present `correction_batch_id` must be a string or `null`.

- **A29**. The closing sentence's "retaining settlement membership, corrections and
  snapshots" (R334) is read as "retaining whatever each earlier format already carries" —
  `settlement_id` grouping and revision/correction history, both already preserved by
  stage-3's import (A20) — plus the historical-statement **feature** continuing to work
  after import, not a new requirement to export `statement_snapshots` verbatim. ADR-007/R265
  already settled that statement snapshots do not survive a restart or import, and stage 4's
  spec does not revisit that decision anywhere else in its text.

- **A30**. No `GET /correction-batches/{id}` (or similar) retrieval endpoint is required —
  stage-4.md defines only the `POST`. A batch's effect is fully inspectable afterward through
  the already-existing `GET /payments/{id}/revisions` (R243), where every batch-created
  revision carries its `correction_batch_id` (R325).
