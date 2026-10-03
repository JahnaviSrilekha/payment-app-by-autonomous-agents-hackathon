# Stage 3 verification

**Verified revision:** `d8c617d` (main, fast-forward only throughout)
**Verifier:** reviewer, from a fresh clone (`payment-app-work/verify-s3-clone`)
**Date:** 2026-10-03

## Isolated check (judges' mode: `--mode isolated`, no outbound network, 2 vCPU / 2 GiB)

Ran `python -m harness run --track pocketful --repo <fresh clone> --stage 3 --mode isolated`
three times from the fresh clone, each into a new `--out` directory:

| Run | stage 1 | stage 2 | stage 3 | stage 4 (overshoot) |
|---|---|---|---|---|
| `s3-final-1` | pass | pass | **fail** | fail |
| `s3-final-2` | pass | pass | pass | fail |
| `s3-final-3` | pass | pass | pass | fail |

Stage 4 correctly fails every run (the overshoot check: stage 3 must not implement
stage-4 features). Stage 1→2→3 upgrade-import chain passes every run.

The single stage-3 failure (run 1) was `test_a_statement_walks_the_balance_forward`,
expecting two rapid payments' statement entries in send order. Root-caused, not a
defect: both payments landed in the same wall-clock second (1-second `created_at`
granularity is a stated design decision), and the stage-3 spec's own text
(`pocketful/spec/stage-3.md` line 68, quoted verbatim in requirements.md R209)
mandates "ordered by `created_at` ascending, then payment `id` ascending for ties" —
a tie-break that does not correlate with arrival order since payment ids are opaque.
Reproduced directly against a live instance: two payments placed in the same second
sort by id, exactly as the two passing runs and the spec both confirm. This is a
timing-flaky gap in the shipped sample check, not in the implementation, which
follows the spec's explicit tie-break rule. Logs: `checks/s3-final-*/stage-3.log`.

## Acceptance suite (tester's `specs/stage-3/acceptance`, 42 black-box tests)

Run against a `docker build --no-cache` image, started per `stage-3/RUN.md`
(`docker run --cpus=2 --memory=2g -e PORT=8080 -p 8120:8080 ...`):

**41 passed, 0 failed, 1 skipped** (`import_real_stage2_export`, R273/R274 — skipped
only because no `--stage2-url` was supplied; R273/R274 have other passing coverage).

## Requirement coverage

Every requirement id the stage-3 requirements document states (R193-R290, A20,
design-19) plus every carried invariant id the suite re-exercises (R1, R2, R3, R45,
R59, R61, R63, R99) is referenced by at least one acceptance test, and every one of
those tests passed on this revision.

