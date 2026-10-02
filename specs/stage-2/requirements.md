# Pocketful — Stage 2 requirements

Source: `we-are-devs/pocketful/spec/stage-2.md`. Section references (§) are to that file
unless marked `stage-1.md §N`. Every stage-1 requirement (R1–R103, `specs/stage-1/requirements.md`)
stays in force by reference and is not restated here; numbering continues from R104 so stage-1
and stage-2 ids never collide in design/task cross-references. Assumptions continue from A8
(stage-1 used A1–A7).

Brand: "Pebble" is the visible app name — header, every page title, signup and login copy —
per the human's original task. It is cosmetic only; no spec identifier (e.g. export `track`)
changes.

## A. Screens and routing

- **R104** (interface). "The following screens must be reachable by URL": `/` (balance, pay
  form, request form, activity feed), `/requests` (incoming/outgoing requests), `/split`
  (split form), `/signup`, `/login`. "Other screens must be reachable through the UI."
  `/authorizations` (added below, §"UI") is a sixth URL-addressable screen.
- **R105** (interface). "The browser and the API share `/requests`[, and `/authorizations`].
  Return the UI for `Accept: text/html`; API requests without that header receive JSON." —
  content negotiation on both routes, by the `Accept` header alone.
- **R106** (interface). "The UI must expose the `data-testid` attributes listed below."
  Additional elements are permitted; "the visual implementation is the team's choice subject
  to the product-quality requirements below."

## B. Product and visual quality (qualitative, testable via accessibility/visual acceptance checks)

- **R107** (behaviour). "The browser experience must feel like a coherent, presentation-ready
  consumer finance product, not a test harness with controls attached." Calm, trustworthy
  character.
- **R108** (behaviour). "Available funds must be the clearest monetary value once holds
  exist, with total and held funds visibly secondary." — `wallet-available` is the visual
  headline on `/` once `held > 0` (ties to R147 below).
- **R109** (behaviour). "Payments, requests, splits and authorisations should be easy to
  scan, and status, direction, privacy and money movement should be understandable without
  interpreting raw API data." Format people, amounts and timestamps for people first; expose
  technical identifiers only where they help the user.
- **R110** (behaviour). "Use a consistent visual system for typography, spacing, colour,
  controls and feedback. Primary actions must be easy to identify." Available, held, pending,
  loading, successful, refused and uncertain states must each be visually distinct, in
  addition to satisfying the behavioural requirements that define them.
- **R111** (limit, accessibility). Required flows stay clear and usable at a 375 CSS-pixel
  viewport and at conventional desktop widths, with no horizontal page scrolling. Inputs have
  visible labels; keyboard focus is apparent; text/controls have sufficient contrast.
  Considered empty, loading and error states; consistent navigation across the required
  routes.
- **R112** (compatibility). "A custom illustration, brand asset or exact visual match to a
  reference is not required."

## C. Signup and login UI

- **R113** (interface). `data-testid`s: `signup-email`, `signup-password`,
  `signup-display-name` (inputs), `signup-submit` (button); `login-email`, `login-password`,
  `login-submit`; `auth-error` (present only when there is an error); `current-user` (visible
  on every screen when signed in, text contains the display name); `current-handle` (text is
  exactly the caller's handle, no `@`, no surrounding words); `logout-button`.
- **R114** (behaviour). Signup/login call stage-1's `POST /signup` / `POST /login` (stage-1.md
  §5) and surface their error cases through `auth-error`.

## D. Balance and pay — `/`

- **R115** (interface). `wallet-balance` text is exactly the formatted amount (R125), with
  `data-amount="{minor units}"`. `pay-handle`, `pay-amount` (decimal string, e.g. `15.00`),
  `pay-note` inputs; `pay-visibility` select with option values exactly `public`/`private`;
  `pay-submit`; `pay-error` (shown only when the payment is refused, including insufficient
  funds); `request-handle`, `request-amount`, `request-note`, `request-submit`;
  `request-error`.
