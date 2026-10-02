# Pocketful — Stage 1 requirements

Source: `we-are-devs/pocketful/spec/stage-1.md`. Every numbered requirement quotes its source
section. Product brand name "Pebble" is cosmetic only (UI copy does not exist in stage 1,
which is API-only); the `track` value in export stays `"pocketful"` exactly as specified.

## A. Scope and global invariants (spec §1)

- **R1** (invariant). "The sum of wallet balances always equals the total seeded by the last
  `POST /_test/reset`." — conservation holds after any mix of operations, including concurrent
  and retried ones.
- **R2** (invariant). "No wallet balance may be negative, including transiently." — holds even
  during concurrent writes.
- **R3** (invariant). "A payment request may move money at most once." — exactly-once effect
  for every idempotent write.
- **R4** (behaviour). "All amounts are exact integer counts of minor units." Deposits,
  top-ups, withdrawals, cards and bank integrations are out of scope; money moves only
  between existing wallets.

## B. Delivery and deployment (spec §2)

- **R5** (interface). Deliver an HTTP service, a `Dockerfile`, and a `RUN.md` with a command
  that builds and starts the service without manual setup. Language/framework/storage
  unrestricted; submission is a container, not a Python package.
- **R6** (interface). Image runs standalone with `-e PORT=<port>` and a port mapping. No
  outbound network at runtime; all deps/assets bundled in the image.
- **R7** (limit). Resource limits: 2 vCPU, 2 GiB memory, ready within 60 s of start, up to 50
  concurrent requests in flight, 5 s per-request timeout (10 s for `POST /_test/reset`),
  outbound network available at build time only, disk ephemeral (state need not survive
  restart).
