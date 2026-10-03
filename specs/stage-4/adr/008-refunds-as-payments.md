# ADR-008: refunds as ordinary opposite-direction payments

## Context

Stage 4 adds `POST /payments/{payment_id}/refunds`, which must move money, be idempotent,
be checked against `refund_exceeds_payment`, and show up in the activity feed, statements
and historical balance views exactly like any other payment (R292: "existing receipts and
saved statements must remain available in their original form" implies a refund is visible
the same way a payment already is, not a side-channel adjustment).

## Options

1. **A refund is a new payment row** (`payments.append_payment` with direction reversed and
   a `refund_of` tag), reusing every existing read path (`balance_view`, `GET /statement`,
   `GET /activity`) unchanged.
2. **A refund is a special adjustment to the target payment's own ledger** (e.g. a new kind
   of revision that moves money, or a field on the original payment recording a refunded
   amount) — would require every balance/statement/activity code path to learn a second way
   money can move.

## Decision

Option 1. A refund is created through the same `append_payment` helper every other payment
uses, with one new optional keyword (`refund_of`), debit and credit reversed relative to the
target (`from_user_id = target.to_user_id`, `to_user_id = target.from_user_id`). This means:

- `balance_view`, `GET /statement`, `GET /activity`, conservation and no-negative-balance
  checks need zero new code — a refund is just another payment in the existing ledger.
- The spec's own framing ("a refund is a new payment in the opposite direction") is
  implemented literally, not simulated.
- `refund_of` is the only new field; `refunded_total` (ADR's companion helper) is the only
  new aggregate, needed solely for the `refund_exceeds_payment` check (R300/R308).

## Consequences

- Refund payments must be excluded from being refund *targets themselves* (R298) and from
  being correction targets (R307) — both are simple field checks (`refund_of is not None`),
  not a parallel code path.
- `settlement_id` is never set on a refund payment (R332), so a refund of a settlement
  member does not implicitly join that settlement — this falls out for free from
  `append_payment`'s existing `settlement_id=None` default; no explicit "exclude from
  settlement" logic is needed.
- Because a refund is an ordinary payment, it is itself correctable under the *general*
  rule unless explicitly excluded — hence R307's extension of `linked_payment_immutable` is
  load-bearing, not cosmetic.