- **R116** (retry, idempotency). "Keep the pay form's values after success. Submitting it
  again without changing a field must not send another payment: `wallet-balance` falls once,
  the feed contains one payment and `pay-error` is absent. Changing a field makes the next
  submission a new payment request." The idempotency key is derived from the form's current
  field values and is stable while they are unchanged (ties to R135: "the key changes only
  when a field changes"); resubmission with the same key/body is stage-1's replay (stage-1.md
  §7), returning the stored 201 body without moving money again.
- **R117** (retry). "Retries follow §7" (stage-1.md) — malformed-body-under-used-key refusal,
  failed-request key release, etc. apply unchanged to the UI's retry behaviour.
- **R118** (behaviour). Decimal input rule: "the form accepts decimal amounts and submits
  minor units to the API." With `minor_units: 2`: `15.00` and `15` both submit `1500`; `15.5`
  submits `1550`.
- **R119** (error). "Nonnumeric input or more than `minor_units` decimal places must show the
  form's error element without sending a request. For example, `15.005` is rejected rather
  than rounded." Applies to `pay-amount`, `request-amount`, `split-amount`,
  `authorize-amount`, and `authorization-capture-amount-{id}` (same decimal-input family).

## E. Formatted amount (shared rule)

- **R120** (behaviour). `wallet-balance` (and every other formatted-amount `data-testid`:
  `activity-amount-*`, `request-amount-*`, `split-share-*`, `wallet-available`,
  `wallet-held`, `authorization-amount-*`, `authorization-captured-*`) is "the decimal with
  exactly `minor_units` decimal places, a single space, then the currency code:
  `100.00 EUR`." For `minor_units: 0` there is no decimal point: `1200 JPY`.
- **R121** (invariant). "Balances are never negative, so there is no sign." — formatter never
  emits a minus sign for any of these fields (holds even for `held`, which is always ≥ 0).

## F. Activity feed — `/`

- **R122** (interface). `activity-list` container, children newest first in the DOM;
  `activity-item-{payment_id}` per visible payment, `data-visibility="public"` or
  `"private"`; `activity-parties-{payment_id}` text contains both handles;
  `activity-amount-{payment_id}` formatted amount; `activity-note-{payment_id}` text is
  exactly the note, present even when empty; `empty-activity` shown instead of the list when
  nothing is visible.
- **R123** (behaviour). "Two payments with equal timestamps may appear in either order" —
  acceptance checks must not assert a strict order between equal-timestamp entries.
- **R124** (behaviour). Feed visibility, party display and note rules reuse stage-1's
  `GET /activity` visibility semantics (stage-1.md §8) — a private payment's non-party viewer
  never sees it here either.

## G. Requests — `/requests`

- **R125** (interface). `incoming-list`, `outgoing-list` containers; `request-item-{request_id}`
  per request, `data-status="{status}"`; `request-amount-{request_id}` formatted amount;
  `request-pay-{request_id}` / `request-decline-{request_id}` present only on a `pending`
  incoming request; `request-cancel-{request_id}` present only on a `pending` outgoing
  request; `request-error` shown when a pay/decline/cancel is refused; `empty-requests` shown
  when both lists are empty.
- **R126** (behaviour). Pay/decline/cancel from this screen call stage-1's
  `POST /requests/{id}/pay|decline|cancel` (stage-1.md §9) unchanged.

## H. Split — `/split`

- **R127** (interface). `split-amount` (decimal, same rule as `pay-amount`, R118/R119);
  `split-handles` (comma-separated, in order); `split-note`, `split-submit`; `split-preview`
  showing shares before submit, containing one `split-share-{handle}` per participant;
  `split-error` shown when refused.
- **R128** (behaviour, rounding). "`split-preview` must show the shares the server would
  compute, by the rule in `stage-1.md` §9, before anything is posted. The preview and
  submitted split must have identical shares." — the UI must compute the same remainder-first
  (or whatever stage-1 §9 specifies) rounding client-side, exactly matching
  `POST /splits`'s server-side computation for the same amount/participant count.

## I. Refresh, no-reload and ordering

- **R129** (behaviour). "After any successful action, the balance, the feed and the request
  lists on the same page must show the new state without a manual reload. Navigation must
  wait for the write to succeed before it refreshes the data. Any mechanism is fine, including
  a full navigation." "There is no live-update requirement here — another client may change
  state, but this browser need only refresh after its own action or an explicit refresh."