- **R8** (limit). Server request queue of at least 256 (technical direction, not spec text,
  but mandated by the coordinator's decided-up-front direction).

## C. Runtime contract (spec §3)

- **R9** (interface). Listen on `0.0.0.0` using `PORT` env var, default `8080`.
- **R10** (behaviour). `GET /health` → 200 `{"status":"ok"}` once ready, within 60 s of
  start; non-200 permitted before ready.
- **R11** (behaviour). `POST /_test/reset` with a fixture body → 204, replacing all state.
  Subsequent requests see only that fixture. Repeatable. No auth required. Must be enabled
  in the delivered image.
- **R12** (interface). All requests/responses `application/json; charset=utf-8`.
- **R13** (interface). Response timestamps are RFC 3339 with explicit offset.
- **R14** (behaviour). Unknown body fields are ignored, never an error.
- **R15** (behaviour). Unknown query parameters are ignored.
- **R16** (interface). IDs are opaque strings, at most 64 characters, format chosen by
  implementation.

## D. Model (spec §4)

- **R17** (behaviour). One currency per service instance, declared in the fixture
  (`currency`, `minor_units`). Amounts are integer counts of minor units.
- **R18** (behaviour). API amounts must have an integral numeric value: `1000`, `1000.0`,
  `1e3` are equivalent valid inputs; booleans and strings are not numbers.
- **R19** (behaviour). Handle: unique across service, matches `^[a-z0-9_]{1,20}$`, immutable
  once set.
- **R20** (behaviour). Seeded users take handle from fixture.
- **R21** (behaviour). Signup-created users derive handle from email: take local part,
  lowercase, replace chars outside `[a-z0-9_]` with `_`, truncate to 20 chars. If taken,
  signup fails with `409 handle_taken` and no account is created.
- **R22** (behaviour). New users start with balance `0`; can receive money / be asked for
  money immediately.
- **R23** (behaviour). A payment moves money from one wallet to another, immediately and
  atomically; either sent directly or created by paying a request.
- **R24** (behaviour). A request asks someone for money; `requester` receives, `payer` is
  asked. States: `pending` → exactly one of `paid`, `declined`, `cancelled`. Only payer pays
  or declines; only requester cancels.
- **R25** (behaviour). A request may exceed the payer's balance; that's legal at creation.
  Paying while short → `409 insufficient_funds`, changes nothing. Money arriving later makes
  it payable.
- **R26** (behaviour). Visibility belongs to the payment, not the request; chosen by payer
  when money moves. Requests carry no visibility and never appear in anyone's feed.
- **R27** (behaviour). `GET /activity` returns payments only, visible to caller iff
  `visibility=public` OR caller is sender/receiver. No other rule (no follow graph/mute
  list). Requests never appear there.
- **R28** (behaviour). `GET /requests` returns only requests where caller is requester or
  payer.
- **R29** (behaviour). A split is not a feed item; its created requests are visible to their
  two parties; fulfilling payments follow the normal feed rule.
- **R30** (behaviour). Visibility is one value on the payment, seen identically by both
  parties and everyone else; `private` hides from third parties only, not from the receiver.
- **R31** (limit). `amount` ≤ `1000000000` per request; no operation produces a balance
  outside ±2⁵³; arithmetic must be exact (no rounding error).
- **R32** (interface/fixture). Fixture format: `currency`, `minor_units`, `users[]` (id,
  email, password, display_name, handle, balance), `payments[]`, `requests[]`, optional
  `settlement_operator_ids` (§11).
- **R33** (behaviour). Seeded users can log in with the given password immediately.
- **R34** (behaviour). Fixture `balance` is the wallet balance after all seeded payments are
  applied; implementation must not replay seeded payments against balances.
- **R35** (error). A fixture `balance` below zero → reset returns `422 validation_failed`,
  changes nothing.
- **R36** (limit). `minor_units` is 0, 2 or 3 (fixtures use JPY=0, EUR=2, BHD=3).
- **R37** (behaviour). No administrative balance endpoint exists (out of scope).

## E. Errors (spec §5)

- **R38** (error). Every 4xx/5xx body: `{"error":{"code":..., "message":...}}`.
- **R39** (error). Status/code table: 400 `malformed_request` (unparseable body or wrong JSON
  type), 400 `missing_idempotency_key`, 401 `unauthenticated`, 403 `forbidden`, 404
  `not_found`, 409 `idempotency_key_reuse`, 422 `validation_failed` (missing required
  field/query param, or a stated rule violated with no more specific code).
- **R40** (error). Correct JSON type but invalid format/out-of-range → 422
  `validation_failed`, unless endpoint specifies otherwise (invalid dates, negative counts,
  values exceeding stated max/length).
- **R41** (error). Endpoint-specific field rules take precedence: invalid `amount` (incl.
  strings/booleans), non-string `note` (incl. `null`), `visibility` other than
  `public`/`private` → 422 `validation_failed`. Omission alone selects defaults.
- **R42** (error). Integer-valued query parameter must be plain decimal digits: `1e9`,
  `4.0`, `+4` → 422 `validation_failed` regardless of numeric value.
- **R43** (error). 400 `malformed_request` reserved for unparseable body or wrong field
  type.
- **R44** (limit). Shared ranges on every endpoint that takes them: `Idempotency-Key` 1-255
  chars else 422; `limit` integer 1-200 else 422; `offset` integer ≥0 else 422.
- **R45** (limit). No 5xx responses ever, including under concurrent load.

## F. Authentication (spec §6)

- **R46** (interface). `POST /auth/signup` body `{email, password, display_name}` → 201
  `{user_id, display_name, token}`. No `handle` field in request.
- **R47** (interface). `POST /auth/login` body `{email, password}` → 200 `{user_id,
  display_name, token}`.
- **R48** (error). Email already registered → 409 `email_taken`.
- **R49** (error). Password < 8 chars → 422 `validation_failed`.
- **R50** (error). `email` not of form `local@domain` → 422 `validation_failed`.
- **R51** (error). Wrong password or unknown email on login → 401 `unauthenticated`.
- **R52** (error). Derived handle already taken → 409 `handle_taken`, no account created.
- **R53** (interface). Every other endpoint requires bearer token except `/health`,
  `/_test/reset`, `/auth/signup`, `/auth/login`. `Authorization: Bearer <token>`.
- **R54** (behaviour). Tokens do not expire. An account may have multiple valid tokens /
  concurrent sessions.
- **R55** (behaviour). Passwords stored via password-hashing function (bcrypt/scrypt/Argon2
  or equivalent); plaintext storage forbidden.

## G. Idempotency (spec §7)

- **R56** (interface). Five write paths require `Idempotency-Key`: `POST /payments`,
  `POST /requests`, `POST /requests/{id}/pay`, `POST /splits`, `POST /settlements`.
- **R57** (behaviour). Key scoped to authenticated user; two users may reuse the same key
  string independently.
- **R58** (behaviour). Replay = same user, same method, same path, same body. Same key +
  same body on a different path is a different (non-replay) request and must succeed
  normally.
- **R59** (error/behaviour) table: header absent/empty → 400 `missing_idempotency_key`;
  first use → normal response, 201; replay (same key, same body) → 200, body identical to
  original as JSON value; same key different body → 409 `idempotency_key_reuse`; key reused
  after original failed with 4xx → treated as first use.
- **R60** (behaviour). "Same body" = same JSON value after parsing; key order/whitespace
  irrelevant.
- **R61** (concurrency). Concurrent identical requests with unused key: exactly one 201, rest
  200 with the same body; operation takes effect only once.
- **R62** (behaviour). Successful replay returns the original response even after the
  resource changes/is cancelled; makes no further state changes.
- **R63** (behaviour/order). Idempotency order: parse body as JSON object, authenticate,
  resolve idempotency key (already-claimed key wins) — all before endpoint field validation
  or current-resource checks. An invalid body reusing a successful key still returns 409
  `idempotency_key_reuse`.

## H. API (spec §8)

- **R64** (interface). `GET /me` → `{user_id, display_name, handle, balance, currency,
  minor_units}`.
- **R65** (interface). `POST /payments` (idempotent, key required) body
  `{to_handle, amount, note?, visibility?}`; `note` default `""`, `visibility` default
  `"public"` → 201 payment object (`payment_id, from_user_id, from_handle, to_user_id,
  to_handle, amount, currency, note, visibility, request_id:null, created_at`).
- **R66** (error) table for `POST /payments`: balance < amount → 409 `insufficient_funds`;
  amount <1, >1e9, non-integer → 422 `validation_failed`; `to_handle` = caller's own → 422
  `self_payment`; `note` >200 chars → 422 `validation_failed`; `visibility` invalid → 422
  `validation_failed`; unknown handle → 404 `not_found`.
- **R67** (behaviour). Debit and credit are one atomic step; never visible in one wallet and
  not the other; failed payment leaves no trace in either.
- **R68** (behaviour). `note` stored/returned verbatim: no trimming/escaping/normalisation;
  unicode/emoji survive byte for byte.
- **R69** (interface). `POST /requests` (idempotent) body `{payer_handle, amount, note?}`;
  caller is requester → 201 request object (`request_id, requester_id, requester_handle,
  payer_id, payer_handle, amount, currency, note, status:"pending", payment_id:null,
  created_at`).
- **R70** (error) table: amount invalid → 422 `validation_failed`; `payer_handle` = caller's
  own → 422 `self_request`; note >200 → 422 `validation_failed`; unknown handle → 404
  `not_found`.
- **R71** (behaviour). Payer's balance not checked at request creation; over-balance request
  created normally, stays pending.
- **R72** (interface). `POST /requests/{id}/pay` (idempotent, payer only) body
  `{visibility?}` default `"public"`; replay must send identical body (`{}` ≠
  `{"visibility":"public"}` for idempotency purposes) → 201 payment object with
  `request_id` set; request becomes `paid` with new `payment_id`.
- **R73** (error) table: request not pending → 409 `request_not_pending`; payer balance <
  amount → 409 `insufficient_funds`; caller not payer → 403 `forbidden`; unknown request →
  404 `not_found`.
- **R74** (behaviour). Replaying a successful pay → 200 with original payment body, even if
  request already `paid`; moves no additional money; must not return 409
  `request_not_pending`.
- **R75** (interface). `POST /requests/{id}/decline` (payer only, no idempotency key) → 200
  request with `status:"declined"`. Declining an already-declined request → 200 current
  state (not an error). `paid`/`cancelled` → 409 `request_not_pending`. Not payer → 403
  `forbidden`.
- **R76** (interface). `POST /requests/{id}/cancel` (requester only, no idempotency key) →
  200 request `status:"cancelled"`. Already-cancelled → 200. `paid`/`declined` → 409
  `request_not_pending`. Not requester → 403 `forbidden`.
- **R77** (interface). `GET /requests?direction=&status=&limit=&offset=` → requests where
  caller is requester or payer, newest first by `created_at`. `direction`:
  incoming(caller=payer)/outgoing(caller=requester)/absent=both. `status`: one of four or
  absent=all. `limit` default 50 range 1-200; `offset` default 0, ≥0; out of range → 422;
  unknown direction/status value → 422. Response `{requests:[...], has_more}`.
- **R78** (interface). `POST /splits` (idempotent) body `{amount, participant_handles[],
  note?}`. Caller may be included or omitted in participants. Shares follow §9 rounding, in
  given order. A request created for every participant except caller, caller as requester →
  201 `{split_id, amount, currency, note, shares[{handle,amount}], requests[...],
  created_at}`. `shares` covers every participant incl. caller in given order, sums to
  amount. `requests` covers every participant except caller, same order.
- **R79** (error) table for splits: amount invalid → 422; `participant_handles` empty or
  duplicate → 422 `validation_failed`; note >200 → 422; unknown handle → 404 `not_found`.
- **R80** (behaviour). Split with only caller as participant is valid: one share, zero
  requests, `"requests": []`. No balance checks in splits.
- **R81** (interface). `GET /activity?limit=&offset=` → payments visible per feed contract,
  newest first by `created_at`, `{payments:[...], has_more}`. Same-second order unspecified;
  stable pagination under concurrent writes not required. `limit`/`offset` as in
  `GET /requests`.

## I. Money and rounding (spec §9)

- **R82** (limit/behaviour). Shares are whole minor units, sum exactly to `amount`, differ by
  at most one minor unit. Remainder distributed to first participants in given order (larger
  shares first). Table: 1000/3→334,333,333; 1/3→1,0,0; 10/3→4,3,3; 999/3→333,333,333;
  5/5→1,1,1,1,1.
- **R83** (behaviour). Reordering `participant_handles` changes who gets the extra unit. A
  share of `0` is legal and still produces a request.
- **R84** (invariant). Each split's shares independent of previous splits; after any number
  of splits paid in full, balances still sum exactly to seeded total.

## J. Export and import (spec §10)

- **R85** (interface). `GET /_test/export` → 200
  `{track:"pocketful", format_version:1, state:{...opaque...}}`, unauthenticated.
- **R86** (interface). `POST /_test/import` takes that whole object, atomically replaces
  state, returns 204. Must accept an unchanged export from this service. No dependency on
  source process/files/volume/port/network address.
- **R87** (behaviour). Import is replacement not merge; repeating restores exported state
  without duplication.
- **R88** (error). Invalid JSON → per §5 rules; missing fields, wrong track/version, invalid
  state → 422 `validation_failed`, no change to destination.
- **R89** (limit). Test control calls (`reset`, `export`, `import`) have a 10 s timeout.
- **R90** (behaviour). Export is an atomic read-only snapshot; subsequent source writes do
  not change an already-taken export.
- **R91** (behaviour). Preserve on export/import: accounts and hashed-password login,
  existing bearer tokens, currency, balances, payments, requests, permissions, all completed
  idempotent request bodies and original responses. Identities/timestamps/monetary records
  not regenerated or replayed against an already-net balance. Failed request keys remain
  reusable. Existing receipts/tokens/retries remain valid after import.
- **R92** (behaviour). Import removes all previous destination data and credentials
  (replacement semantics).
- **R93** (behaviour). Reset clears all state including imported state. State need not
  survive abrupt container restart.

## K. Atomic net settlements (spec §11)

- **R94** (behaviour). Reset fixture may include `settlement_operator_ids` (array of user
  ids, default `[]`). An operator may execute a settlement across any wallets; this does not
  grant access to another user's requests or private activity items.
- **R95** (interface). `POST /settlements` requires operator + idempotency key. No token →
  401. Authenticated non-operator → 403 `forbidden`. Body `{transfers:[{from_handle,
  to_handle, amount}, ...]}`, 1..32 entries. Each transfer uses ordinary payment
  amount/note/visibility rules (default empty note, public).
- **R96** (error/order). Unknown handle → 404; self-transfer → 422 `self_payment`; malformed
  batch shape → 422 `validation_failed`. Entry errors take precedence, evaluated in input
  order, before insufficient-funds check. Unknown fields ignored.
- **R97** (invariant). Settlement affordable iff every wallet's balance after all incoming
  and outgoing transfers in the batch is nonnegative (collective, not pairwise sequential,
  check). Insufficient collective funds → 409 `insufficient_funds`.
- **R98** (invariant/all-or-nothing). Either all movements commit together or none do; failed
  validation claims no idempotency key and creates no payment or revision.
- **R99** (interface). Success → 201 `{settlement_id, committed_at, payments:[...]}` in input
  order. Every member is an ordinary payment with `settlement_id` set; nonmembers expose
  `null` for that field. Members have `request_id:null` and the same server-assigned
  `created_at` equal to `committed_at`.
- **R100** (behaviour). Constituent payments follow ordinary activity-feed visibility rules.
  Settlement response contains every member's receipt. Replays → 200 original complete
  response (5th idempotent write path).
- **R101** (behaviour). Reset/import must preserve settlement operator permissions, original
  payments, requests, settlement membership and retry responses.

## Assumptions

- **A1**. "Pebble" branding has no UI surface in stage 1 (API-only per §2: "Only the HTTP API
  is required"); the name is recorded here for stage 2+ reuse and does not affect stage-1
  responses or identifiers. Reasoning: spec explicitly scopes stage 1 to the HTTP API with no
  UI requirement.
- **A2**. "Entry errors take precedence in input order, before insufficient funds" (§11) is
  read as: validate every transfer entry (unknown handle, self-transfer, malformed shape) in
  array order first, raising on the first entry that fails any of those checks, and only once
  every entry passes those per-entry checks is the collective affordability check run.
  Reasoning: directly stated order; "in input order" naturally scopes to the per-entry
  validation pass.
- **A3**. IDs: implementation will prefix resource ids (`u_`, `p_`, `rq_`, `sp_`, `st_`) and
  use opaque random/sequential tokens ≤64 chars, matching the fixture's own id style.
  Reasoning: spec leaves "their format is yours" (§3.4) and fixture examples use this style.
- **A4**. `display_name` has no stated validation rule in stage 1; any string (including
  empty) is accepted. Reasoning: spec never lists a constraint for it, unlike `password` and
  `email`.
- **A5**. Settlement `transfers` entries without `note`/`visibility` use the same defaults as
  `POST /payments` (`""`/`"public"`); an explicit `note`/`visibility` per transfer follows the
  same field-level validation (R41) as a normal payment, with `malformed_request`/`validation_failed`
  precedence identical to §5. Reasoning: §11 says "Each uses ordinary payment amount, note and
  visibility rules."
