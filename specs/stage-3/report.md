# Pocketful — Stage 3 report

**Verified revision: `d8c617d`** (code — the revision the reviewer's `verification.md`
names). Permanent record (requirements/design/ADRs/tasks/this report) merges into main on
top of the subsequent cosmetic fix, currently at `17252dd`. All times CEST, 2026-10-03.

## Check results

- Isolated-mode harness (`python -m harness run --track pocketful --stage 3 --mode
  isolated`), run by the reviewer from a fresh clone, three times: stage 1 **pass**, stage 2
  **pass** every run; stage 3 **pass** on 2 of 3 runs, the third a timing-flaky sample-check
  failure root-caused to the spec's own tie-break rule (two payments landing in the same
  wall-clock second sort by payment id per R209's literal text, not arrival order — not a
  defect, see `verification.md`); stage 4 (overshoot) correctly **fails** every run.
- Full black-box acceptance suite (`specs/stage-3/acceptance/`, 42 tests, tester-authored):
  **41 passed, 0 failed, 1 skipped** (`import_real_stage2_export`, skipped only for want of
  `--stage2-url`; R273/R274 have other passing coverage), run against a `docker build
  --no-cache` image under the real 2 vCPU/2 GiB constraint.
- Reviewer's independent stress probe (`specs/stage-3/probes/stress_corrections_snapshots_holds.py`),
  run 5× with varying race outcomes: exactly-once correction races, idempotency replay,
  capture/void conservation, snapshot immutability under a concurrent correction storm, and
  global conservation across a 60-op concurrent storm all held every run.
- Developer unit tests on the final merged revision: 377/377 (stdlib `unittest`, in-process
  real HTTP server).

## Requirement coverage

R193–R290 (98 stage-3 requirements) plus A14–A20, and stage-1's R1–R103/stage-2's R104–R192
(still in force by reference), all have passing evidence. Full per-requirement table with
covering test names: `specs/stage-3/verification.md`. No requirement is uncovered or marked
fail/skip. Every row of `tasks.md` (T23–T29) is marked done below with the batch and merge
commit that closed it.

## Assumptions (requirements.md)