- **R130** (interface). `wallet-refresh`: a button on `/` that refreshes balance and feed
  without clearing the pay form.
- **R131** (concurrency). "Latest refresh wins: a delayed earlier read must not overwrite a
  later refresh, including when responses arrive out of order." Requires a sequence number (or
  equivalent) attached to each refresh request, with a stale response discarded on arrival.
- **R132** (behaviour, compatibility). "The same balance refresh rules apply to the available
  and held amounts introduced below" (§"Authorizations and captures") — R131 covers
  `wallet-available`/`wallet-held` too.

## J. Competing clients and uncertain outcomes

- **R133** (error). "Another client may spend the balance after this browser reads it. A
  refused payment shows `pay-error`, refreshes the balance/feed, and preserves all pay
  inputs."
- **R134** (error, concurrency). "A request cancelled elsewhere while its pay button is
  visible must show `request-error` when payment is refused and refresh the request list so
  the stale pay button disappears."
- **R135** (retry, concurrency). "If a payment response is lost, including after
  `POST /payments` commits, show `pay-uncertain` (nonempty text), not `pay-error`. Keep the
  unchanged form retryable with the same key and body." The idempotency key changes only when
  a field changes (restates R116's key-derivation rule from the uncertain-outcome angle).
- **R136** (retry). "Successful retry removes both error/uncertainty elements, refreshes the
  balance and feed, and moves money exactly once. Unknown outcomes are not confirmed
  rejections." — the UI must not show `pay-error` merely because a prior attempt's outcome is
  unknown; only an actual refusal response produces `pay-error`.
- **R137** (compatibility). "No background polling, live synchronization, or recovery across
  page reloads is required." — bounds R135/R136 to the current in-memory page session.

## K. Existing clients after an upgrade (export/import)

- **R138** (compatibility). "A stage-2 service must accept an export produced by the same
  team's stage-1 service."
- **R139** (compatibility). "A browser signed in before that export/import upgrade must
  remain signed in afterwards." — the stage-1 session token in the export remains valid after
  import (ties to stage-1's session-token export requirement, technical direction / design.md).
- **R140** (compatibility). "Existing pending requests remain payable through the request
  screen" after import.
- **R141** (compatibility, retry). "A payment whose response was lost before export remains
  retryable after import with the same body and key; the UI must recover the original payment
  and refresh the imported balance." — the UI's retry path (R135) must work across an
  import boundary using the imported idempotency record.
- **R142** (compatibility). "These requirements apply when import completes between browser
  requests; migration during an in-flight request is not required." "No page reload or new
  screen is required. The form and pending retry identity must survive the upgrade." —
  explicit scope limiter, recorded as read (no mid-request-migration handling needed).

## L. Authorizations and captures — model and invariants

- **R143** (behaviour). "A payment may be authorised now and captured later, for the full
  amount or less. An authorisation places a hold on the payer's wallet: it reserves money
  without moving it. Capturing moves the money; a final capture also releases whatever was
  not captured. Nonfinal captures keep the remainder held. An open authorisation expires and
  releases its remainder on its own."
- **R144** (invariant). "The sum of all wallet `total` values always equals the total seeded
  by the last reset. A hold moves no money; payments, settlements and captures transfer money
  between wallets." — conservation restated for `total`; a hold is balance-neutral.
- **R145** (invariant). "`available = total − held` must never be negative. Held funds cannot
  fund new payments, authorizations or settlement net debits. Captures may spend the money
  reserved for them." — every balance check that was against `total`/`balance` in stage 1
  (payments, requests-pay, settlement net debits, now also new authorizations) moves to
  `available` (restated concretely in R150).
- **R146** (invariant). "Cumulative captures must not exceed the authorized amount. Each
  idempotent capture moves money once. A closed hold cannot be captured again."
- **R147** (interface). `GET /me` keeps `balance`; `balance` equals `total`. `available` and
  `held` are new fields beside it. With no open holds, `balance`, `total` and `available`
  agree and `held` is zero, and every earlier (stage-1) behaviour is unchanged.
- **R148** (behaviour). `POST /payments` remains an immediate transfer; "it must not leave an
  intermediate hold or require a separate capture."
- **R149** (compatibility). "Every `409 insufficient_funds` in stage 1 — on `POST /payments`,
  `POST /requests/{id}/pay` and settlements — is now evaluated against `available`. With no
  open holds, the result is unchanged."
- **R150** (behaviour). "Paying a request remains immediate. Authorizing a request is out of
  scope." — no `POST /requests/{id}/authorize`-style endpoint; a request is paid (immediate
  transfer) or not at all.
- **R151** (compatibility). "`POST /splits` is unchanged" — still debits `total`/`available`
  immediately exactly as stage 1 (no hold phase for splits).
- **R152** (behaviour). "There are now seven idempotent write paths: stage 1's five,
  authorizations and captures. The same replay rules apply independently to each" (stage-1.md
  §7: claim-then-validate, replay returns stored response, mismatched body under used key is
  `409 idempotency_key_reuse`, failed request frees the key).

