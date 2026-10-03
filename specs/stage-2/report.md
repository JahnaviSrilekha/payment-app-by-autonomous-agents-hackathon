# Pocketful — Stage 2 report

**Verified revision: `b1d3ad470c93d6fcba89e769c1a3b4dac637380e`** (code + tests + acceptance
suite — the revision the reviewer's `verification.md` names). Permanent record
(requirements/design/ADRs/tasks/this report) merged into main at
`f6ef8bce1b44e3bc79eaf48681bd4c666bca6c33`. All times CEST, 2026-10-02/03.

## Check results

- Isolated-mode harness (`python -m harness run --track pocketful --stage 2 --mode
  isolated`), run independently by the reviewer against a fresh build at `b1d3ad4`: stage 1
  **147/147 pass**, stage 2 **35/35 pass**, stage 3 **fail** (no overshoot), `claimed stage:
  2`, `highest_contiguous: 2`.
- Full black-box acceptance suite (`specs/stage-2/acceptance/`, 58 tests, tester-authored,
  merged): **58 passed, 0 failed, 0 skipped**, confirmed independently by both the tester
  and the reviewer against freshly-built containers at the verified revision (the reviewer's
  own run additionally re-ran the stage-1 suite directly against the stage-2 service: 82/83,
  the single difference being the expected `format_version: 2` export divergence, see
  Open gaps).
- Developer unit tests (stdlib `unittest`, in-process real HTTP server + Node-driven
  `app.js` logic tests + a cross-language rounding sweep pinning the JS split-preview port
  to the Python implementation): 271/271 passing on the final merged revision.

## Requirement coverage

R104–R192 (89 stage-2 requirements) plus stage-1's R1–R103, all still in force by
reference, have passing evidence. Full per-requirement table with test names:
`specs/stage-2/verification.md`. No requirement is uncovered or marked fail/skip. Every row
of `tasks.md` (T10–T22) is marked done with the batch and merge commit that closed it.

## Assumptions (requirements.md)

A8 (error-table row order is not precedence — canonical order instead mirrors the real,
verified `create_payment`/`pay_request` code paths in stage-1, since stage-1's own table
lists `insufficient_funds` first yet checks it last), A9 (authorizing a request is out of
scope — the two features never cross-reference each other), A10 (`GET /authorizations`
pagination defaults mirror `GET /requests` exactly), A11 (the UI's split-preview calls the
same pure rounding function the server uses, not a second implementation), A12 ("latest
refresh wins" via a client-side monotonic sequence number), A13 (`pay-uncertain` reserved
for a lost/no response, never a definite error). A8 was revised once mid-stage (see
Rejections #2); A9–A13 were never contested. One additional working assumption surfaced and
resolved during batch 4 (not separately numbered, folded into T17's done-test wording):
signup/login may use a native form POST with server-side redisplay on failure, since they
are not in the technical direction's "in-page script" list for pay/refresh/request/split
and carry no idempotency key or uncertain-state requirement.

## Rejections and what they changed

1. **Tester completeness-review fix, before any code was written** (commit `b0ada79`):
   R163/R175 cross-referenced assumption "A9," but the error-table-order reasoning is
   actually recorded as **A8** (A9 is the unrelated request/authorization-independence
   assumption). Fixed the mislabel. Purely a documentation cross-reference fix, no
   behavioural change.
2. **A8/R163/R175 precedence rewrite** (tester completeness review, commit `3b3d833`,
   before batch 3 was built): the original A8 assumed the spec's error-table *row order* was
   check-execution order. Checked against the one real precedent in the codebase —
   stage-1's verified `create_payment` (`stage-1/src/payments.py`) lists `insufficient_funds`
   as its first table row but actually checks it *last*, after every shape check and after
   `self_payment`/`not_found`, because an amount must be shape-valid before it can be
   compared to a balance. Rewrote A8 and R163/R175 to state the real precedent (mirroring
   `create_payment` field-for-field for `POST /authorizations`, and the id-path-resource
   pattern in `pay_request`/`decline_request`/`cancel_request` for the capture endpoint) and
   updated `design.md` §13 to match. Also fixed R114's endpoint names (`POST /signup` →
   `POST /auth/signup`, cosmetic). Developer had not yet started batch 3, so no code
   rework was needed — design.md already matched the corrected order almost exactly (one
   field-order tweak: note/visibility shape checks moved before self_payment/not_found).
   Later independently re-verified line-by-line against the actual stage-1 source by the
   reviewer before merging (commit `66aeb707`'s report).
3. **Signup/login reload design question** (developer, before batch 4 merge, no code
   rework): developer found that preserving the email field across both a failed login and
   a click-then-immediate-navigation race is only jointly satisfiable with a full form
   redisplay on failure, not an in-place JS patch. Checked against the spec: the technical
   direction's "must run from an in-page script" rule names only pay/refresh/request/split;
   signup/login were never in it and have no idempotency key or uncertain-state requirement.
   Approved the native-form approach as submitted; the defect was in my own `tasks.md` T17
   wording ("with no page reload"), which over-specified beyond R113/R114. Fixed (commit
   `b97d29d`, merged `f6ef8bc`).
4. **Batch 1→5 topology/rebase churn** (not rejections — routine, all resolved by the
   developer/reviewer pair without coordinator involvement): every batch after the first
   needed one or more rebases onto a moving `main` (reviewer's own probe/screenshot commits,
   tester's suite-fix commits) before a fast-forward merge was possible. All were disclosed
   transparently as "zero product-code drift" merges and verified as such by the reviewer
   before each fast-forward.
5. **Acceptance-suite bugs found during batch 3 and batch 5 development** (not product
   rejections — reported by the developer to the tester, fixed by the tester, all merged):
   batch 3 found 5 suite bugs (party-exclusivity expectation violating R180, an unfunded
   1e9-boundary fixture, a conservation-violating capture expectation, a wrong-payer test
   setup, an int-indexed string-keyed dict) — all fixed in tester commits `11124f8` and
   `a680e32`. Batch 5 found 2 more (incoming-hold-counted-against-receiver contradicting
   R144/R147, and an over-`available` authorize amount) — fixed in tester commit `5f2c2c5`.
   None were product defects; every fix was to the suite's own fixtures/expectations, each
   checked by hand against the cited requirement before being accepted as a suite bug
   rather than a product gap.
6. **T21 visual-review rejection** (reviewer, batch 5, one round): the reviewer's screenshot
   review of `/authorizations` found `authorize-error` and `authorization-error` both
   rendering inside the Wallet card instead of their own forms/cards (R109's "easy to scan"
   / R188/R190 consistency). Developer fixed via a `messageHost` anchor map so each error
   element anchors to its own direct-child control (commit `5faa5c1`), added regression
   tests pinning both anchors, and the reviewer re-verified and merged.
7. **Process gap, not a rejection** (caught during coordinator close-out, same pattern as
   stage 1): the `coordinator-s2-docs` branch's T17 wording-fix commit (#3 above) had only
   been described informationally in a room message, never handed to the reviewer as an
   actual merge request. Caught via `git branch --no-merged main` before the roll call;
   reviewer merged it (`f6ef8bc`) before close-out proceeded.

## Invariant evidence

- **Conservation of `total` (R144)**: acceptance suite's mixed-storm tests; reviewer's
  independent stress probe (`specs/stage-2/probes/probe_stage2_verify_stress.py`, 1144 HTTP
  requests) — total conserved throughout a 50-op mixed storm of every operation kind.
- **`available = total − held` never negative, including transiently (R145)**: reviewer's
  stress probe — 8 concurrent readers racing 100 partial captures, `available` never
  observed negative; batch-3 developer concurrency test and a dedicated reviewer probe at
  batch 3 (`d95ad15`) both targeted this specifically before batch 5 re-confirmed it.
- **Held funds unspendable by other operations (R145)**: reviewer's probe — 20 concurrent
  over-budget attempts across all four money-moving/holding paths (payment, request-pay,
  settlement, new authorization), none succeed beyond `available`.
- **Cumulative captures ≤ authorized; closed hold can't be captured again (R146)**:
  reviewer's probe — exactly 5 of 20 concurrent captures succeed against a 100.00 hold with
  `0100.00` as the authorized ceiling, cumulative never exceeding it; an 8-trial
  capture-vs-void race resolves to exactly one winner every time.
- **Exactly-once on all 7 idempotent write paths (R152 + stage-1's R3)**: acceptance suite's
  per-endpoint replay/concurrency tests; reviewer's probe confirms exactly-once across all 7
  paths under concurrent identical-key load.
- **Lazy expiry correctness (R159)**: acceptance suite and reviewer's probe both confirm an
  authorization past `expires_at` shows `expired` and its remainder is back in `available`
  with no action taken at the deadline.
- **Rounding (stage-1 R82–R84, still in force)**: reconfirmed via the stage-1 suite run
  directly against the stage-2 service (82/83) and the UI's cross-language split-preview
  sweep (JS port pinned to the Python `split_shares` over amounts 1–400 × 1–13 participants
  plus the stage-1 table).
- **No 5xx ever**: 0 observed across the reviewer's 1144-request stress probe and the full
  acceptance suite run.

## Open gaps

- **Expected export-format divergence**: running the stage-1 acceptance suite directly
  against the stage-2 service produces one expected failure (`r85_export_shape`) because
  stage-2 always exports `format_version: 2` by explicit design (`design.md` §15), not
  `format_version: 1`. Documented by the reviewer in `verification.md`; not a defect.
- **R112/R137 have no dedicated test**: both are permissions with no assertable behaviour
  ("a custom illustration ... is not required"; "no background polling ... is required") —
  documented as suite assumptions TA-1..TA-7 in the tester's README; nothing to cover.
- No other open defects. No branch left unmerged at close-out (`git branch --no-merged
  main` empty as of `f6ef8bc`). All three seats replied `clear` to the close-out roll call.

## Timeline

| Step | Start | End |
|---|---|---|
| Carry-forward, requirements/design/ADRs/tasks drafted, committed (`8acf008`→`8197bb5`) | 19:21 | 19:49 |
| Handoffs to developer and tester sent | 19:52 | 19:52 |
| Tester completeness review received; A9→A8 label fix (`b0ada79`) | 19:52 | 19:55 |
| A8/R163/R175 precedence contradiction found and adjudicated; design.md §13 + R114 fixed (`3b3d833`) | 20:23 | 20:25 |
| Docs branch merged into main (`e320937`), after a ~3h reviewer-availability gap required one resend | 20:29 (first sent) | 23:27 (merged) |
| Batch 1 (T10) and batch 2 (T16) built in parallel from stage start, reviewed, resubmitted after the docs-merge topology fix, merged (`50d0042`, `a7a584c`) | ~20:46 (first handoffs) | 23:53 |
| Batch 3 (T11–T15) built, reviewed, merged (`e1792db`) | ~00:00 | 00:53 |
| Signup/login reload design question raised, adjudicated, T17 wording fixed (`b97d29d`) | 03:13 | 03:16 |
| Batch 4 (T17–T20) built, reviewed (one visual-review round via screenshots, no rejection), merged (`53f1eca`) | ~01:00 | 03:38 |
| Batch 5 (T21–T22) built, one rejection (error-anchor placement, `5faa5c1`) fixed and re-verified, merged (`b1d3ad4`) | ~03:40 | 04:40 |
| Stage verification requested and completed: PASS (`verification.md`, `9f248fc`) | 04:46 | 04:55 |
| Docs-merge gap found (T17 fix never formally handed off) and closed (`f6ef8bc`) | 04:56 | 04:56 |
| Close-out roll call — all seats (developer, tester, reviewer) replied `clear` | 04:57 | 04:58 |
