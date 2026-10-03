# Pocketful — Stage 4 report

**Verified revision:** `7a4b37844412c592e937ba3b255f6e44620c01bd` (code; stage verification
ran from a fresh clone at this revision — see `specs/stage-4/verification.md`, committed at
`ff2c84c`). A trailing cosmetic fix (RUN.md's carried "stage 3" header, flagged by the
verification itself) landed after at `b4a74698d5e122e1824bd818358affdc8783dcfb` — this is
the final revision for the stage and for the whole track, since stage 4 is the last stage.

This is the **last stage of the pocketful track**. Its close marks the track complete.

## Timeline (2026-10-03, all times CEST)

| Time | Event |
|---|---|
| 14:42 | Stage-4 handover read; room confirmed, all seats present |
| 14:51–14:52 | requirements.md/design.md/tasks.md (R291-R334, A21-A30, §§25-30, ADR-008/009) committed and merged (`b0639aa`); handoffs sent to developer, tester, reviewer |
| 15:04 | Tester's completeness review: one wording defect (R303) + two design-only notes |
| 15:08 | Amendment merged (`61913fe`) |
| ~15:17–15:2x | T31 (refunds) handed off, reviewed, merged (`f79ac44`) |
| ~15:3x | Acceptance suite (71 tests) merged (`676e06d`) |
| 15:42–16:00 | T32 (corrections extensions) handed off; A26 phase-2 order defect caught by developer, fixed (`ce7d542`→`51ff317`), merged (`2883edb`) |
| 16:17–16:39 | T33 (correction-batches) handed off, one reject-fix cycle (R325 `correction_batch_id` exposure), merged (`bca798f`) |
| ~17:1x | Tester's acceptance-suite fixes (two rounds) merged (`13eb106`) |
| 17:18 | T34+T35 (format_version 4, invariant/concurrency tests) handed off — final batch |
| 17:42–19:42 | Room quiet during reviewer's deep stress-testing pass (9 timekeeper ticks; status check sent at 19:43, confirmed active not blocked) |
| 19:4x | Reviewer isolated a genuine test-suite bug (not implementation) in the v1/v2/v3 downgrade helper, routed to tester |
| 19:4x | Tester's fix merged (`21fb077`) |
| 19:52 | Developer resubmitted T34+T35 after merging main in |
| ~20:0x | T34+T35 merged (`7a4b378`) — all five tasks (T31-T35) complete |
| 20:0x | Stage verification requested |
| 20:1x | **Stage verification: PASS** (`specs/stage-4/verification.md` @ `ff2c84c`) |
| 20:17–20:24 | RUN.md cosmetic fix requested, delivered, merged (`b4a7469`) |
| 20:24 | Close-out roll call sent to all three seats |
| 20:2x–20:26 | All three seats replied `clear`; no unmerged branches (`git branch --no-merged main` empty) |

Total elapsed: ~5h44m (14:42 → 20:26).

## Check results

- **Isolated harness check** (3 runs, judges' mode, 2 vCPU/2 GiB): stage 4 clean on all 3
  (5/5 sample tests); stage-1/2 upgrade chain into stage 4 clean on all 3 (147/147, 35/35).
  Stage 3 failed identically on all 3 runs with the *same, already-adjudicated* flake from
  stage-3's own verification (`test_a_statement_walks_the_balance_forward`, a same-second
  id-tie-break gap in the judge's sample check, not an implementation defect — R209 mandates
  id-order explicitly, and stage-3/ is frozen/unmodified). `highest_contiguous = 2` reflects
  this pre-existing, non-stage-4 gap; stage 4 itself and everything feeding into it pass.
  No overshoot line applies (stage 4 is the last stage).
- **Acceptance suite** (`specs/stage-4/acceptance`, 71 black-box tests, run twice against a
  `docker build --no-cache` image under `--cpus=2 --memory=2g`): **70 passed, 0 failed,
  1 skipped** (expected — no `--stage2-url` supplied), both runs.
- **Developer unit tests**: 425/425 (`python3 -m unittest discover tests`, `stage-4/`).
- **Reviewer's independent stress probe** (`specs/stage-4/probes/stress_stage4_invariants.py`,
  3 runs, same constrained container): stage-1 rounding table exact; exactly-once under
  concurrent identical-key refunds/batches; exactly-once conflict resolution under
  single/single, single/batch and batch/batch `expected_revision` races; all-or-nothing on a
  genuinely-rejected batch; conservation and no negative total/available (incl. historical
  boundaries) under a 50-in-flight mixed storm; zero 5xx throughout — all passing, all 3
  runs.

## Requirement coverage

