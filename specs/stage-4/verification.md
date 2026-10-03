# Stage 4 verification

**Verified revision:** `7a4b37844412c592e937ba3b255f6e44620c01bd` (main, fast-forward only throughout)
**Verifier:** reviewer, from a fresh clone (`payment-app-work/verify-s4-clone`)
**Date:** 2026-10-03

This is the last stage of the pocketful track; this verification closes out the whole build.

## Isolated check (judges' mode: `--mode isolated`, no outbound network, 2 vCPU / 2 GiB)

Ran `python -m harness run --track pocketful --repo <fresh clone> --stage 4 --mode isolated`
three times from the fresh clone, each into a new `--out` directory:

| Run | stage 1 | stage 2 | stage 3 | stage 4 | highest contiguous |
|---|---|---|---|---|---|
| `s4-verify-1` | pass | pass | **fail** | pass | 2 |
| `s4-verify-2` | pass | pass | **fail** | pass | 2 |
| `s4-verify-3` | pass | pass | **fail** | pass | 2 |

Stage 4 is the last stage, so there is no overshoot line to check. Stage 4's own judge
sample suite passed clean on all three runs (5/5), and the upgrade-import chain into
stage 4 (stage-1 147/147, stage-2 35/35) passed every run.

All three runs failed the identical single stage-3 test:
`test_a_statement_walks_the_balance_forward` — `assert [-200, -300] == [-300, -200]`
(`checks/s4-verify-*/stage-3.log`). This is the exact same gap already root-caused and
accepted in `specs/stage-3/verification.md` (verified revision `d8c617d`, "Stage 3: pass"):
two payments placed back-to-back land in the same wall-clock second (1-second
`created_at` granularity, a stated design decision), and the statement's tie-break for
same-instant entries is `created_at` ascending then payment `id` ascending (R209,
`pocketful/spec/stage-3.md` line 68, quoted verbatim in `specs/stage-3/requirements.md`).
Payment ids are opaque random strings, so id order does not correlate with send order —
whichever id sorts first "wins" the tie, independent of which payment was sent first.
The judge's sample check hardcodes send order as the expected order, which only holds by
chance.

Reproduced fresh against a live stage-4-clone stage-3 instance (not by reading the judge's
test source, which is off-limits): two payments sent back-to-back land at an identical
`created_at`, and the statement sorts them by id, exactly as R209 and the stage-3 folder's
(frozen, unmodified) implementation both specify. The stage-3 folder cannot be edited now
that stage 3 is accepted, and the implementation is correct per the spec's own explicit
tie-break rule — this is a timing-flaky gap in the shipped judge sample check, not a defect
in anything stage 4 (or any earlier stage) shipped, and is unrelated to any stage-4 change
(stage-3/ is carried forward byte-for-byte, confirmed at T31's mechanical carry-forward
commit). It reproduced 3/3 here versus 1/3 in stage 3's own original verification — both
outcomes are consistent with a coin-flip on two independently random payment ids, not a
regression.

## Acceptance suite (tester's `specs/stage-4/acceptance`, 71 black-box tests)

Run against a `docker build --no-cache` image, started per `stage-4/RUN.md`
(`docker run --cpus=2 --memory=2g -e PORT=8080 -p 8130:8080 ...`), twice for consistency:

**70 passed, 0 failed, 1 skipped** (`import_real_stage2_export`, R273/R274 — skipped only
because no `--stage2-url` was supplied; R273/R274 have other passing coverage), both runs.

## Requirement coverage

Every requirement id stage 4's requirements document states (R291-R334, A21-A28,
design-29) is referenced by at least one acceptance test (`specs/stage-4/acceptance/COVERAGE.md`,
regenerable with `python3 run.py --list --md`), and every one of those tests passed on
this revision. Stage 1-3's own requirement ids (R1-R290) were independently verified at
their own stage gates (`specs/stage-1/verification.md`, `specs/stage-2/verification.md`,
`specs/stage-3/verification.md`); the carried subset the stage-4 suite re-exercises in new
combinations (concurrent/storm conditions, refund and batch interactions) is listed below
alongside stage 4's own ids.

