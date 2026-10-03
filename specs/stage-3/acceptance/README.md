# Pocketful — stage 3 acceptance suite (black-box)

Tests every stage-3 requirement (R193–R290 of `specs/stage-3/requirements.md`,
from `we-are-devs/pocketful/spec/stage-3.md`) plus the carried stage-1
invariants (R1 conservation, R2 no negative balance, R3 exactly-once, R45 no
5xx, exact rounding) under stage-3 conditions — against a running service,
through its external interfaces only. No product code is read or imported.

## Run

```sh
# full suite
python3 run.py --base-url http://127.0.0.1:8080

# cross-check a live stage-2 service's export (R273)
python3 run.py --base-url http://127.0.0.1:8080 --stage2-url http://127.0.0.1:8081

# list tests / emit the requirement coverage table (markdown)
python3 run.py --base-url http://x --list [--md]
```

Requirements: Python 3.10+, no packages. Exit code 0 iff nothing FAILED.
Every test resets the service with its own fixture first (`POST /_test/reset`),
so the suite is order-independent and tolerates state carried over from
earlier stages. `COVERAGE.md` is the committed requirement-id → test table.

## What is covered

- Payment timestamps: `created_at` RFC 3339 with offset on every
  payment-bearing response, activity ordering, seeded `created_at`
  (supplied, omitted → reset time, future → 422 with no state change),
  fixture balance untouched by seed loading (R193–R197).
- `GET /me?as_of=`: inclusive instant semantics, opening/current boundaries,
  exact echo, naive/bare/empty → 422, no-temporal-params = current corrected
  values (R198–R204, R251).
- `GET /statement`: defaults, half-open window, oldest-first with id
  tiebreak, opening/closing balances, delta signs, full-window sum
  invariant, pagination invariance (entries, balances, `has_more` on the
  final partial page and beyond the end), own-payments-only visibility
  (R205–R213, R266).
- Revisions and corrections: revision 1 shape, body validation (all four
  fields, bounds, `effective_at` not in the future), 403/404/401/400s with
  A15 precedence, 201 response shape, strictly increasing `recorded_at`,
  stale revision, replay-even-after-newer-revisions, key reuse, parties and
  visibility unchanged, zero-amount reversal, same-amount no-op (design),
  corrections absent from the feed, revisions endpoint access (R214–R233,
  R241–R245).
- Correction money movement: same-two-wallets atomic moves, direction by
  increase/decrease, `insufficient_funds` precedence, `historical_overdraft`
  at effective-time boundaries (combined movements per boundary), rejected
  corrections preserve balances/history/statements/idempotency state,
  conservation of the seeded total in every historical view (R234–R240).
- `known_at`: selection of the latest revision recorded at or before the
  instant, unknown payments contribute nothing, exact echo, ordering by
  selected `effective_at`, selected amount/`revision`/`effective_at`/
  `recorded_at` on entries, zero-delta entries, no double-count, window and
  inclusive `as_of` retained, future instants allowed (R246–R259).
- Stable pagination: snapshot token on every first call, frozen
  entries/balances/default `to` across later payments, corrections and hold
  lifecycle events, limit/offset-only restriction (422 before 404), unknown/
  foreign/pre-reset tokens 404, unrecognized params ignored, a correction
  moving a payment into/out of a fresh window, concurrent same-revision
  corrections cannot both succeed (R260–R269, R290).
- Settlements and import: settlement members immutable with
  `committed_at` as rev-1 effective/recorded time, capture corrections 422,
  stage-3 export (`format_version: 3`, revisions + `base_balance` carried),
  import of v1/v2-shaped exports with authorizations/captures accounted,
  live stage-2 export cross-check with `--stage2-url` (R270–R275, A20).
- Historical holds: four-field consistency (`balance = total`,
  `available = total - held`) at every probe, hold start/reduction/release
  at event times, expiry at `expires_at`, `known_at` gating (unknown
  authorization contributes nothing; deadline known once creation is),
  queries beyond now, request-start default, `closed_at`, seeded open holds
  assumed created at reset, statement contains money movements only,
  captures once with links, correction vs holds overdraft precedence
  (R276–R286, R288–R289).
- Storm: 50 concurrent mixed operations (payments, authorization/capture,
  corrections with same-revision races, settlements, historical readers,
  snapshot paging), then conservation at several instants, no negative
  historical views, snapshot unchanged, exactly one winner per raced
  payment, exact replays, zero 5xx (R1, R2, R3, R45, R239, R240).

## Suite-level assumptions (conservative readings)

- **SA-1.** Statement entries always carry `revision`, `effective_at` and
  `recorded_at` — the spec's example "omits the revision fields and
  `snapshot` token described below", so the real response has them even with
  no corrections and no `known_at` (`statement_entry_revision_fields_uncorrected`).
- **SA-2.** R253's exact `known_at` echo is asserted on `GET /me`; the
  statement response shape (R208/R260) has no echo field requirement, so none
  is asserted there.
- **SA-3.** Corrections error precedence follows requirements A15:
  idempotency-key resolution (replay/reuse) → payment lookup → sender check →
  field validation → structural immutability → stale revision →
  `insufficient_funds` → `historical_overdraft`; for snapshots, the
  query-combination 422 precedes the token 404.
- **SA-4.** A seeded open authorization without `created_at` is assumed
  created at reset (R286); probes use instants captured around the reset.
- **SA-5.** Hold lifecycle actions follow stage-2 roles (R165/R176): the
  receiver captures, the payer voids.
- **SA-6.** Stage-3 exports `format_version: 3` carrying per-payment
  `revisions` and per-user `base_balance`, and imports 1/2/3 (A20).
- **SA-7.** A correction restating the current amount is valid and appends a
  revision without moving money (design §19 step 6); tagged `design-19`,
  excluded from requirement coverage.
- **SA-8.** A wrong JSON *type* in a correction body is asserted as strict
  422 `validation_failed` — adjudicated by @coordinator per stage-1 design
  §9 step 4: every correction field has an endpoint-specific rule
  (R223-R226), so wrong-type values take the endpoint's 422 code, never the
  generic 400 `malformed_request` (R43's reservation applies only where no
  endpoint-specific rule exists).
- **SA-9.** A write landing in the same second as a subsequent
  selection-based read (statement/`as_of` with `known_at` omitted) can
  under-select for up to ~1s: probed evidence shows a revision recorded at
  `T` not selected by a read beginning at `T+ε`, though R248 says omission
  means everything known when the read begins. The suite sleeps ~1.1s after
  such writes (marked `SA-9`) so verification is not blocked on the same
  second; the same-second behavior itself is reported to @reviewer for
  adjudication (candidate R248 divergence, plus the R279 capture-known
  inversion which the suite asserts spec-correctly and currently fails on).