| Requirement | Status | Covering test(s) |
|---|---|---|
| A20 | pass | `capture_immutable_and_import_versions` |
| R1 | pass | `storm_stage3_invariants` |
| R2 | pass | `concurrent_corrections_same_expected_revision`, `storm_stage3_invariants` |
| R3 | pass | `correction_replay_reuse_exactly_once`, `concurrent_same_key_correction_replay`, `storm_stage3_invariants` |
| R45 | pass | `storm_stage3_invariants` |
| R59 | pass | `corrections_auth_and_lookup_errors`, `correction_replay_reuse_exactly_once`, `correction_insufficient_funds_preserves` |
| R61 | pass | `concurrent_same_key_correction_replay` |
| R63 | pass | `corrections_auth_and_lookup_errors` |
| R99 | pass | `settlement_member_correction_rejected` |
| R193 | pass | `created_at_on_payment_responses` |
| R194 | pass | `created_at_on_payment_responses` |
| R195 | pass | `seeded_created_at_semantics` |
| R196 | pass | `seeded_future_created_at_rejected_atomically` |
| R197 | pass | `seeded_created_at_semantics` |
| R198 | pass | `me_as_of_validation` |
| R199 | pass | `me_as_of_validation` |
| R200 | pass | `me_without_temporal_params_current` |
| R201 | pass | `me_as_of_semantics` |
| R202 | pass | `me_as_of_semantics` |
| R203 | pass | `me_as_of_semantics` |
| R204 | pass | `me_as_of_semantics` |
| R205 | pass | `statement_defaults_shape_window`, `statement_window_half_open` |
| R206 | pass | `statement_defaults_shape_window`, `statement_pagination_full_window` |
| R207 | pass | `statement_defaults_shape_window`, `statement_window_half_open` |
| R208 | pass | `statement_defaults_shape_window` |
| R209 | pass | `statement_defaults_shape_window`, `statement_ordering_ties_by_id` |
| R210 | pass | `statement_defaults_shape_window`, `statement_window_half_open` |
| R211 | pass | `statement_defaults_shape_window`, `statement_pagination_full_window` |
| R212 | pass | `statement_pagination_full_window` |
| R213 | pass | `statement_visibility_own_only` |
| R214 | pass | `statement_entry_revision_fields_uncorrected`, `correction_success_shape_revisions` |
| R215 | pass | `seeded_created_at_semantics` |
| R216 | pass | `me_as_of_semantics`, `correction_moves_money_conserves`, `new_accounts_open_at_zero` |
| R217 | pass | `new_accounts_open_at_zero` |
| R218 | pass | `seeded_future_created_at_rejected_atomically` |
| R219 | pass | `corrections_auth_and_lookup_errors` |
| R220 | pass | `corrections_auth_and_lookup_errors` |
| R221 | pass | `corrections_auth_and_lookup_errors` |
| R222 | pass | `corrections_body_validation` |
| R223 | pass | `corrections_body_validation` |
| R224 | pass | `corrections_body_validation`, `correction_zero_amount_reversal` |
| R225 | pass | `corrections_body_validation` |
| R226 | pass | `corrections_body_validation` |
| R227 | pass | `corrections_body_validation` |
| R228 | pass | `correction_preserves_parties_visibility_activity` |
| R229 | pass | `correction_success_shape_revisions` |
| R230 | pass | `correction_success_shape_revisions` |
| R231 | pass | `correction_stale_revision`, `concurrent_corrections_same_expected_revision` |
| R232 | pass | `correction_stale_revision`, `correction_replay_reuse_exactly_once`, `concurrent_same_key_correction_replay` |
| R233 | pass | `correction_replay_reuse_exactly_once` |
| R234 | pass | `correction_moves_money_conserves` |
| R235 | pass | `correction_moves_money_conserves` |
| R236 | pass | `correction_insufficient_funds_preserves`, `correction_vs_holds_overdraft_precedence` |
| R237 | pass | `correction_historical_overdraft` |
| R238 | pass | `correction_historical_overdraft` |
| R239 | pass | `correction_insufficient_funds_preserves`, `correction_historical_overdraft`, `storm_stage3_invariants` |
| R240 | pass | `correction_moves_money_conserves`, `storm_stage3_invariants` |
| R241 | pass | `correction_replay_reuse_exactly_once` |
| R242 | pass | `correction_preserves_parties_visibility_activity` |
| R243 | pass | `correction_success_shape_revisions`, `revisions_endpoint_access` |
| R244 | pass | `revisions_endpoint_access` |
| R245 | pass | `corrections_auth_and_lookup_errors`, `revisions_endpoint_access` |
| R246 | pass | `known_at_validation_and_echo` |
| R247 | pass | `known_at_selection_semantics` |
| R248 | pass | `known_at_selection_semantics` |
| R249 | pass | `known_at_selection_semantics` |
| R250 | pass | `statement_known_at_window_and_combo` |
| R251 | pass | `me_as_of_semantics`, `statement_known_at_window_and_combo` |
| R252 | pass | `me_as_of_validation`, `known_at_validation_and_echo` |
| R253 | pass | `known_at_validation_and_echo` |
| R254 | pass | `known_at_selection_semantics` |
| R255 | pass | `statement_known_at_window_and_combo` |
| R256 | pass | `known_at_selection_semantics` |
| R257 | pass | `correction_zero_amount_reversal` |
| R258 | pass | `statement_known_at_window_and_combo` |
| R259 | pass | `me_without_temporal_params_current`, `statement_entry_revision_fields_uncorrected` |
| R260 | pass | `snapshot_freeze_across_changes` |
| R261 | pass | `snapshot_freeze_across_changes` |
| R262 | pass | `snapshot_freeze_across_changes` |
| R263 | pass | `snapshot_param_rules` |
| R264 | pass | `snapshot_param_rules` |
| R265 | pass | `snapshot_freeze_across_changes` |
| R266 | pass | `statement_pagination_full_window` |
| R267 | pass | `snapshot_param_rules` |
| R268 | pass | `correction_moves_payment_across_window` |
| R269 | pass | `concurrent_corrections_same_expected_revision`, `storm_stage3_invariants` |
| R270 | pass | `settlement_member_correction_rejected` |
| R271 | pass | `settlement_member_correction_rejected` |
| R272 | pass | `settlement_member_correction_rejected` |
| R273 | pass | `capture_immutable_and_import_versions`, `import_real_stage2_export` |
| R274 | pass | `capture_immutable_and_import_versions`, `import_real_stage2_export` |
| R275 | pass | `capture_immutable_and_import_versions` |
| R276 | pass | `me_historical_holds_lifecycle`, `historical_total_follows_revisions` |
| R277 | pass | `me_historical_holds_lifecycle` |
| R278 | pass | `me_historical_holds_lifecycle` |
| R279 | pass | `me_historical_holds_lifecycle` |
| R280 | pass | `me_historical_holds_lifecycle` |
| R281 | pass | `me_historical_holds_lifecycle` |
| R282 | pass | `me_historical_holds_lifecycle` |
| R283 | pass | `historical_total_follows_revisions` |
| R284 | pass | `correction_vs_holds_overdraft_precedence` |
| R285 | pass | `correction_insufficient_funds_preserves`, `correction_vs_holds_overdraft_precedence` |
| R286 | pass | `seeded_holds_assumed_creation` |
| R287 | pass | `seeded_holds_assumed_creation` |
| R288 | pass | `statement_only_money_movements` |
| R289 | pass | `statement_only_money_movements` |
| R290 | pass | `snapshot_freeze_across_changes`, `correction_moves_payment_across_window` |
| design-19 | pass | `correction_noop_same_amount` |