| Requirement | Status | Covering test(s) |
|---|---|---|
| R1 | pass | `storm_stage3_invariants`, `storm_stage4_invariants` |
| R2 | pass | `concurrent_corrections_same_expected_revision`, `storm_stage3_invariants`, `storm_stage4_invariants` |
| R3 | pass | `correction_replay_reuse_exactly_once`, `concurrent_same_key_correction_replay`, `storm_stage3_invariants`, `refund_replay_reuse_exactly_once`, `batch_replay_and_rejection_idempotency`, `concurrent_same_key_refund_replay`, `storm_stage4_invariants` |
| R45 | pass | `storm_stage3_invariants`, `storm_stage4_invariants` |
| R59 | pass | `corrections_auth_and_lookup_errors`, `correction_replay_reuse_exactly_once`, `correction_insufficient_funds_preserves` |
| R61 | pass | `concurrent_same_key_correction_replay` |
| R63 | pass | `corrections_auth_and_lookup_errors` |
| R99 | pass | `settlement_member_correction_rejected` |
| A20 | pass | `capture_immutable_and_import_versions` |
| R291 | pass | `storm_stage4_invariants` |
| R292 | pass | `originals_unchanged_after_refund_and_batch`, `storm_stage4_invariants` |
| R293 | pass | `refund_requires_idempotency_key`, `refund_replay_reuse_exactly_once`, `refund_insufficient_funds_available`, `batch_auth_and_idempotency`, `concurrent_same_key_refund_replay`, `ten_idempotent_write_paths` |
| R294 | pass | `refund_requires_idempotency_key` |
| R295 | pass | `refund_auth_and_lookup_errors` |
| R296 | pass | `refund_auth_and_lookup_errors` |
| R297 | pass | `refund_target_kinds` |
| R298 | pass | `refund_target_kinds` |
| R299 | pass | `refund_amount_validation` |
| R300 | pass | `refund_cumulative_limit`, `correction_below_refunded_rejected`, `storm_stage4_invariants` |
| R301 | pass | `refund_creates_opposite_payment` |
| R302 | pass | `refund_creates_opposite_payment`, `refund_replay_reuse_exactly_once`, `concurrent_same_key_refund_replay` |
| R303 | pass | `refund_insufficient_funds_available`, `batch_combined_affordability`, `storm_stage4_invariants` |
| R304 | pass | `refund_never_reopens_request_or_authorization` |
| R305 | pass | `refund_creates_opposite_payment` |
| R306 | pass | `correction_refund_linked_immutable` |
| R307 | pass | `refund_of_settlement_member`, `correction_refund_linked_immutable` |
| R308 | pass | `correction_below_refunded_rejected` |
| R309 | pass | `correction_debits_available_funds` |
| R310 | pass | `batch_auth_and_idempotency`, `batch_precedence_chain` |
| R311 | pass | `batch_shape_validation` |
| R312 | pass | `batch_shape_validation`, `batch_boundary_one_and_thirty_two` |
| R313 | pass | `batch_per_item_errors_input_order` |
| R314 | pass | `batch_per_item_errors_input_order` |
| R315 | pass | `batch_per_item_errors_input_order`, `batch_settlement_completeness_and_instants` |
| R316 | pass | `batch_settlement_completeness_and_instants`, `batch_precedence_chain` |
| R317 | pass | `batch_settlement_completeness_and_instants` |
| R318 | pass | `batch_settlement_completeness_and_instants` |
| R319 | pass | `batch_shape_validation` |
| R320 | pass | `batch_precedence_chain`, `batch_historical_overdraft_all_or_nothing` |
| R321 | pass | `batch_precedence_chain`, `batch_historical_overdraft_all_or_nothing` |
| R322 | pass | `batch_combined_affordability` |
| R323 | pass | `batch_historical_overdraft_all_or_nothing`, `batch_replay_and_rejection_idempotency`, `storm_stage4_invariants` |
| R324 | pass | `batch_success_shape_shared_recorded_at` |
| R325 | pass | `batch_success_shape_shared_recorded_at` |
| R326 | pass | `batch_per_item_errors_input_order`, `batch_success_shape_shared_recorded_at` |
| R327 | pass | `originals_unchanged_after_refund_and_batch` |
| R328 | pass | `originals_unchanged_after_refund_and_batch` |
| R329 | pass | `originals_unchanged_after_refund_and_batch` |
| R330 | pass | `batch_replay_and_rejection_idempotency` |
| R331 | pass | `ten_idempotent_write_paths` |
| R332 | pass | `refund_of_settlement_member` |
| R333 | pass | `concurrent_shared_revision_at_most_one`, `storm_stage4_invariants` |
| R334 | pass | `export_import_v4_roundtrip` |
| A21 | pass | `refund_auth_and_lookup_errors`, `refund_insufficient_funds_available` |
| A22 | pass | `refund_cumulative_limit` |
| A23 | pass | `refund_target_kinds`, `refund_never_reopens_request_or_authorization` |
| A24 | pass | `correction_refund_linked_immutable` |
| A25 | pass | `correction_below_refunded_rejected` |
| A26 | pass | `batch_auth_and_idempotency`, `batch_per_item_errors_input_order`, `batch_precedence_chain`, `batch_combined_affordability` |
| A27 | pass | `batch_success_shape_shared_recorded_at` |
| A28 | pass | `export_import_v4_roundtrip` |
| design-29 | pass | `export_import_v4_roundtrip` |

