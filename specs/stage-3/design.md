# Pocketful — Stage 3 design

Builds on `specs/stage-1/design.md` §§1-9 and `specs/stage-2/design.md` §§10-16 unchanged
except where noted (§§10-16 are reproduced below verbatim since stage-3 continues to ship
every stage-1/2 route). Same process model (`ThreadingHTTPServer`, `request_queue_size=256`,
one global `STATE_LOCK`), same idempotent-write pipeline shape, same rounding function, same
settlement algorithm, same HTTP error-pipeline ordering. This document's new material (§§17-24)
covers only what stage 3 adds: the revision/effective-recorded-time ledger model, `GET /me`
and `GET /statement` as historical views, corrections, stable statement pagination, historical
holds, and the export/import bump to `format_version: 3`.

## 10. Data model additions

```
User:  ...(unchanged fields from stage-1 design §2)..., balance (int)   # == total, unchanged meaning
Authorization:
  id, from_user_id, to_user_id, amount (int, original),
  captured_amount (int, cumulative, starts 0),
  note, visibility, status ("open" | "captured" | "voided" | "expired"),
  expires_at (RFC3339, absolute), created_at (RFC3339), seq (int),
  payment_ids: list[payment_id]   # every capture's payment, in order; payment_id = payment_ids[-1] or null

Service: ...(stage-1 fields)...,
  authorization_ttl_seconds: int (default 600),
  authorizations: dict[authorization_id -> Authorization],
  authorization_order: list[authorization_id]   # insertion order, newest-first views reverse this
Payment: ...(stage-1 fields)..., authorization_id (nullable)   # new; request_id/authorization_id
                                                                 # are never both non-null (A9)
```

`held` and `available` are **never stored** — they are computed at read time (§11) from
`authorizations`, exactly as `total`/`balance` already is a live stored integer (stage-1
design §2) rather than recomputed from the payment ledger. This mirrors the spec's own
framing: "`available` is derived, never seeded" (R155) applies equally to derivation at
runtime, not just at reset.

## 11. Expiry, `held` and `available` — pure functions, no background timer

```
effective_status(auth, now) -> str:
    if auth.status == "open" and auth.expires_at <= now:
        return "expired"
    return auth.status

remaining_amount(auth, now) -> int:
    if effective_status(auth, now) != "open":
        return 0
    return auth.amount - auth.captured_amount

held(user_id, service, now) -> int:
    return sum(remaining_amount(a, now) for a in service.authorizations.values()
               if a.from_user_id == user_id)

available(user_id, service, now) -> int:
    return service.users[user_id].balance - held(user_id, service, now)
```

Every one of these is a pure function of stored state and the current wall-clock time,
called fresh on every `GET /me`, `GET /authorizations`, and every write that needs
`available` (new authorization, payment, request-pay, settlement) or `remaining_amount`
(capture, void). **No endpoint mutates `status` from `open` to `expired`** — there is nothing
to mutate, because effective status is always recomputed (R159: "reads and writes must
reflect expiry even if no request occurred at the deadline" is satisfied for free, with no
sweep/cron needed, and expiry survives export/import unchanged since `expires_at` is
preserved verbatim). Stored `status` only ever transitions `open -> captured` or
`open -> voided`, written inside `STATE_LOCK` by the capture/void handlers; "expired" is a
view, never a stored write. See [ADR-004](adr/004-lazy-expiry.md).

