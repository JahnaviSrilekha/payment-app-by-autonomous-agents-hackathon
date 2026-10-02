# Pocketful — Stage 1 report

**Verified revision: `76aa05f79ac1b9c9b4f0dacc0199584962554d98`** (code + tests + acceptance
suite). Permanent record (requirements/design/ADRs/tasks/verification) merged into main at
`ad81713014a88038383b00021e547bd794cd7c97`. All dates 2026-10-02, CEST.

## Check results

- Isolated-mode harness (`python -m harness run --track pocketful --stage 1 --mode
  isolated`), run independently by the reviewer against a fresh `--no-cache` build at
  `76aa05f`: stage 1 **147/147 pass**, stage 2 **fail** (no overshoot), `claimed stage: 1`.
- Full black-box acceptance suite (`specs/stage-1/acceptance/`, 83 tests, tester-authored,
  merged): **83 passed, 0 failed, 0 skipped**, run independently against a freshly built
  container by both the tester and the reviewer.
- Developer unit tests (stdlib `unittest`, in-process real HTTP server + threaded
  concurrency drivers): 124/124 passing on the final merged revision.

## Requirement coverage

R1–R103 (101 requirements from the original spec read plus R102/R103 added after the
tester's completeness review) all have passing evidence. Full per-requirement table with
test names: `specs/stage-1/verification.md`. No requirement is uncovered or marked
fail/skip.

## Assumptions (requirements.md)

A1 (Pebble branding has no stage-1 UI surface), A2 (settlement entry-error precedence read
as a full per-entry validation pass before the collective affordability check), A3 (id
format: typed prefix + `secrets.token_hex(8)`), A4 (no stated `display_name` validation
rule, any string incl. empty accepted), A5 (settlement transfer defaults/validation mirror
`POST /payments`), A6 (401 unauthenticated precedes 400 missing_idempotency_key — added
after tester completeness review), A7 (over-255-char idempotency key → 422
validation_failed, never claims a record — added after tester completeness review). None
were contested by the developer, tester or reviewer during the stage.

## Rejections and what they changed

1. **Design-doc correction** (tester completeness review, before any code was written):
   `design.md` §5 step 1 validated per-field JSON types *before* lock acquisition and
   idempotency-key resolution, contradicting R63 ("already claimed key resolved before
   endpoint field validation"). Fixed in commit `0420629`: step 1 now only rejects
   unparseable/non-object bodies; all field-type checks moved to step 6, after key
   resolution. The developer confirmed (before and after this fix landed) that the actual
   implementation already matched the corrected version, so no code rework was needed —
   only the design document was wrong.
2. **Batch 3 (T7, splits) resubmission**: reviewer rejected the first submission for
   validating `note` length *after* the unknown-handle 404 check in `create_split`,
   inconsistent with `create_payment`/`create_request`'s order and `design.md` §9 step 4-5.
   Developer fixed (commit `a937234`, added a combined-case unit test), resubmitted, and the
   reviewer accepted and merged (`e884873`).
3. **R79/R41 defect found during stage verification** (not a batch rejection — found by the
   reviewer's own acceptance-suite run after batch 4 merged): `participant_handles` given a
   non-array value or explicit `null` returned `400 malformed_request` instead of `422
   validation_failed`, even though it is a field with endpoint-specific rules (R79) and so
   should take the 422 path per R41. The reviewer routed the fix directly to the developer
   with a full repro; developer fixed `stage-1/src/splits.py` (commit `76aa05f`, both the
   non-array and explicit-null branches now raise `validation_failed`, matching
   `get_note`'s null-handling precedent), added unit tests for the string/null/missing
   cases, and the reviewer re-verified the full acceptance suite (83/83) and the harness
   isolated-mode run against the fix before declaring stage 1 verified.
4. **Process gap, not a rejection**: the coordinator's `coordinator-s1-docs` branch
   (requirements.md/design.md/ADRs/tasks.md) was referenced by commit hash throughout the
   stage in every handoff but was never itself merged into main. Caught during coordinator
   close-out (before the roll call), the reviewer merged it (`ad81713`) so the permanent
   record lives on main alongside the code.

## Invariant evidence

- **Conservation (R1)**: acceptance suite `r1_fuzz_mixed_operations_invariants`,
  `r84_splits_paid_conserve`; reviewer's independent 50-op mixed concurrent stress probe
  (`specs/stage-1/probes/probe_stage1_verify_stress.py`) — total balance conserved
  throughout.
- **No negative balance, ever (R2)**: acceptance suite `r2_concurrent_debits_never_negative`,
  `r67_payment_atomic_no_trace_on_failure`; reviewer's stress probe and dedicated 50-way
  same-key race — no negative balance observed under either.
- **Exactly-once (R3)**: acceptance suite `r61_concurrent_same_key_exactly_one_201`,
  `r61_concurrent_pay_same_key_once`, `r61_concurrent_settlement_same_key`; reviewer's
  dedicated 50-thread identical-key race on a single endpoint — exactly one `201`, 49
  identical `200` replays, balance moved exactly once.
- **All-or-nothing settlements (R98)**: acceptance suite
  `r97_collective_not_sequential`, `r98_settlement_all_or_nothing`; reviewer's stress probe
  confirms every committed settlement's member payments share one `settlement_id` with
  `created_at == committed_at`, and failed settlements leave no partial trace.
- **Rounding (R82–R84)**: acceptance suite `r82_rounding_table_exact` reproduces every row
  of the spec's table exactly (1000/3→334,333,333; 1/3→1,0,0; 10/3→4,3,3; 999/3→333,333,333;
  5/5→1,1,1,1,1); reviewer's stress probe confirms shares always sum exactly to the split
  amount under concurrent load.
- **No 5xx ever (R45)**: acceptance suite `r45_no_5xx_under_50_concurrent`; reviewer's 50-op
  mixed stress run and 50-way race — zero 5xx in both.

## Open gaps

- `coverage.md`'s own printed coverage table in the acceptance suite loops `range(1, 102)`,
  so R102/R103 never appear in that table's *printed* output even though their test
  (`r102_r103_no_out_of_scope_endpoints`) runs and passes as part of the 83. Cosmetic
  reporting gap in the suite's printer, noted by the reviewer in `verification.md`; not a
  test or product defect and does not block stage 1.
- Reviewer's cleanup note: while removing its own verification containers, a name-filtered
  `docker rm -f` also removed two pre-existing containers (`pocketful-demo` and one other)
  without first checking ownership. Both were plain rebuildable demo containers, not
  state-bearing, and unrelated to this result repository — flagged here for visibility only.

## Timeline

| Step | Start | End |
|---|---|---|
| Requirements/design/tasks drafted, committed (`99cf34f`) | 15:38 | 15:43 |
| Handoffs to developer and tester sent | 15:43 | 15:47 |
| Tester completeness review received; design.md fix + R102/R103/A6/A7 committed (`0420629`); follow-up sent | 15:47 | 15:54 |
| Batch 1 (T1,T2,T3,T4,T9) built, reviewed, merged (`0c9df94`) | 15:54 | ~16:28 |
| Acceptance suite built, iterated, merged (`6dc2d9b`→`8311895`) | ~16:28 | ~17:00 |
| Batch 2 (T5,T6) built, reviewed, merged (`2f47fc9`) | ~17:00 | ~17:31 |
| Batches 3 (T7) and 4 (T8) built in parallel, handed to reviewer | 17:00 (started) | 17:31 (handed off) |
| Batch 3 rejected once (note-length precedence), fixed, merged (`e884873`) | 17:31 | ~17:36–18:27 |
| Batch 4 merged (`bdd406a`) | 18:27 | ~18:50 |
| R79/R41 defect found at verification, fixed, merged (`76aa05f`) | ~18:50 | ~18:57 |
| Stage verification requested and completed: PASS (`verification.md`, `84be1d8`) | 18:57 | ~19:07 |
| Docs-merge gap found and fixed (`ad81713`) | 19:08 | 19:08 |
| Close-out roll call — all seats (developer, tester, reviewer) replied `clear` | 19:09 | 19:17 |

Total stage-1 duration: **15:38 → 19:17 CEST (~3h 39m)**.
