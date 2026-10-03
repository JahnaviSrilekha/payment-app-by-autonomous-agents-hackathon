# Pocketful — Stage 4 design

Builds on `specs/stage-1/design.md` §§1-9, `specs/stage-2/design.md` §§10-16 and
`specs/stage-3/design.md` §§17-24, all unchanged except where noted below. Same process
model (`ThreadingHTTPServer`, `request_queue_size=256`, one global `STATE_LOCK`), same
idempotent-write pipeline shape, same rounding function, same settlement algorithm, same
`balance_view`/`effective_status_view`/`held_view` primitives. This document's new material
(§§25-30) covers only what stage 4 adds: refunds, the `linked_payment_immutable`/
`refund_exceeds_payment` extensions to corrections, batch corrections, and the
`format_version: 4` export/import bump.

## 25. Data model additions

```
Payment: ...(stage-1/2/3 fields)..., refund_of (nullable payment_id, default null)
  # set only on a refund payment; never set on the payment it targets.

Revision: ...(stage-3 fields: revision, amount, effective_at, recorded_at, reason)...,
  correction_batch_id (nullable, default null)
  # set only on a revision created by POST /correction-batches; null for revision 1 and
  # for every revision created by the single-payment correction endpoint.
```

No new top-level `Service` collection is needed (A30): a batch's revisions are findable
through the payments they belong to, each one tagged with `correction_batch_id`.

## 26. `POST /payments/{payment_id}/refunds` — idempotent path 9

New module `src/refunds.py`. Follows the shared pipeline (`server.run_idempotent`) through
idempotency-key resolution exactly like every other idempotent path, then runs (A21):

1. Payment lookup — 404 `not_found` if unknown (R296).
2. Ownership — caller must be `target["to_user_id"]`, else 403 `forbidden` (R295).
3. `amount` field shape — same integer-range rule as every other amount field
   (`state.parse_amount` + `state.check_amount_range`), else 422 `validation_failed`
   (R299).
4. Refund-of-refund — `target.get("refund_of") is not None` -> 422
   `invalid_refund_target` (R297/R298). A target with `request_id` or `authorization_id`
   set needs no special check: both are valid targets (A23).
5. `refund_exceeds_payment` — let `current = target["revisions"][-1]["amount"]` and
   `refunded = refunded_total(service, target["id"])` (A22, sums `amount` over every
   payment with `refund_of == target["id"]`); if `amount + refunded > current`, 422
   `refund_exceeds_payment` (R300).
6. `insufficient_funds` — if `state.available(target["to_user_id"], service, now) <
   amount`, 409 `insufficient_funds` (R303); note the refund's *debtor* is
   `target["to_user_id"]` (the original receiver, who is refunding money back), so this is
   the same `available(...) < amount` shape every other debit check uses.