## M. Fixture / model additions

- **R153** (interface). Fixture gains `authorization_ttl_seconds` (service-wide default) and
  an `authorizations` array, each entry: `id`, `from_user_id`, `to_user_id`, `amount`, `note`,
  `visibility`, `status`, `expires_at`.
- **R154** (behaviour). "`authorization_ttl_seconds` applies to every authorisation created
  through the API. It defaults to 600 when omitted. If supplied, it must be a positive
  integer number of seconds. Seeded authorisations carry their own absolute `expires_at`
  instead" (not derived from the ttl).
- **R155** (behaviour). "A user's seeded `balance` is still `total`. `available` is derived,
  never seeded — the service subtracts the seeded open holds itself."
- **R156** (error). "A sum of seeded unexpired open holds larger than that user's `balance`
  is a reset error: `422 validation_failed` from `POST /_test/reset`, changing nothing,
  exactly like a negative seeded balance."
- **R157** (behaviour). "Seeded `status` is `open`, `captured`, `voided` or `expired`. Only
  `open` holds anything."
- **R158** (compatibility). "An earlier fixture may omit `authorizations` altogether;
  omission means an empty list." — stage-1 fixtures (R138) import with zero authorizations.
- **R159** (time, behaviour). "An authorization whose `expires_at` is at or before now is
  `expired` and holds no funds. Reads and writes must reflect expiry even if no request
  occurred at the deadline." — expiry is evaluated lazily at read/write time from the stored
  `expires_at`, not via a background timer; "`GET /authorizations` must show
  `status: "expired"`, and `GET /me` must include the released remainder in `available`."
- **R160** (time). "Seeded expiry times are at least an hour from reset time, in the past or
  future; newly created authorizations may have shorter lifetimes." — no minimum-lifetime
  validation on API-created authorizations.

## N. `POST /authorizations`

- **R161** (interface). `Idempotency-Key` required; caller is the payer. Body: `to_handle`
  (required), `amount` (required), `note`/`visibility` optional with `POST /payments`'
  defaults (`""`/`"public"`).
- **R162** (interface). `201` response shape: `authorization_id`, `from_user_id`,
  `from_handle`, `to_user_id`, `to_handle`, `amount`, `captured_amount` (`0`), `currency`,
  `note`, `visibility`, `status: "open"`, `expires_at`, `payment_id: null`, `created_at`.
  `expires_at` is `created_at` plus `authorization_ttl_seconds`.
- **R163** (error). Error precedence table: `available < amount` → `409 insufficient_funds`;
  `amount` below 1, above 1000000000, or not an integer → `422 validation_failed`;
  `to_handle` equal to caller's own handle → `422 self_payment`; `note` over 200 chars or
  `visibility` not `public`/`private` → `422 validation_failed`; unknown `to_handle` →
  `404 not_found`. (Row order in the spec table is the precedence order, consistent with
  stage-1 §5's payment error precedence — recorded as A8 below since the spec states the
  table but not explicitly "in this order.")
- **R164** (behaviour). "An open authorisation is not a feed item and never appears in
  `GET /activity`."

## O. `POST /authorizations/{id}/capture`