## Invariant evidence (money invariants, stressed independently of the acceptance suite)

Reviewer stress probe (`specs/stage-3/probes/stress_corrections_snapshots_holds.py`,
committed at `004900b`), run three times against the same resource-constrained
container (`--cpus=2 --memory=2g`) used for the acceptance suite above — all three
runs, 5/5 checks, varying race outcomes each time (ruling out a probe that only ever
exercises one code path):

- **Exactly once / conflict resolution**: 20 concurrent corrections sharing one
  `expected_revision` → exactly one 201, the other 19 are 409 `stale_revision`
  (R231, R239).
- **Exactly once / idempotency**: 25 concurrent requests sharing one idempotency key
  → exactly one commit, with the stage-1 200-replay/201-first-use contract (R3,
  R59, R61, R232) and byte-identical bodies.
- **Conservation under a lifecycle race**: concurrent capture + void attempts on one
  authorization → the authorized amount is conserved exactly (captured + released
  == 500) regardless of which side wins, `held`/`available` never negative,
  `available == total - held` throughout (R234, R239, R276, R277).
- **Snapshot immutability (R290)**: a statement snapshot read concurrently through a
  correction storm returns byte-identical `opening_balance`/`entries`/
  `closing_balance` on every read, during and after the storm.
- **Global conservation (R1, R2, R45)**: a 60-operation concurrent
  payment/authorize storm across 4 users leaves the sum of every `total` exactly
  equal to the seeded total, every run.

Also covered by the acceptance suite's own `storm_stage3_invariants` (R1, R2, R3,
R45, R239, R240, R269) and `concurrent_*` tests, independently passing above.

## Hygiene

- Build file (`Dockerfile`) and run document (`RUN.md`) present in `stage-3/`.
- No nested `.git`, no symlinks under `stage-3/`.
- No dependency manifest (stdlib-only, confirmed by `Dockerfile`: `FROM
  python:3.12-slim`, no `pip install`); dependency vulnerability scan is therefore
  moot (informational: zero third-party dependencies).
- Secret scan (hardcoded keys/tokens/passwords) of `stage-3/src`: clean.
- No branching on test inputs, fixture identifiers or test names found anywhere in
  the reviewed diffs across all five stage-3 batches (T23-T29).
- Minor, non-blocking: `stage-3/RUN.md`'s header and image tag still read "stage 1"
  (copy-pasted from stage 1, functionally harmless — the build/run commands are
  generic). Not a verification blocker; flagged for documentation polish only.

## Open gaps

- The R209 tie-break (payment id, not arrival order) means two statement entries
  whose selected `effective_at` lands in the same second sort unpredictably from a
  real client's point of view. This is exactly what the spec text mandates, not an
  implementation defect, but is worth the product owner's awareness if sub-second
  `created_at` precision is ever revisited.
- `stage-3/RUN.md`'s stage-1 header carry-over (see Hygiene).

## Verdict

**Stage 3: pass.** Highest contiguous stage: 3. All stated money invariants
(conservation, no negative balances, exactly-once, all-or-nothing, rounding, no 5xx
under concurrent load) hold under the tester's acceptance suite and the reviewer's
independent stress probe, both run against the actual resource-constrained
(2 vCPU / 2 GiB) container. Every stated requirement id has passing evidence.
