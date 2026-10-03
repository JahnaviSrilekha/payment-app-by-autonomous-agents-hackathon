# Pocketful — stage 4 acceptance suite (black-box)

Tests every stage-4 requirement (R291–R334 of `specs/stage-4/requirements.md`,
from `we-are-devs/pocketful/spec/stage-4.md`: refunds, the
`linked_payment_immutable`/`refund_exceeds_payment` correction extensions,
`POST /correction-batches`, `format_version: 4` export/import) plus the full
carried stage-1/2/3 suite (R193–R290 and the stage-1 invariants R1, R2, R3,
R45) re-run under stage-4 conditions — against a running service, through its
external interfaces only. No product code is read or imported.

## Run

```sh
# full suite
python3 run.py --base-url http://127.0.0.1:8080

# cross-check a live stage-2/1 service's export (R273)
python3 run.py --base-url http://127.0.0.1:8080 --stage2-url http://127.0.0.1:8081

# list tests / emit the requirement coverage table (markdown)
python3 run.py --base-url http://x --list [--md]
```

Requirements: Python 3.10+, no packages. Exit code 0 iff nothing FAILED.
Every test resets the service with its own fixture first (`POST /_test/reset`),
so the suite is order-independent and tolerates state carried over from
earlier stages. `COVERAGE.md` is the committed requirement-id → test table.

## What stage 4 adds

- Refunds `POST /payments/{id}/refunds`: missing-key 400, 404/403 with A21
  precedence, target kinds (direct, request-payment, capture; never a
  refund-of-refund → 422 `invalid_refund_target`), amount shape and the
  1_000_000_000 range boundary, cumulative `refund_exceeds_payment` against
  the current corrected amount (A22), the created payment's shape
  (`refund_of`, `request_id: null`, `authorization_id: null`, note/visibility
  copied), 201/replay-200/exact-once with key reuse 409, `insufficient_funds`
  against **available** funds (holds counted) with atomic rejection and no
  idempotency record on failure, requests/authorizations never reopened and
  released holds never restored (R304), settlement membership untouched
  (R332), `refund_of: null` on every non-refund payment (R305)
  (R293–R305, R332).
- Corrections under refunds: captures and refund payments are
  `linked_payment_immutable` (A24), a correction may not reduce a payment
  below its refunded total with A25 precedence over `stale_revision`,
  correction debits against available funds, ordinary corrections still
  available (R306–R309).
- Batches `POST /correction-batches`: 401/403 operator rules with A26
  placement (item errors beat the operator 403; the 403 beats
  `incomplete_settlement`), idempotency-key requirement and reuse, body shape
  1..32 distinct payment_ids (exact 1 and 32 boundaries, 33 rejected before
  per-item 404s), unknown fields ignored, per-item validation in input order
  (404/422/409/R326 future `effective_at`), settlement completeness
  (`incomplete_settlement`) and identical-instant check with offset-spelling
  tolerance (`Z` vs `+02:00`), full precedence chain shape→item→403→
  completeness→combined `insufficient_funds`→combined `historical_overdraft`,
  combined affordability with netting across legs (each leg alone affordable,
  combined not — and vice versa), all-or-nothing rejection leaving
  history/balances/idempotency unchanged, 201 shape (`correction_batch_id`,
  shared `recorded_at` strictly later than every member's prior, `revisions`
  in input order), replay semantics (200 original body even after later
  mutations), settlement members correctable in batches only (R310–R331).
- Closing rules: concurrent single/single, single/batch and batch/batch
  corrections sharing an expected revision cannot both succeed (R333),
  originals (payment/settlement retries, activity entries, revision 1,
  frozen snapshots) unchanged after refunds and batches (R327–R329, R292),
  `format_version: 4` export/import round trip preserving `refund_of` and
  `correction_batch_id`, v1/v2/v3 imports defaulting the new fields to null,
  refund_of referential integrity on import (design §29) (R334, A28).
- Ten idempotent write paths: every one of the ten rejects a missing key
  with 400 `missing_idempotency_key` (R293/R331).
- Storm: 50 concurrent mixed operations (payments with retried keys,
  authorization/capture, same-revision correction races, refunds, batches,
  settlements, historical readers, snapshot paging) then conservation at
  several instants, wallet shape, authorization invariants, frozen snapshot,
  at most one winner per expected revision, per-payment refund caps,
  opposite-direction refund shape, monotonic `recorded_at`, shared batch
  `recorded_at` (R1, R2, R3, R45, R292, R300, R303, R323, R333).

The carried stage-3 material (timestamps, `as_of`, statements, revisions and
corrections, `known_at`, snapshot pagination, settlements and import,
historical holds, stage-3 storm) is unchanged from `specs/stage-3/acceptance`
except: the export assertion now expects `format_version: 4` (A28) and the
downgrade helper strips the two new stored fields so v1–v3 payloads exercise
import defaulting.

## Suite-level assumptions (conservative readings)

Carried from stage 3: SA-1 … SA-10 of `specs/stage-3/acceptance/README.md`
(revision fields on statement entries, A15 error precedence, seeded-hold
creation instant, hold roles, same-second selection sleep SA-9, fresh future
instants SA-10). Stage 4 adds:

- **SA-11.** Refund error precedence follows A21: authenticate → idempotency
  resolution → payment lookup (404) → ownership (403, caller must be
  `target.to_user_id`) → amount shape → `invalid_refund_target` →
  `refund_exceeds_payment` → `insufficient_funds` (against the original
  receiver's `available` — the source text's "moves existing money from the
  receiver's available funds", per R303's AC and design §26 step 6; the
  R303 prose parenthetical saying "the original payer's" contradicts its own
  AC and was reported to @coordinator on 2026-10-03, message f52a7dc2).
- **SA-12.** Batch error precedence follows A26's six phases; in particular
  the operator 403 sits after per-item validation (settlements.py
  real-code precedent), so a non-operator with an invalid item sees the
  item's error, and an operator with an incomplete settlement sees
  `incomplete_settlement`, not `insufficient_funds`.
- **SA-13.** Batch items are not sender-checked per item (A26 phase 2 lists
  no sender check; R310's operator requirement is the batch permission):
  the operator may correct payments they did not send, which is what makes
  "including payments that belong to a settlement" actionable.
- **SA-14.** `refunded_total` is the sum of refund payments' `amount` (A22);
  refund payments are immutable so their amounts never change. The refund
  target's "current corrected amount" is its latest revision's amount.
- **SA-15.** The stage-4 statement-snapshot semantics of R334's "snapshots"
  follow A29: importing a v1–v3 export preserves settlement membership and
  correction history, and the snapshot *feature* works afterwards; snapshots
  themselves are not expected to survive an import (ADR-007, R265).
- **SA-16.** Import validation of `refund_of` (must name another payment
  present in the export; self-reference rejected) and
  `correction_batch_id` (string or null) comes from design §29 and is tagged
  `design-29` in coverage, not to a requirement id. A28 (as amended in
  61913fea) now explicitly names these consequences and the no-import-on-422
  behavior, which the suite asserts.