A14 (statement's default `from` is an unbounded lower bound, same reference point as
`GET /me`'s pre-earliest-payment opening balance), A15 (error precedence for corrections and
snapshot statements, derived from stage-2's actual code — lookup → ownership → field
validation → business rules, all after idempotency-key resolution), A16 (one wall-clock read
per request for `to`/`as_of` defaults), A17 (payment-id tiebreak fully resolves `effective_at`
ties), A18 (overdraft boundary-checking only at instants where a payment or hold event
falls), A19 (snapshot tokens are opaque, server-held, not self-describing), A20 (stage-3
bumps export to `format_version: 3`, carrying `revisions`/`base_balance` as stored fields so
a self-export/import cycle preserves correction history; still accepts 1/2 by deriving them
fresh, as stage-2's own export did for version 1). None were contested during the
completeness review (the tester's first-pass review found full coverage with no gaps). A15
was refined once mid-stage: the correction-field wrong-JSON-type precedent (SA-8, see
Rejections #2) and the holds-view independent-AND-gate precedent (R279, see Rejections #3)
were both resolved by checking actual stage-1/2 code and the spec's own effective/known
framing respectively, not asserted from the spec table alone — the same "check the real
precedent" discipline stage-2's A8 rewrite established.

## Rejections and what they changed

1. **R218 nonnegative-opening-balance gap, found by reviewer during batch-1 review**
   (commit `eefc9c5`, before batch 1 was merged): the original reset validation checked each
   seeded payment's running balance but not a receiver-only seeded payment's implied
   negative opening balance on the very first replay iteration. Reviewer reproduced it
   (`bob` balance 2500, receives 5000, sends nothing → should be 422, state unchanged) and
   developer fixed it before the batch was ever formally rejected through the coordinator —
   caught and closed within the same review pass.
2. **SA-8 adjudication — wrong-JSON-type correction fields** (tester's suite, `19cae4a`;
   developer's fix, `51643a6`): tester's suite accepted either 400 or 422 for a wrong-type
   `reason`/`effective_at`/etc., flagging it as an R43-vs-R227 ambiguity. Resolved by citing
   stage-1 design.md §9 step 4's existing precedent (wrong type for a field with an
   endpoint-specific rule is always that endpoint's code, never generic 400) — not actually
   ambiguous once checked against the real rule. Found in the process: `reason` specifically
   fell through to the generic 400 handler (routed through `state.py`'s `get_string` instead
   of the endpoint's own type check); fixed alongside `effective_at`, same pattern.
3. **R279 historical-holds defect — `held_view`'s `(as_of, known_at)` gating** (found by
   tester's acceptance suite and reviewer's independent probe in the same window; developer's
   fix on `developer-s3-t24fix`, commits `ec6e023`+`51643a6`): a nonfinal capture's effect on
   `held` leaked before its event was actually known. Coordinator's initial adjudication
   (two hypotheses: inverted `known_at` comparison, missing `as_of` gate) correctly predicted
   the required output values (both independent AND-gates: `event_time <= as_of` AND
   `event_time <= known_at`, confirmed against the spec's effective/known framing) but not
   the exact root cause — the reviewer traced it precisely to a third term, the
   unrecorded-remainder/seeded-capture fallback, misdiagnosing an event-backed capture that
   `known_at` excluded as record-less and adding it back in, bypassing both gates. Developer's
   fix computes the unrecorded remainder against all recorded captures, isolating only
   genuinely record-less seeded captures; a four-cell regression table plus the disambiguating
   third probe (coordinator-requested: `as_of` after the event, `known_at` before it) now
   locks the behaviour in.
4. **Suite-only bugs, no implementation defect** (tester, committed directly, no coordinator
   routing needed beyond the initial SA-8/R279 adjudication): an RFC3339 `+`-in-query
   percent-encoding bug (`05ac541`+`653d75b`), a stage-2 suite probe asserting
   `format_version: 3` must be rejected — superseded by A20, bumped to probe version 4
   (`05ac541`), a family of five S(n) time-direction/helper bugs from a full self-audit
   (`e1d5fcc`), a stale module-load-time "future" instant going stale on long runs (`87cc55b`,
   SA-10), and a live `/authorizations` (no `as_of`) lifecycle-timing family the reviewer
   reported (`673b2e9`, SA-9).
5. **Cosmetic, flagged in stage verification, not a defect** (`17252dd`): `stage-3/RUN.md`'s
   header and image tag still read "stage 1" / `pocketful-stage1`, a copy-paste leftover
   surviving both carry-forwards. Fixed before close-out.

No requirement failed adjudication three times; nothing needed splitting.

## Invariant evidence

- **Conservation**: sum of all wallet balances equals the seeded total in every historical
  view (R240), proven by the acceptance suite's `storm_stage3_invariants` and the reviewer's
  60-op concurrent storm probe.
- **No negative money, ever, including historically**: `historical_overdraft`'s boundary
  sweep (R237-238, R284) and the live `insufficient_funds` check both independently
  verified; the reviewer's probe confirms `available` never negative under concurrent
  capture/void races.
- **Exactly once**: correction replay (`correction_replay_reuse_exactly_once`), 25 concurrent
  requests sharing one idempotency key (reviewer's probe, exactly one commit), 20 concurrent
  corrections racing one `expected_revision` (exactly one 201, rest `stale_revision`).
- **All or nothing**: `insufficient_funds`/`historical_overdraft` rejections preserve
  balances, revision history, statements and idempotency state unchanged (R239), verified
  by `correction_insufficient_funds_preserves` and equivalent historical-overdraft tests.
- **Rounding**: unchanged from stage 1, reconfirmed via regression (no new split logic in
  this stage).
- **Snapshot stability**: a statement snapshot read concurrently through a correction storm
  stays byte-identical throughout and after (R290), reviewer's probe item 4.
- **No 5xx ever**: 0 observed across the full acceptance suite and every stress probe run.

## Open gaps

- **Close-out roll call: tester has not replied.** Roll call sent 12:35, resent 13:04 (15 min,
  per the waiting rule) after tester's "running one final confirmation pass" update; no
  reply or new commits on `tester-s3` as of this report (over 2 hours later). Developer and
  reviewer both replied `clear`. No branch is left unmerged (`git branch --no-merged main`
  empty as of `17252dd`), the acceptance suite the tester authored is fully green and merged,
  and the reviewer's independent verification already covers everything the tester's own
  suite would re-confirm — so this is recorded as a documented gap in process (an
  unacknowledged roll call), not a sign of unfinished stage-3 work. If the tester's reply
  arrives later, no further action is expected to be needed.
- **Expected export-format note**: stage-3 exports always emit `format_version: 3` (A20); a
  stage-1/2-shaped import (`format_version` 1 or 2) is accepted by deriving `revisions`/
  `base_balance` fresh, exactly as reset would — not a defect, documented in design.md §23.
- No other open defects.

## Timeline

| Step | Start | End |
|---|---|---|
| Handover read, participants confirmed, stage-3.md read | 05:03 | 05:03 |
| Carry-forward (`84158ac`), requirements/design/ADRs/tasks drafted and committed (`fab817c`) | 05:03 | 05:15 |
| Handoffs to reviewer, tester, developer sent (parallel) | 05:16 | 05:17 |
| Docs merged into main (`fab817c`, fast-forward) | ~05:19 | ~05:19 |
| Tester completeness review: complete, no gaps | ~05:17 | ~05:30 |
| Batch 1 (T23) + batch 2 (T24) built in parallel from stage start, handed off | ~05:17 | 06:16 |
| Reviewer independent verification, R218 fix (`eefc9c5`), batch 1+2 merged (`d1249c3`) | 06:16 | ~07:10 |
| Batch 3 (T25), batch 4 (T26/T27, after one export-crash rejection/resubmit), batch 5 (T28/T29) built and handed off | ~07:10 | 07:45 |
| Reviewer deep independent verification (quiet period; coordinator follow-up 09:34) | 07:52 | 10:43 |
| All three batches content-approved, stage-3 fully merged (`42cbf02`), reviewer's stress probe committed (`004900b`) | 10:43 | ~10:50 |
| Tester's consolidated suite fix + candidate R279 defect reported (`e1d5fcc`); SA-8/R279 adjudicated | ~10:50 | 11:07 |
| Reviewer independently confirms R279, precise root cause; developer fixes R279+SA-8 (`ec6e023`, `51643a6`) | 11:1x | 11:24 |
| Tester: third R279 probe + SA-8 tightening (`19cae4a`), stale-timestamp fix (`87cc55b`), SA-9 fix (`673b2e9`) | 11:0x | ~11:55 |
| Reviewer merges all fixes; stage-3 verification: **PASS** (`92c3a8a`) | ~11:55 | ~12:05 |
| RUN.md cosmetic fix routed, built, merged (`17252dd`) | 12:12 | ~12:28 |
| Close-out roll call sent; developer and reviewer reply `clear`; tester resend at 13:04, no reply as of this report | 12:35 | 14:35+ |