This computation runs inside `STATE_LOCK` for writes (so a concurrent capture/void/expiry
race can never be observed as a negative `available` or a double-spend of the same hold —
R192) and inside `STATE_LOCK` for `GET /me`/`GET /authorizations` too, exactly as stage-1
already serializes all reads of `balance` the same way (stage-1 design §3: "every handler
that reads or writes service state acquires [`STATE_LOCK`] for the full duration").

## 12. Invariant enforcement (additions to stage-1 design §4)

| Invariant | Enforced by |
|---|---|
| R144 conservation of `total` | A hold writes nothing to any `balance`/`total`; only `POST /authorizations/{id}/capture` moves money, as one ordinary debit+credit inside the lock, identical in shape to `POST /payments` (§O). |
| R145 `available = total - held` ≥ 0, held funds unspendable elsewhere | §11's `available()` is the **only** function any write path calls to check affordability; `POST /payments`, `POST /requests/{id}/pay`, settlement net-debit (stage-1 §4), and `POST /authorizations` all call it in place of stage-1's bare `balance` check (R149/R163). A hold is reserved the instant `POST /authorizations` commits (adds a row read by every later `held()` call in the same lock acquisition), so two concurrent authorizations against the same headroom cannot both succeed — the second observes the first's row. |
| R146 cumulative captures ≤ authorized; closed hold can't be captured again | `capture_exceeds_authorization` compares the requested amount against `remaining_amount(auth, now)` (R171), computed fresh under the lock at validation time, after the idempotency-key resolution step (§13) but before commit; a second capture after `status != "open"` (including freshly-computed `"expired"`) is rejected at the same check (`409 authorization_not_open` / `409 authorization_expired`) before any mutation. |
| R168 remainder released atomically with a final capture | The capture handler's single commit step both increments `captured_amount` and (if `final` or the remainder hits 0) sets `status = "captured"` — one critical-section write, so `available` reflects the release in the same observable instant money moves; no intermediate state exists where the payment has moved but the remainder is still shown held. |
| R192 concurrency/serializability | Unchanged mechanism from stage-1 design §3: the single global lock totally orders every state access, so any concurrent mix of payments/requests/splits/settlements/authorizations/captures/voids is equivalent to some sequential order of the same operations. |

## 13. Idempotent-write pipeline — the two new paths

`POST /authorizations` and `POST /authorizations/{id}/capture` are idempotent paths 6 and 7,
and follow stage-1 design §5 verbatim, with their own per-endpoint validation at step 6:

- **`POST /authorizations`** step-6 order (R163, A8 — mirrors `create_payment` in
  `stage-1/src/payments.py` field-for-field, since stage-1's own table lists
  `insufficient_funds` first yet checks it last in the real implementation): `amount` shape
  (`422 validation_failed` if not an integer in `[1, 1_000_000_000]`) → `note` shape → `visibility`
  shape (`422 validation_failed`) → `to_handle == caller` (`422 self_payment`) → `to_handle`
  known (`404 not_found`) → `available(caller) >= amount` (`409 insufficient_funds`, last —
  needs a shape-valid amount already). Commit:
  insert the `Authorization` row (`status: "open"`, `captured_amount: 0`, `payment_ids: []`,
  `expires_at = created_at + authorization_ttl_seconds`); no wallet mutation (a hold moves no
  money, R144).
- **`POST /authorizations/{id}/capture`** step-6 order (R175, A8): authorization exists
  (`404 not_found`) → caller is the receiver (`403 forbidden`) → `effective_status == "open"`
  else `409 authorization_not_open`, with the expired case first checked specifically
  (`409 authorization_expired`) since an expired authorization is also not `"open"` but has
  its own code — the expiry check runs before the generic not-open check so an expired hold
  never reports the wrong code → `amount` shape (`422 validation_failed` if present and not a
  positive integer) → `amount` (or the default remainder) `<= remaining_amount` else `422
  capture_exceeds_authorization`. Commit: append to `payment_ids`, bump `captured_amount`,
  debit payer / credit receiver by the captured amount (ordinary payment, `authorization_id`
  set, `request_id: null`), set `status = "captured"` if `final` (default `true`) or the
  capture exhausts the remainder, else leave `"open"`.

Both store an `IdempotencyRecord` exactly as stage-1 design §5 step 8, keyed by
`(user_id, method, path, key)` with the **exact parsed-JSON body** — R166/R174: `{}` and
`{"amount": 2000}` are different stored bodies even when they would capture the same amount,
and adding/omitting `final` is just another body difference, never special-cased.

**`POST /authorizations/{id}/void`** has no idempotency key (R176), handled like stage-1's
decline/cancel (stage-1 design, request-decline path): acquire lock, authorization exists
(`404`), caller is the payer (`403 forbidden`), `status == "captured"` or effective-expired
→ `409 authorization_not_open`; `status == "voided"` already → `200` with current state
(no-op, naturally idempotent by convergence, R177); `status == "open"` → set `"voided"`,
return `200`. No stored record, no replay logic — reissuing the same void request is just
the same state transition applied again (or a no-op if already voided).

## 14. Browser UI architecture

Server-rendered HTML (stdlib `http.server` handlers return templated HTML strings — no
template engine dependency; small f-string/`string.Template`-based renderer) for every route
in R104/R184, chosen by `Accept`: a request whose `Accept` header contains `text/html` gets
HTML; everything else (including no `Accept` header, matching stage-1's existing JSON
endpoints) gets JSON, per R105. All CSS and JS are inlined or served from the same container
with no external URLs (delivery constraint: no CDN, no runtime network).

**Why server-rendered + a small in-page script, not a SPA:** the spec explicitly allows
"any mechanism, including a full navigation" (R129) for post-action refresh, and requires no
build step beyond what's bundled in the image. A SPA framework would add a build pipeline and
bundle size for no required capability. See [ADR-005](adr/005-ui-architecture.md).

**One shared static module (`/static/app.js`, inlined into every HTML page or served from
the same container with a `Cache-Control` header — still zero runtime network calls out)**
owns three cross-cutting behaviours so each of pay/authorize/split/request/refresh does not
reimplement them:

1. **Idempotency key derivation (R116, R135).** For each write form, the key is
   `sha256(form_id + "|" + JSON.stringify(current field values))`, recomputed on every input
   event and cached; the key sent with a submission is whatever was cached *before* the user
   changes any field again. Resubmitting unchanged fields reuses the same key and the same
   JSON body (stage-1's replay, §5 step 5) — "the key changes only when a field changes"
   (R116) falls out directly from hashing the field values themselves.
2. **Submit/refresh lifecycle.** On submit: disable the submit control, POST with the cached
   key; on a definite error response, render `*-error` from the response body's error code
   (a small code→message table) and re-enable the form, fields intact; on network
   failure/timeout (no response), render `pay-uncertain` (the only form with an "uncertain"
   state — R135/A13) and leave the form retryable with the identical cached key/body — the
   next submit click sends the exact same request, which is stage-1's replay path whether the
   original attempt actually committed or not (R136); on success, clear error/uncertain
   elements and trigger a refresh (§14.3).
3. **Sequence-numbered refresh (R131, R132, A12).** Every GET that repaints `wallet-*`,
   `activity-*`, `request-*`, or `authorization-*` elements is issued with a client-side
   monotonically increasing integer; a response is applied to the DOM only if its sequence
   number is the highest seen so far for that resource (balance+feed are one resource;
   requests and authorizations are tracked separately), so an out-of-order late response from
   an earlier refresh is dropped. `wallet-refresh` and the post-success refresh both go
   through this same function.

Each screen's inline script is a thin caller of this module: it lists its own `data-testid`
fields, wires their events, and renders server JSON into the existing DOM nodes (no client
framework/virtual DOM — the HTML already has the right structure from the server-rendered
initial page, and a refresh re-renders the same handful of containers from the JSON
response).

**Split preview (R128, A11)** calls a shared pure function `split_shares(amount, n)`
(stage-1 design §6) compiled once into `/static/app.js` as the *same* algorithm the server
runs in `src/splits.py` — ported by hand, not reimplemented independently, and covered by a
unit test that runs the same table (334/333/333 etc., stage-1 design §6) against the JS port
so the two can never silently diverge.

**Session continuity across import (R139, R140, R141).** The browser holds its bearer token
in an in-memory JS variable set at login/signup and replayed on every fetch; it never reads
the token from a server-rendered page after import, so nothing server-side needs to change
for the token to keep working — stage-1 already exports/imports the `tokens` map verbatim
(stage-1 design §8), and a page that was never reloaded still has the same token in memory.
A pending retry (R141) works the same way: the pay form still holds its cached key/body in
page memory; resubmitting it hits the imported `IdempotencyRecord` exactly as it would have
hit the pre-import one, because import replaces `Service.idempotency` wholesale (stage-1
design §8) under the same `(user_id, method, path, key)` addressing scheme stage-1 already
uses — no new mechanism is needed for cross-import retry, only for the **existing** one to be
part of the exported/imported state, which it already is.

## 15. Export / import format — `format_version: 2`

`state` adds `authorization_ttl_seconds` and `authorizations` (serialized `dict` → JSON
array, in `authorization_order`) to the stage-1 `Service` structure; every stage-1 field is
unchanged (stage-1 design §8's compatibility promise: "later stages add fields ... never
remove or repurpose stage-1 fields"). Payment gains `authorization_id` (nullable, defaults
`null` for every stage-1-originated payment on import).

Import accepts `format_version` **1 or 2**: a version-1 payload is accepted as
`authorization_ttl_seconds = 600` (the default, R154) and `authorizations = []` (R158 —
"an earlier fixture may omit `authorizations` altogether; omission means an empty list"),
with every existing payment's `authorization_id` defaulting to `null`. Any other
`format_version`, or a structurally invalid `state`, is `422 validation_failed`, destination
unchanged — unchanged behaviour from stage-1 design §8 except for the widened accepted-version
set. `POST /_test/reset`'s fixture-validation pass (R156: seeded open holds summing above a
user's `balance` is `422 validation_failed`, nothing changes) runs after structural parsing,
before any user/authorization is actually installed, exactly like stage-1's existing
negative-seeded-balance check (same reset validation pass, one more rule added to it).

Export always emits `format_version: 2` going forward (so a stage-2 export, re-imported into
stage-2 or a later stage, is a version-2 round-trip); it never emits version 1.

## 16. HTTP error pipeline — additions

Unchanged from stage-1 design §9 (unparsed-body → auth → idempotency-key resolution →
field validation → resource/permission checks), with two new 409 codes
(`authorization_not_open`, `authorization_expired`, `capture_exceeds_authorization` is 422)
slotted into step 5 at the position given by each endpoint's own table (§13 above) and A8's
precedence assumption.

## 17. Data model additions — revisions, snapshots

```
Revision:
  revision (int, 1-based, append-only per payment),
  amount (int, the selected amount for this revision),
  effective_at (RFC3339), recorded_at (RFC3339, server-assigned, strictly increasing per payment),
  reason (str, "" for revision 1)

Payment: ...(stage-1/2 fields: id, from_user_id, to_user_id, amount, note, visibility,
  created_at, request_id, authorization_id, settlement_id)...,
  revisions: list[Revision]   # revisions[0] is revision 1: amount == Payment.amount,
                               # effective_at == recorded_at == created_at, reason == ""
  # Payment.amount/created_at are the ORIGINAL values and never change (R241) — they are
  # what every non-statement response (activity, requests, authorizations, the bare
  # Payment.amount field) always shows. Only a statement entry overlays a selected revision.

User: ...(stage-1/2 fields)..., base_balance (int)   # set once at reset/import; see §18

Service: ...(stage-1/2 fields)...,
  statement_snapshots: dict[token -> SnapshotRecord]

SnapshotRecord:
  user_id, entries (list, already selected/filtered/sorted, full window, unpaginated),
  opening_balance, closing_balance, from_used, to_used, known_at_used   # frozen at creation
```

`base_balance(user)` (R216) is computed once, at reset or import, as: seeded ending
`balance` minus the net effect (sum of signed original amounts) of that user's seeded
payments. A user created after reset (no seed row) has `base_balance = 0` (R217). It never
changes after that — corrections only ever add revisions, never touch `base_balance`
— which is exactly how R216 ("corrections must not change opening balances") is enforced:
there is no code path that writes `base_balance` outside reset/import.

## 18. The one balance function — `balance_view`

Every current and historical money read (`GET /me`'s `balance`, `GET /statement`'s
`opening_balance`/`closing_balance`/`balance_after`) is computed by one pure function, so
"as of" and "as known at" are never implemented twice:

```
select_revision(payment, known_at) -> Revision | None:
    # known_at is None means "now" (everything known when the read begins, R248) — resolved
    # to the actual wall-clock instant once per request (A16), then treated as a normal value.
    candidates = [r for r in payment.revisions if r.recorded_at <= known_at]
    return candidates[-1] if candidates else None   # revisions is already recorded_at-ascending (R230)

signed_amount(payment, user_id, revision) -> int:
    return +revision.amount if payment.to_user_id == user_id else -revision.amount

balance_view(user, as_of, known_at) -> int:
    # as_of is None means "now"/unbounded-above; both resolved once per request (A16).
    total = user.base_balance
    for p in payments_touching(user):           # sent or received, O(payments); fine at this scale
        rev = select_revision(p, known_at)
        if rev is not None and rev.effective_at <= as_of:
            total += signed_amount(p, user.id, rev)
    return total
```

- `GET /me` with no `as_of`/`known_at`: `balance_view(user, now, now)` — reduces to "sum of
  latest revisions of every payment", i.e. the plain live total (R200).
- `GET /me?as_of=T`: `balance_view(user, T, now)` (R201-203).
- `GET /me?as_of=T&known_at=K`: `balance_view(user, T, K)` (R276, §20).
- `GET /statement`'s `opening_balance` for window `[from, to)`: `balance_view(user, from, known_at)`
  computed with a **strict** `<` at the `from` boundary (R210 "immediately before `from`"),
  which here means evaluating `balance_view` with `as_of` set to the instant *just before*
  `from`; implemented as a variant `strict_before=True` flag on the `effective_at <= as_of`
  test (`<` instead of `<=`) rather than a second function, since the only difference is
  that one comparison operator. `closing_balance` is the same call at `to` with
  `strict_before=True`. This makes R211 (`opening + Σdelta == closing`) true by construction:
  the entries in `[from, to)` are exactly the payments whose selected revision flips from
  excluded-at-`from` to included-at-`to`, so their deltas telescope exactly between the two
  calls — no separate reconciliation code is needed or written.
- The live, incrementally-maintained `User.balance`/`total` field from stage-1/2 design
  (unchanged: every payment and every correction still updates it immediately, in the same
  critical section as the money movement, R234) remains the fast path for the *current*,
  *fully-known* view and for every affordability check (`insufficient_funds`, R236/R285) —
  `balance_view` is used **only** when `as_of`/`known_at` is supplied, or for the
  `historical_overdraft` boundary sweep (§19), never to recompute the live total on every
  ordinary request. `balance_view(user, now, now)` and `User.balance` are kept equal by
  construction (every write path updates both: the live field directly, and implicitly the
  revision list `balance_view` reads) and this equality is exactly what the reviewer's stress
  probe checks (conservation, R240).

`held_view`/`available_view` (§20) are the authorization-side counterpart, built the same
way from authorization lifecycle events instead of payment revisions.

## 19. `POST /payments/{payment_id}/corrections` — idempotent path 8

Follows stage-1 design §5 verbatim through idempotency-key resolution (replay/reuse at the
`(user_id, "POST", path, key)` level, exact stored body, R231-233), then step 6
(field/business validation) and step 7 (commit) are:

1. Payment lookup — 404 if unknown (R221).
2. Sender check — caller must be `payment.from_user_id`, else 403 `forbidden` (R220).
3. Field shape — `expected_revision` positive int, `amount` int `0..1_000_000_000`, `reason`
   1..200 chars, `effective_at` parseable RFC3339 not later than now; else 422
   `validation_failed` (R222-227).
4. Immutability — `payment.settlement_id is not None or payment.authorization_id is not None`
   → 422 `linked_payment_immutable` (R272, R275). (A15: this structural check runs after
   field-shape validation but before the revision/affordability checks below, since it is
   unconditionally true or false for the payment regardless of the correction's content.)
5. Staleness — `expected_revision != len(payment.revisions)` → 409 `stale_revision` (R231).
6. Compute `delta = amount - payment.revisions[-1].amount`. If `delta == 0` the correction
   still appends a new revision (a no-op amount restated with new `effective_at`/`reason` is
   valid — the spec places no floor other than the revision changing `effective_at`/`reason`);
   no money moves. If `delta > 0` (increase) the **debtor is the original sender**; if
   `delta < 0` (decrease) the **debtor is the original receiver** (R235), moving `abs(delta)`
   to the other party.
7. Affordability (live, current balances; A15/R236) — if the debtor's current `available`
   can't cover `abs(delta)`, 409 `insufficient_funds`, no state change.
8. Historical-overdraft sweep (R237-238, R284) — tentatively append the new revision to a
   working copy; let `B` = the set of distinct `effective_at` instants across both parties'
   payments after the tentative append (A18: balance is piecewise constant between such
   instants, so checking only these boundaries is exhaustive). For each `t` in `B`, both
   parties' `balance_view(party, t, now)` **and**, if either party has open or
   recently-closed holds, `balance_view(party, t, now) - held_view(party, t, now)` (R284's
   `total` and `available`) must be `>= 0`. Any violation → 409 `historical_overdraft`,
   discard the tentative revision, no state change (R239) — nothing was ever committed, so
   "discard" is simply not entering step 9.
9. Commit (single critical section): append the real revision to `payment.revisions`
   (`recorded_at` = fresh timestamp, strictly greater than the previous revision's, R230);
   apply `delta` to the live `User.balance`/`total` of both parties (same mechanism as an
   ordinary payment, R234); store the idempotency record with the 201 body (`payment_id`,
   `revision`, `amount`, `effective_at`, `recorded_at`, `reason`, R229).

Steps 7-8 are the explicit precedence `insufficient_funds` before `historical_overdraft`
(R236-237); both are evaluated against the *tentative* post-correction world, never
partially applied.

## 20. `GET /me` and `GET /statement` with `known_at` — selection and historical holds

`select_revision` (§18) already implements R247-248 exactly: "latest revision recorded at
or before `known_at`" / "none yet recorded → contributes nothing" / "omission → everything
known now".

**Historical holds** (R276-290) generalize stage-2 §11's `effective_status`/`remaining_amount`
from a single implicit "now" to an explicit `(as_of, known_at)` pair, replacing stage-2's
functions (not adding a parallel path — stage-2's own `now`-only calls become
`effective_status_view(auth, now, now)`, so there is exactly one implementation):

```
known_events(auth, known_at):
    # creation, each capture, and void all share one "event time" that is both their
    # effective time and their recorded time (A15 extends the settlement convention,
    # R271, to hold events) — so one comparison against known_at gates both axes.
    caps = [c for c in auth.captures if c.event_time <= known_at]
    void = auth.void if auth.void and auth.void.event_time <= known_at else None
    return caps, void   # creation itself is gated by the caller (below)

effective_status_view(auth, as_of, known_at) -> str:
    if auth.created_at > known_at:
        return "unknown"       # not yet known at all; contributes nothing anywhere it's used
    caps, void = known_events(auth, known_at)
    if void is not None and void.event_time <= as_of:
        return "voided"
    if any(c.final and c.event_time <= as_of for c in caps):
        return "captured"
    if as_of >= auth.expires_at:        # deadline is known as soon as creation is (R279);
        return "expired"                # takes effect functionally at expires_at (R278, R280)
    return "open"

remaining_amount_view(auth, as_of, known_at) -> int:
    if effective_status_view(auth, as_of, known_at) != "open":
        return 0
    caps, _ = known_events(auth, known_at)
    captured_by_as_of = sum(c.amount for c in caps if c.event_time <= as_of)
    return auth.amount - captured_by_as_of

held_view(user, as_of, known_at) -> int:
    return sum(remaining_amount_view(a, as_of, known_at) for a in user.authorizations_as_payer)
```

`effective_status_view(auth, now, now)` and stage-2's `effective_status(auth, now)` are the
same function by construction (every `known_at`/`as_of`-gated branch degenerates to the
plain time comparison when both equal `now`), so `GET /authorizations` and the capture/void
handlers (§13, unchanged) keep calling the two-argument form — it is kept as a thin wrapper
`effective_status(auth, now) = effective_status_view(auth, now, now)` so stage-2 call sites
are untouched.

`GET /me?as_of=T&known_at=K` response: `total = balance_view(user, T, K)`,
`held = held_view(user, T, K)`, `available = total - held`, `balance = total` (R276).
Without `as_of`, `T` = the instant the request began (R281, A16); without `known_at`, `K` = the
same instant (R248).

## 21. `GET /statement` — entries, windowing, and ordering under revisions

For caller `user`, window `[from, to)` (`from` defaults to `-infinity` as a sentinel meaning
"no lower filter", A14; `to` defaults to the request-start instant, A16/R205), optional
`known_at` (default: request-start instant):

```
entries = []
for p in payments_touching(user):             # sent or received, R213 (no visibility filter)
    rev = select_revision(p, known_at)
    if rev is None:
        continue
    if from <= rev.effective_at < to:          # half-open window, on the SELECTED effective_at (R254)
        entries.append((rev.effective_at, p.id, p, rev))
entries.sort(key=lambda e: (e[0], e[1]))        # effective_at asc, then payment id asc (R254, A17)
opening_balance = balance_view(user, from, known_at, strict_before=True)   # §18
closing_balance = balance_view(user, to, known_at, strict_before=True)
running = opening_balance
for (_, _, p, rev) in entries:
    delta = signed_amount(p, user.id, rev)
    running += delta
    # emit {payment: {...p, amount: rev.amount}, delta, balance_after: running,
    #       revision: rev.revision, effective_at: rev.effective_at, recorded_at: rev.recorded_at}
# running == closing_balance here by construction (R211) — asserted in the developer's unit test
```

Pagination (`limit`/`offset`, same bounds as `GET /requests`, R206) slices the already-fully-
computed `entries` list; `opening_balance`/`closing_balance`/each `balance_after` never depend
on the slice (R212). A zero-amount revision still produces an entry with `delta = 0` (R257);
no correction is counted alongside the revision it replaces because `select_revision` returns
exactly one revision per payment (R258).

## 22. Stable statement pagination — snapshots

The **first** `GET /statement` call for a given `(from, to, known_at)` combination (i.e. one
not supplied a `snapshot`) runs §21 once, stores the full `entries`/`opening_balance`/
`closing_balance`/resolved `to`/`known_at` in a new `SnapshotRecord` under a fresh opaque
token (`ids.new_id("snap")`, same scheme as every other id, ADR-003; A19: server-generated,
not self-describing), and returns page 1 plus the token (R260-261).

`GET /statement?snapshot=<token>&limit=&offset=`: look up the token in
`service.statement_snapshots`; 404 `not_found` if absent, created by another user, or created
before the last reset (reset clears the dict wholesale, same as every other in-memory
collection, R264-265); any of `from`/`to`/`known_at` present → 422 `validation_failed` before
the lookup (A15/R263). Otherwise slice the **stored** `entries` by `limit`/`offset` and return
the **stored** `opening_balance`/`closing_balance` — no recomputation, so later payments or
corrections cannot change the answer (R262, R266, R268/R290). Because every write and every
snapshot read/creation runs inside the single global `STATE_LOCK` (ADR-001, unchanged), there
is no race between "a correction lands" and "a snapshot is created from a torn read" —
snapshot creation is just another critical section.

## 23. Export/import — `format_version: 3`, cross-stage compatibility, historical `total`

Export bumps to `format_version: 3` (A20): every `Payment` gains a `revisions` array
(serialized in full, revision order) and every `User` gains `base_balance`, both carried
through **as stored**, never recomputed at import, so a stage-3-to-stage-3 export/import
cycle preserves every correction's history exactly (R241 extended to survive import; see
ADR-006's consequence that `balance_view` has exactly one source of truth — the exported
`revisions` list — so there is nothing else that could drift). `statement_snapshots` are
**not** exported (R265 already permits their loss across a restart; a re-paged snapshot
after a restart is simply a fresh `GET /statement` call, which is indistinguishable to a
caller who starts over with a plain query).

Import accepts `format_version` 1, 2, or 3. For 1 or 2 (no `revisions`/`base_balance`
present): every payment gets `revisions = [the implicit revision 1 built from its existing
amount/created_at]`; every settlement-member payment's revision 1 uses the settlement's
`committed_at` as both `effective_at` and `recorded_at` (R271 — nothing changes in
substance, only in which field name now carries that value, since it was always the
payment's one and only timestamp in stages 1-2); every user's `base_balance` is computed
from the imported `balance` and the imported payments exactly as a reset fixture would be
(§17) — correct for these formats because they carry no correction history to lose. For
format 3: both fields are taken directly from the payload, unchanged. Any other
`format_version`, or a structurally invalid `state`, is `422 validation_failed` (unchanged
from stage-1 §8 / stage-2 §15). Authorizations/captures import unchanged from stage-2 §15;
captures remain payments with `authorization_id` set, so they are automatically covered by
the immutability check (§19 step 4) without any import-time special-casing. After import of
any accepted version, an imported stage-1/2/3 state is indistinguishable, from that moment
on, from a stage-3 service seeded with the equivalent fixture directly.

## 24. HTTP error pipeline — additions

Unchanged shape from stage-1 §9 / stage-2 §16 (parse body → authenticate → idempotency-key
resolution → field validation → resource/permission/business checks). New codes slot in at
the position each endpoint's own table above gives: `stale_revision`, `insufficient_funds`,
`historical_overdraft` (409, corrections, §19); `linked_payment_immutable` (422, corrections,
settlement/capture checks, §19); `not_found` for an unrecognized/foreign/pre-reset snapshot
token (404, §22); `validation_failed` for a malformed/mutually-exclusive statement query or
correction body (422, §19/§22).

## Decision records

- [ADR-001: language, HTTP server and concurrency model](adr/001-language-and-server.md) (carried forward, unchanged)
- [ADR-002: password hashing](adr/002-password-hashing.md) (carried forward, unchanged)
- [ADR-003: id format](adr/003-id-format.md) (carried forward, unchanged; authorization ids use the same scheme, prefix `a_`; snapshot tokens prefix `snap_`)
- [ADR-004: lazy expiry, no background sweep](adr/004-lazy-expiry.md) (carried forward; generalized by ADR-006, not replaced)
- [ADR-005: server-rendered UI with a shared retry/sequencing script, no SPA framework](adr/005-ui-architecture.md) (carried forward, unchanged)
- [ADR-006: append-only revisions + one `balance_view` function for every historical read](adr/006-revision-ledger.md)
- [ADR-007: statement snapshots as server-held frozen records, not re-derivable tokens](adr/007-statement-snapshots.md)