7. Commit (single critical section): `payments.append_payment(service,
   from_user_id=target["to_user_id"], to_user_id=target["from_user_id"], amount=amount,
   note=target["note"], visibility=target["visibility"], refund_of=target["id"])` — a
   thin extension of the existing `append_payment` helper with one new keyword argument
   defaulting to `None` everywhere else, so every stage-1/2/3 call site is unchanged
   (R301). No authorization/request object is touched (R304); no settlement membership is
   copied (R332 — `settlement_id` defaults to `None` on the refund payment, same as every
   `append_payment` call that doesn't pass one).

`append_payment` already does the debit+credit atomically inside the caller's lock
acquisition (stage-1 design §4), so step 7 is a straight reuse with the direction reversed
(debit `target.to_user_id`, credit `target.from_user_id`) — no new money-movement code path,
only a new caller.

## 27. Corrections — `linked_payment_immutable` and `refund_exceeds_payment` extensions

`src/corrections.py`'s `create_correction` gains two checks (A24, A25), both inserted in the
existing step-6 sequence right where their status code already sits:

- Step 4 (immutability, 422, unchanged position) becomes:
  `payment.get("settlement_id") is not None or payment.get("authorization_id") is not None
  or payment.get("refund_of") is not None` -> `linked_payment_immutable` (R307).
- New step 4b (422, immediately after step 4, before step 5's `stale_revision`):
  `amount < refunded_total(service, payment["id"])` -> `refund_exceeds_payment` (R308). This
  uses the correction's *requested* `amount` (the new total, not a delta) against the same
  `refunded_total` helper §26 introduces.

`refunded_total` moves to `src/refunds.py` as a small pure function of `service` and a
payment id, imported by both `corrections.py` and `refunds.py` — one implementation, two
call sites, avoiding the duplication a second inline sum would otherwise create.

## 28. `POST /correction-batches` — idempotent path 10

New module `src/correction_batches.py`. Follows the shared pipeline through idempotency-key
resolution, then runs the six phases of A26:

**Phase 1 — request shape.** `corrections` present, a list, length 1..32, every element a
dict, `payment_id`s pairwise distinct (R312) — else 422 `validation_failed`, mirroring
`settlements._validate_entries`'s own shape checks.

**Phase 2 — per-item validation, input order.** For each item, in list order, run exactly
stage-3's per-item correction checks (`corrections.py`'s steps 1, 3, 4/4b, and the
`effective_at`-not-later-than-now check), refactored into a shared helper
`corrections.validate_item(service, payment_id, body, now)` that both the single-correction
endpoint and this phase call, so the two paths can never diverge:
payment lookup (404) -> field shape (422) -> `linked_payment_immutable` **without** the
`settlement_id` disjunct (422, R315/A24) -> `refund_exceeds_payment` (422, R308) ->
`stale_revision` (409, comparing the item's `expected_revision` against the payment's
*current* `len(revisions)` — not yet mutated, since nothing commits until phase 6) ->
`effective_at` not later than now (422, R326). The helper returns a `(payment, tentative
amount, delta, effective_raw)` tuple per item on success; the batch handler stops at the
first item whose checks fail and returns that item's error, discarding everything (R323).

**Phase 3 — operator permission.** `user["id"] not in service["settlement_operator_ids"]`
-> 403 `forbidden` (R310), positioned here per A26 (after phase 2, mirroring
`create_settlement`'s real ordering, not the spec prose's listed order).

**Phase 4 — settlement completeness.** Group the batch's items by the `settlement_id` of
their target payment (skip items whose target has none); for each settlement touched, in
the order its first member appears in the input:
- Every payment in the service with that `settlement_id` must have a corresponding item in
  this batch, else 422 `incomplete_settlement` (R316).
- Every item in this group must have an `effective_at` that parses (`state.parse_rfc3339`)
  to the same instant as every other item in the group, else 422 `validation_failed`
  (R317) — compared as parsed `datetime`s, not strings, so `+00:00`/`Z` are equal.

**Phase 5 — combined affordability.** Compute one `deltas` dict (`user_id -> net change`)
by summing every item's `delta = amount - previous_amount` onto `payment.from_user_id`
(negative) and `payment.to_user_id` (positive) — the same collective shape
`settlements._check_affordability` already uses for transfer legs (R322). For every
`user_id` with `deltas[user_id] < 0`, if `state.available(user_id, service, now) +
deltas[user_id] < 0`, 409 `insufficient_funds` (R321), nothing committed.

**Phase 6 — combined historical sweep.** Append every item's tentative revision to its
payment's `revisions` list (still inside `STATE_LOCK`, still nothing externally observable
since no response has been sent), then run `corrections._sweep_historical_overdraft`'s
boundary logic once over the **union** of every affected party across every item (instead
of one payment's two parties) and the union of every new tentative revision's instants. On
any `historical_overdraft` violation, pop every tentative revision that was appended in this
phase (in reverse order) and raise 409 `historical_overdraft` (R320's final phase) — nothing
is ever left half-applied (R323).

**Commit** (phases 5-6 having passed, the tentative revisions are already appended from
phase 6's trial — this phase makes them permanent and moves money): compute one shared
`recorded_at` (A27: the maximum, over every targeted payment, of
`corrections.next_recorded_at(payment)` evaluated *before* the tentative append); overwrite
each tentative revision's `recorded_at` with this shared value and set its
`correction_batch_id` to a freshly minted `ids.new_id("cb")`; apply each item's `delta` to
its payment's two parties' `balance` (same mechanism as `corrections.create_correction`
step 9, run once per item, all inside the one critical section); store the idempotency
record with the 201 body (R324):
`{"correction_batch_id": ..., "recorded_at": ..., "revisions": [corrections.correction_response(p, r) for each item, input order]}`,
each entry extended with `correction_batch_id` (R325).

Because phases 1-4 run to completion with no mutation, and phase 6's trial append is undone
on failure before any response is sent, a rejected batch is indistinguishable from one that
was never attempted (R323) — the same "tentative append under the lock, popped on failure"
technique `corrections.create_correction` already uses for a single item, applied to a list.

## 29. Export/import — `format_version: 4`

`state.Payment` gains `refund_of` (nullable), `state.Revision` gains `correction_batch_id`
(nullable) — both carried through **as stored**, never recomputed at import (A28), extending
ADR-006's "one source of truth" principle to the two new fields.

Import accepts `format_version` **1, 2, 3 or 4**. For versions 1-3 (no `refund_of`/
`correction_batch_id` present): every payment defaults `refund_of = null`; every revision
(whether the payment's stored `revisions` array or the one materialized for a pre-3 payment,
stage-3 design §23) defaults `correction_batch_id = null`. For version 4: both fields are
taken directly from the payload, with referential/structural validation added to
`testctl._build_from_state`/`_validate_stored_revisions` symmetric to the existing
`authorization_id` check (stage-3 design §15/23):
- a payment's `refund_of`, if not null, must be a string naming another payment already
  present in `raw["payments"]` (processed as a first pass collecting ids, then a second pass
  validating `refund_of` references, since payments can reference any other payment
  regardless of list order) — else `422 validation_failed` (A28's referential-integrity
  extension of the existing pattern).
- a revision's `correction_batch_id`, if present, must be a string or `null` — no further
  referential check, since nothing else stores or indexes batch ids (A30).

Export always emits `format_version: 4` going forward. Any other `format_version`, or a
structurally invalid `state`, is 422 `validation_failed` (unchanged from stage-1 §8).

## 30. HTTP error pipeline — additions

Unchanged shape from stage-1 §9 / stage-2 §16 / stage-3 §24 (parse body -> authenticate ->
idempotency-key resolution -> field validation -> resource/permission/business checks). New
codes slot in at the position each endpoint's own section above gives: `invalid_refund_target`
(422, refunds, §26); `refund_exceeds_payment` (422, refunds §26 and corrections §27 — reused,
not a new code per spec text R321); `incomplete_settlement` (422, batch corrections, §28);
the existing `linked_payment_immutable`, `stale_revision`, `insufficient_funds`,
`historical_overdraft`, `validation_failed`, `not_found`, `forbidden` are reused verbatim
(R321).

## Decision records

- [ADR-001: language, HTTP server and concurrency model](adr/001-language-and-server.md) (carried forward, unchanged)
- [ADR-002: password hashing](adr/002-password-hashing.md) (carried forward, unchanged)
- [ADR-003: id format](adr/003-id-format.md) (carried forward, unchanged; correction-batch ids use the same scheme, prefix `cb_`)
- [ADR-004: lazy expiry, no background sweep](adr/004-lazy-expiry.md) (carried forward, unchanged)
- [ADR-005: server-rendered UI with a shared retry/sequencing script, no SPA framework](adr/005-ui-architecture.md) (carried forward, unchanged — stage 4 adds no UI requirements, confirmed by reading stage-4.md in full)
- [ADR-006: append-only revisions + one `balance_view` function for every historical read](adr/006-revision-ledger.md) (carried forward, unchanged)
- [ADR-007: statement snapshots as server-held frozen records, not re-derivable tokens](adr/007-statement-snapshots.md) (carried forward, unchanged)
- [ADR-008: refunds as ordinary opposite-direction payments, not a new ledger primitive](adr/008-refunds-as-payments.md)
- [ADR-009: correction batches share one trial-append/undo mechanism with single corrections](adr/009-batch-corrections.md)