- **R165** (interface). `Idempotency-Key` required; only the receiver (`to` party) may
  capture. Body: `amount` optional (defaults to the authorisation's remaining amount),
  `final` optional boolean (default `true`).
- **R166** (retry). "As on `POST /requests/{id}/pay`, a replay must send the identical body —
  `{}` and `{"amount": 2000}` are different JSON values even when they mean the same capture,
  so reusing a key across the two is `409 idempotency_key_reuse` per `stage-1.md` §7." —
  idempotency body-equality is syntactic, not semantic.
- **R167** (interface). Returns `201` with the created payment, "in exactly the shape
  `POST /payments` returns," `authorization_id` set to this authorisation, `request_id: null`.
  Captured amount is the payment's `amount`; `note`/`visibility` copied from the
  authorisation; appears in the activity feed by the ordinary visibility rule. Payments
  created without an authorisation keep `authorization_id: null`; existing `request_id`
  semantics unchanged.
- **R168** (behaviour). Default (final) capture: authorisation becomes `captured`, carries
  `captured_amount` and `payment_id`, "releases the uncaptured remainder immediately:
  capturing 1500 of 2000 returns 500 to the payer's `available` in the same step" (atomic with
  the capture).
- **R169** (error). "Default: one final capture per authorisation. A second capture after a
  final capture is `409 authorization_not_open`."
- **R170** (behaviour). Extended capture mode: `{"amount": 700, "final": false}` keeps the
  remainder held; status stays `open`; further captures allowed up to the remainder.
  "Capturing the entire remainder closes it even with `final: false`." A final capture closes
  it and releases any remainder.
- **R171** (error). "`capture_exceeds_authorization` compares with the remaining amount;
  omitted amount defaults to that remainder." — the 422 check in the error table below is
  against remaining, not original, amount.
- **R172** (behaviour). "`captured_amount` is cumulative; `payment_id` is the latest capture;
  `payment_ids` lists every capture in order." "Every authorization response adds
  `remaining_amount`: the amount still held, zero when closed."
- **R173** (behaviour). "Void and expiry can close a partially captured authorization,
  release only the remainder, and preserve all capture records."
- **R174** (compatibility). "New fields do not change idempotency body equality" — adding
  `final`/omitting it, or any new response field, never affects R166's body-equality replay
  check.
- **R175** (error). Error table: authorisation not `open` → `409 authorization_not_open`;
  `expires_at` at or before now → `409 authorization_expired`; `amount` above uncaptured
  remainder → `422 capture_exceeds_authorization`; `amount` below 1 or not an integer →
  `422 validation_failed`; caller is not the receiver → `403 forbidden`; unknown authorisation
  → `404 not_found`. (Row order is the stated precedence order — A8.)

## P. `POST /authorizations/{id}/void`

- **R176** (interface). "Only the payer may void — the from party releasing their own hold.
  No idempotency key, like decline and cancel."
- **R177** (behaviour). "`200` with the authorisation, `status: "voided"`, the hold released.
  Voiding an already-voided authorisation is `200` with the current state." — void is
  naturally idempotent without a key, by state convergence.
- **R178** (error). "A `captured` or `expired` one is `409 authorization_not_open`."

## Q. Cross-cutting authorization access control

- **R179** (error). "For an existing authorization, capture and void return `403 forbidden`
  when the caller is not the permitted party, including callers who are neither party."
- **R180** (behaviour). "`GET /authorizations` returns only authorizations involving the
  caller" (as payer or receiver) — a non-party never sees it listed at all (distinct from the
  403 on direct capture/void by id).

## R. `GET /authorizations`

- **R181** (interface). Query params `direction` (`outgoing` = caller is payer, `incoming` =
  caller is receiver, absent = both), `status` (one of the four statuses, or absent for all),
  `limit`, `offset`. "Newest first by `created_at`."
- **R182** (time). "An authorisation expired by the clock matches `expired`, never `open`" —
  the `status` filter applies lazy-expiry (R159) before filtering, not the stored status.
- **R183** (compatibility). "`limit`, `offset` and `has_more` behave exactly as on
  `GET /requests`" (stage-1.md pagination rules).

## S. UI — `/authorizations` and wallet additions

- **R184** (interface). New route `/authorizations`; shares the route with the API per R105
  (`Accept: text/html` → UI, else JSON).
