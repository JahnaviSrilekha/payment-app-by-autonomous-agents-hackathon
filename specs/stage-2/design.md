# Pocketful — Stage 2 design

Builds on `specs/stage-1/design.md` §§1-9 unchanged except where noted: same process model
(`ThreadingHTTPServer`, `request_queue_size=256`, one global `STATE_LOCK`), same idempotent-write
pipeline shape (§5), same rounding function (§6), same settlement algorithm (§7), same HTTP
error-pipeline ordering (§9). This document covers only what stage 2 adds or changes:
authorizations/holds, the `available`/`held` split, the two new idempotent write paths, void,
lazy expiry, the browser UI, and the export/import bump to `format_version: 2`.

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

## Decision records

- [ADR-001: language, HTTP server and concurrency model](adr/001-language-and-server.md) (carried forward, unchanged)
- [ADR-002: password hashing](adr/002-password-hashing.md) (carried forward, unchanged)
- [ADR-003: id format](adr/003-id-format.md) (carried forward, unchanged; authorization ids use the same scheme, prefix `a_`)
- [ADR-004: lazy expiry, no background sweep](adr/004-lazy-expiry.md)
- [ADR-005: server-rendered UI with a shared retry/sequencing script, no SPA framework](adr/005-ui-architecture.md)