## Invariant evidence (money invariants, stressed independently of the acceptance suite)

Reviewer stress probe (`specs/stage-4/probes/stress_stage4_invariants.py`, committed at
`3f75856`), run three times against the same resource-constrained container
(`--cpus=2 --memory=2g`) used for the acceptance suite above — all three runs, every
check passing, varying race outcomes each time:

- **Stage-1 rounding table (R82, carried)**: `POST /splits` against the full table —
  1000/3→334,333,333; 1/3→1,0,0; 10/3→4,3,3; 999/3→333,333,333; 5/5→1,1,1,1,1 — exact
  largest-remainder shares, every entry.
- **Exactly once / idempotency key**: 10 concurrent identical-key refunds → exactly one
  201, nine 200-replays, all naming the same payment (R302/R3); 10 concurrent
  identical-key correction-batches → exactly one 201, nine 200-replays, all naming the
  same `correction_batch_id` (R330/R3).
- **Exactly once / conflict resolution (R333)**: 20 concurrent single-corrections sharing
  one `expected_revision` → exactly one 201, nineteen 409 `stale_revision`; the same for
  20 concurrent single/batch-mixed and 20 concurrent batch/batch requests sharing one
  `expected_revision` — exactly one commit in every combination, no partial writes.
- **All-or-nothing (R323)**: a correction-batch item that fails the historical-overdraft
  phase (construction verified to actually reject: 409/422 both runs) leaves every
  targeted payment's revision count and every party's balance byte-identical to before.
- **Conservation under a mixed storm (R1/R2/R45/R240/R322)**: 50 concurrent, mixed
  refund/correction/batch requests against shared targets — no 5xx response anywhere in
  the storm, and the sum of every user's `total` equals the seeded total exactly,
  immediately after.
- **No negative total/available at historical boundaries (R237/R238/R284/R285/R303)**:
  swept across every user at the storm's boundary instants, well before and well after
  the entire scenario — `total` and `available` nonnegative at every point checked.

Also covered by the acceptance suite's own `storm_stage4_invariants` (R291, R1, R2, R3,
R45, R300, R303, R323, R333, R292) and `concurrent_*` tests, independently passing above.

## Hygiene

- Build file (`Dockerfile`) and run document (`RUN.md`) present in `stage-4/`.
- No nested `.git`, no symlinks under `stage-4/`.
- No dependency manifest (stdlib-only, confirmed by `Dockerfile`: `FROM
  python:3.12-slim`, no `pip install`); dependency vulnerability scan is therefore
  moot (informational: zero third-party dependencies).
- Secret scan (hardcoded keys/tokens/passwords) of `stage-4/src`: clean.
- No branching on test inputs, fixture identifiers or test names found anywhere in the
  reviewed diffs across all four stage-4 batches (T31-T35) or in a final sweep of
  `stage-4/src`.
- Minor, non-blocking, carried from stage 3: `stage-4/RUN.md`'s header and image tag
  still read "stage 3" (the same copy-paste leftover already flagged, one stage further
  along each carry-forward). Not a verification blocker.

## Open gaps

- The R209 same-second tie-break (payment id, not arrival order) — see the isolated
  check above. Pre-existing, already adjudicated at stage 3, not a stage-4 regression,
  and not fixable from our side (the implementation is correct per spec; the gap is in
  the judge's own sample check, and the stage-3 folder is frozen).
- `stage-4/RUN.md`'s stage-3 header carry-over (see Hygiene).

## Verdict

**Stage 4: pass.** All stated money invariants (conservation, no negative balances,
exactly-once, all-or-nothing, the stage-1 rounding table, no 5xx under concurrent load)
hold under the tester's acceptance suite and the reviewer's independent stress probe,
both run against the actual resource-constrained (2 vCPU / 2 GiB) container. Every
requirement id stage 4 states has passing evidence. The isolated harness check's
"highest contiguous stage: 2" reflects a pre-existing, already-accepted, environment-
timing gap in the judges' own stage-3 sample test (not a stage-4 or stage-3
implementation defect); stage 4 itself, and the stage-1/stage-2 upgrade chain into it,
pass clean on every run.