- **R185** (interface). `wallet-balance`: formatted `total`, retaining existing display and
  `data-amount` (unchanged from stage 2's balance screen, R115).
- **R186** (interface). `wallet-available`: formatted `available`, `data-amount`. "Present
  this as the headline number — it is what the user can actually spend" (ties to R108).
- **R187** (interface). `wallet-held`: formatted `held`, `data-amount`, "absent when `held`
  is zero."
- **R188** (interface). `authorize-handle`, `authorize-amount`, `authorize-note`,
  `authorize-visibility`, `authorize-submit`: same input rules as the pay form (R118/R119).
  `authorize-error` shown when refused, including insufficient available funds.
- **R189** (interface). `authorization-list` container on `/authorizations`, children newest
  first in the DOM. `authorization-item-{authorization_id}` carries
  `data-status="{status}"`. `authorization-amount-{id}` formatted authorised amount.
  `authorization-captured-{id}` formatted captured amount, present only when `status` is
  `captured`. `authorization-expires-{id}` text is the RFC 3339 `expires_at`.
- **R190** (interface). `authorization-capture-amount-{id}`: decimal input pre-filled with
  the remaining amount, present only on an incoming `open` authorisation.
  `authorization-capture-{id}` button, present only on an incoming `open` authorisation.
  `authorization-void-{id}` button, present only on an outgoing `open` authorisation.
  `authorization-error` shown when a capture or void is refused. `empty-authorizations`
  shown when the list is empty.
- **R191** (behaviour). "The UI must reflect seeded and newly created holds. Show available
  funds as the user's spending balance, including immediately after reset with open holds."

## T. Concurrency

- **R192** (concurrency). "Concurrent requests must produce the same results as executing
  them one at a time in some order, and the requirements above hold at every read" —
  serializability for every stage-2 endpoint and read, same as stage-1's global critical
  section (technical direction).

## Assumptions

- **A8**. The spec's error tables for `POST /authorizations` and
  `POST /authorizations/{id}/capture` (R163, R175) state cases but not explicitly "in this
  order"; read as precedence order top-to-bottom, consistent with stage-1 §5's payment
  validation order and with how `capture_exceeds_authorization` (checked against remaining,
  R171) must be evaluated before a generic `validation_failed` on the same field would ever
  be reached. Reasoning: stage-1's established pattern (specific business-rule 409/422s
  before generic shape checks) and internal consistency of the capture table.
- **A9**. "Authorizing a request is out of scope" (R150) is read as: no new endpoint
  combines a request with a hold; an authorization's `to_handle` may coincide with an
  existing request's counterparties, but the two features never interact (an authorization
  never references a `request_id`, and a request never references an `authorization_id`).
  Reasoning: the spec defines `authorization_id` only on the payment produced by a capture,
  and defines `request_id` only on payments from `POST /requests/{id}/pay`; both default to
  `null` for the other kind of payment, so a payment is never both.
- **A10**. `GET /authorizations` pagination defaults (`limit`/`offset` when omitted) mirror
  stage-1's `GET /requests` defaults exactly, per R183. Reasoning: explicit "behave exactly
  as on `GET /requests`."
- **A11**. The UI's split-preview computation (R128) is implemented by calling the same
  pure rounding function the server uses for `POST /splits`, not a client-side
  reimplementation, so the two can never diverge. Reasoning: spec requires the preview and
  submitted split to have "identical shares," and a second independent implementation of a
  rounding rule risks silent drift; this is an implementation choice, not a new behaviour.
- **A12**. "Latest refresh wins" (R131) is implemented with a monotonically increasing
  sequence number attached client-side to each refresh (and to the post-action refresh), and
  the UI discards any response whose sequence number is lower than the highest already
  rendered. Reasoning: spec names "sequence numbers" explicitly in the technical direction
  ("the latest refresh wins over delayed earlier responses (sequence numbers)").
- **A13**. `pay-uncertain` (R135) is shown only for a timeout/connection-loss on
  `POST /payments` itself (no HTTP response received), not for a definite error response;
  a definite `4xx` shows `pay-error` and a definite `2xx` shows success. Reasoning: spec
  contrasts "uncertain" (lost response) with "refused" (an actual error) and "unknown
  outcomes are not confirmed rejections" implies the uncertain state is reserved for the
  no-response case.
