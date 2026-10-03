# ADR-009: correction batches share one trial-append/undo mechanism with single corrections

## Context

`POST /correction-batches` must validate up to 32 corrections, check combined affordability
across all of them at once (R322), and commit all-or-nothing (R323) — multiplying
stage-3's single-correction historical-overdraft sweep (`corrections._sweep_historical_overdraft`,
tentative-append-then-pop-on-failure) across many payments and many tentative revisions at
once.

## Options

1. **Reuse the tentative-append/pop-on-failure technique from `create_correction`, scaled up**:
   append every item's tentative revision to its payment under the lock, run one combined
   sweep over the union of affected parties/instants, pop everything on failure.
2. **Build a separate pure simulation** that computes the batch's effect on a deep-copied
   state without touching the real `payments`/`revisions` lists, check it, then replay the
   accepted mutations onto the real state.

## Decision

Option 1. The existing single-correction code already proves the tentative-append pattern
is correct and cheap at this scale (append is O(1), the sweep is O(payments × boundaries)
either way), and reusing it means `corrections.py`'s per-item validation
(`validate_item`, §27/§28) and sweep logic (`_sweep_historical_overdraft`, generalized to a
set of parties/instants instead of one payment's two parties) are the **only** places this
logic exists — a batch of size 1 behaves identically to a single correction by construction,
which is exactly what R318 ("ordinary single-payment corrections remain available for
nonmembers") requires to stay true without a second implementation.

A full deep-copy simulation (option 2) would need its own affordability/sweep code (or a
state-diffing layer to translate simulated deltas back into real mutations), doubling the
surface that could silently diverge from the single-correction path stage 3 already shipped
and the reviewer already stress-tested.

## Consequences

- The batch handler must be careful that phase 2's per-item validation (stale-revision,
  linked-immutable, refund-exceeds) runs against the *not-yet-mutated* state, and that
  phase 6's trial append happens only after every item has independently passed phase 2-4,
  so a later item's failure never leaves an earlier item's tentative revision stranded.
- Popping on failure must happen in reverse append order (consistent with `list.pop()`
  semantics) so each payment's `revisions` list is restored to its exact pre-batch shape.
- The one shared `recorded_at` (A27) is computed from the pre-append state (each payment's
  own latest revision before any tentative append), not reread after phase 6's trial append,
  since by then every payment already carries its own tentative (not-yet-final) revision as
  its "latest" — reading it then would self-reference the uncommitted revision instead of
  the last real one.