Every id stage 4 states — R291-R334, A21-A28, design-29 — has at least one passing
acceptance test, explicitly tagged in `specs/stage-4/acceptance/COVERAGE.md`; none lack
coverage. A29 and A30 are not independently tagged there: A29 is an interpretive reading of
R334 (exercised by that id's own tests, `export_import_v4_roundtrip` and
`originals_unchanged_after_refund_and_batch`), and A30 is a scope-limiting "no endpoint
required" statement with no positive behaviour of its own to tag — both are sound, neither
is an untested requirement (reviewer's precision note on this report, recorded here rather
than reopened as a fix cycle). Stage 1-3's
own ids (R1-R290) remain independently verified at their own stage gates and are
additionally re-exercised in stage-4 conditions (storm/concurrent/refund-and-batch
interactions) by `storm_stage3_invariants`, `storm_stage4_invariants` and the
`concurrent_*` suite. Full id-by-id table in `specs/stage-4/verification.md`.

## Assumptions

A21-A30, recorded in `specs/stage-4/requirements.md`. Two were amended during the stage,
both before any code depended on the wrong reading:

- **A26** (batch error-precedence phase 2): an earlier draft listed "field shape before
  payment lookup"; corrected to "lookup before field shape" to match design.md §28's
  explicit order and A15's established real-code precedent, after the developer caught the
  contradiction while implementing T32. No code was ever built against the wrong order.
- **A28** (format_version 4): expanded, not changed in substance, to explicitly name the
  format-4 round trip and the two design-29 import validations as testable consequences —
  prompted by the tester's completeness review tagging them `design-29` without an explicit
  home in the doc.

One design.md wording fix (not a numbered assumption): §27 said `refunded_total` lives in
`src/refunds.py`; corrected to `src/ledger.py` to match `tasks.md`'s T31 (the module-cycle-
avoiding choice both the original handoff and the developer actually used).

## Rejections and what they changed

- **T32 batch, reject-fix cycle**: none at the batch-review level (T32 merged clean); the
  A26 doc defect above was caught and fixed alongside, not as a code rejection.
- **T33, one reject-fix cycle**: R325 required every new revision to expose
  `correction_batch_id`, including through `GET /payments/{id}/revisions` and the
  single-correction response. The first submission missed this on those two paths (a
  manual patch had only updated the batch's own response construction). Developer fixed by
  routing both paths through the shared `correction_response`/`revision_entry` helpers
  instead of a parallel manual shape, so the field can't drift out of sync again; added a
  regression test; reviewer verified and merged (`bca798f`).
- **Acceptance suite, two reject-adjacent fix cycles** (not code rejections — tester's own
  suite, found during reviewer's independent verification): (1) party-scoped call bugs in
  several probes (calling an endpoint as the wrong party) and a batch historical-overdraft
  fixture that wasn't actually testing a negative boundary; (2) the `downgrade()` test
  helper stripped the `revisions` field from format-3 fixtures too, when only formats 1-2
  should lose it (format 3's defining feature per A20 is carrying the full `revisions`
  array) — reviewer isolated this by constructing a correct v3 downgrade by hand and
  confirming it imports cleanly, proving the implementation was right and the test wrong.
  Both rounds fixed by tester, re-verified by reviewer, merged (`b0928a2`/`21fb077`).
- **One latent developer-side defect, caught by the developer itself**: the T33 batch's
  R325 regression test sat at module level (never collected by `unittest discover`) —
  found and fixed during T35's work; the full-suite count only reflects it running from
  that point on.

No rejection changed a requirement, an invariant, or anything already accepted from an
earlier stage.

## Open gaps

- **R209 same-second tie-break** (judge sample check only): pre-existing, already
  adjudicated at stage 3 (`specs/stage-3/verification.md`), reproduced here 3/3 (vs. 1/3 at
  stage 3 — both consistent with a coin-flip on two random payment ids, not a regression).
  Not fixable from our side: the implementation is correct per the spec's own explicit
  tie-break rule (R209), and the stage-3 folder is frozen/accepted. Carries unresolved into
  the final deliverable since there is no stage 5 to fix it in — documented here as the
  track's one known, non-blocking gap.
- No other open gaps. The RUN.md cosmetic carry-over flagged in `verification.md` was fixed
  before this report (`b4a7469`).

## Stage-4 task list

All of T31-T35 complete (`specs/stage-4/tasks.md`, this commit marks every row `done`).
Five review batches, single dependency chain (documented reason: `refunded_total`,
`validate_item` and the format-4 bump are each shared prerequisites of every later task —
see `tasks.md`'s "Batch ordering note").

## Close-out

`git branch --no-merged main` empty — no seat branch anywhere holds unmerged work. Roll
call (20:24): developer, tester and reviewer all replied `clear`. Every handoff in the room
has its answer.

---

This closes stage 4 and the pocketful track. The final track-level report follows in the
room.
