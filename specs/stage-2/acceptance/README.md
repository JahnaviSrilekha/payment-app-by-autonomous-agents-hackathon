# Pocketful — stage 2 acceptance suite (black-box)

Tests every stage-2 requirement (R104–R192 of `specs/stage-2/requirements.md`,
from `we-are-devs/pocketful/spec/stage-2.md`) plus the carried-forward stage-1
invariants, against a running service, through its external interfaces only.
No product code is read or imported.

## Run

```sh
# API + invariants + export/import + browser UI (playwright needed for UI)
python3 run.py --base-url http://127.0.0.1:8080

# API only
python3 run.py --base-url http://127.0.0.1:8080 --skip-ui

# Cross-check a real stage-1 export (R138) with a live stage-1 instance
python3 run.py --base-url http://127.0.0.1:8080 --stage1-url http://127.0.0.1:8130

# List tests / emit the requirement coverage table (markdown)
python3 run.py --base-url http://x --list [--md]
```

Requirements: Python 3.10+. The UI suite additionally needs Playwright + Chromium:

    pip install playwright && playwright install chromium

Without Playwright the UI tests are reported as SKIP; exit code is 0 iff no test
FAILED. Every test resets the service with its own fixture first
(`POST /_test/reset`), so the suite is order-independent and tolerates state
carried over from earlier stages.

## What is covered

- Stage-1 invariants still hold under stage-2: conservation of `total`, no
  negative balance, exactly-once idempotency, all-or-nothing settlements, the
  exact stage-1 rounding table, no 5xx — exercised under 50-thread mixed
  storms that also run authorizations/captures/voids concurrently
  (`storm_mixed_concurrent`, `concurrent_authorizations_same_headroom`,
  `concurrent_captures_and_void_race`).
- New stage-2 invariants: `available = total − held` never negative including
  transiently (concurrent readers during write storms), held funds unspendable
  by payments/request-pay/settlements/new authorizations, cumulative captures
  ≤ authorized under concurrent capture attempts, closed holds never
  capturable again (including capture-vs-void and capture-vs-expiry races),
  each idempotent capture moving money exactly once.
- Lazy expiry: past-deadline authorizations read as `expired` with the
  remainder back in `available`, with no action taken at the deadline
  (`seeded_expired_lazy_read`, `lazy_expiry_created_auth`, status filters).
- Idempotency on `POST /authorizations` and `POST /authorizations/{id}/capture`:
  replay-same-body → 200 identical, same-key-different-body → 409
  `idempotency_key_reuse` (including `{}` vs an equal-value explicit `amount`
  and `final` present/absent), concurrent identical creates/captures on an
  unused key → exactly one 201.
- Export/import: a synthesized stage-1-shaped `format_version: 1` export
  imports with defaulted ttl and empty authorizations; with `--stage1-url`,
  a real stage-1 service's export is imported too; a pre-import bearer token
  and a lost-response payment retry survive an export/import cycle; pending
  requests stay payable; import validates and replaces; reset clears.
- Browser UI (Playwright, driven by `data-testid` on the real rendered pages):
  signup/login error/success, pay form decimal rules and
  resubmit-is-a-no-op, lost response → `pay-uncertain` → retry completes
  exactly once, concurrent-spend refusal refreshing balance/feed, latest-
  refresh-wins with out-of-order responses, requests screen
  pay/decline/cancel and stale-pay-button, split preview == submitted shares
  for the stage-1 rounding table, authorizations screen create/capture/void
  with direction+status gating, seeded holds visible immediately after reset,
  activity feed visibility/order/notes, content negotiation, 375px and
  desktop layout (no horizontal scroll, visible labels, visible keyboard
  focus, contrast ≥ 4.5:1 on the wallet numbers).

## Requirement coverage

Regenerate with `python3 run.py --base-url http://x --list --md`; the table
below is committed as `COVERAGE.md`.

Requirement ids with no test — both are permissions, not behaviour:

- **R112** — "A custom illustration, brand asset or exact visual match to a
  reference is not required": permits an implementation choice; nothing to
  assert.
- **R137** — "No background polling, live synchronization, or recovery across
  page reloads is required": a scope limiter; absence cannot be asserted
  black-box without risking a false failure of a permitted mechanism.

## Assumptions (suite-level, conservative readings)

- **TA-1.** `GET /authorizations` returns its rows under an
  `authorizations` key (mirroring `GET /requests`' `requests`), with
  `has_more` (R183). Any other list key fails the suite and should be
  reconciled with @coordinator.
- **TA-2.** Combined-error precedence for `POST /authorizations` and
  `.../capture` follows the requirements' error tables read top-to-bottom
  (R163/R175 + assumption A8), e.g. insufficient `available` beats an unknown
  `to_handle`, and `authorization_expired` beats `forbidden`/exceeds. NOTE:
  design.md §13 orders validation differently (404/403 before state checks) —
  flagged to @coordinator; if design order is adjudicated canonical, only the
  two `*_error_precedence` tests change.
- **TA-3.** A seeded `captured` authorization's `captured_amount` is
  unspecified in the fixture model; only `remaining_amount == 0` and
  non-holding are asserted for it.
- **TA-4.** A non-boolean `final` (wrong JSON type) is `400 malformed_request`
  per stage-1 §5's wrong-field-type rule (R43); not asserted as 422.
- **TA-5.** Seeded `expires_at` values used by this suite are ≥ 2 h from reset
  time (the spec's floor is 1 h) to absorb clock skew.
- **TA-6.** UI "headline" for `wallet-available` is asserted as computed
  font-size ≥ `wallet-balance`'s once holds exist; "consistent visual system"
  (R107) is asserted as one shared non-default body font across all six
  routes, plus the distinct-state checks of `ui_distinct_states_loading`.
- **TA-7.** Decimal form inputs are typed in major units (`10.00` → 1000
  minor); the stage-1 rounding table is exercised through them converted to
  major units.