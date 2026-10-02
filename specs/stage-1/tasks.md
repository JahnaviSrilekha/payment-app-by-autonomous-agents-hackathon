# Pocketful — Stage 1 tasks

Deliverable root: `stage-1/` (Dockerfile, RUN.md, `src/`). Layout below is a suggestion, not
a requirement — the developer may restructure within `stage-1/` as long as every file stays
under that stage's folder and the done-tests pass.

Suggested files: `src/server.py` (HTTP routing), `src/state.py` (data model + `STATE_LOCK`),
`src/ids.py`, `src/errors.py`, `src/auth.py`, `src/idempotency.py`, `src/payments.py`,
`src/requests.py`, `src/splits.py`, `src/settlements.py`, `src/testctl.py`.

| id | requirements | files | dependencies | done test | status | batch |
|---|---|---|---|---|---|---|
| T1 | R5,R6,R7,R8,R9,R10,R12-R16,R38,R39,R43-R45 | `src/server.py`, `src/state.py`, `src/ids.py`, `src/errors.py` | none | `GET /health` → 200 within 60s of container start; unknown route/method → 404/405 with the standard error envelope; unit tests for `errors.py` status/code mapping and `ids.py` format (≤64 chars) | pending | 1 |
| T2 | R19,R21,R22,R46-R55,R64 | `src/auth.py` | T1 | Signup/login spec examples reproduced exactly incl. derived handle from email, `409 email_taken`, `409 handle_taken`, `422` short password / bad email shape, `401` wrong password; `GET /me` matches schema; unit test proves `hashlib.scrypt` never called while `STATE_LOCK` is held (e.g. via a lock-contention test: concurrent signups to different emails do not serialize on hashing time) | pending | 1 |
| T3 | R11,R32-R37,R85-R93 | `src/testctl.py` | T1, T2 | `POST /_test/reset` with the spec fixture seeds users/payments/requests exactly; negative-balance fixture → 422, no state change; `GET /_test/export` → `POST /_test/import` round trip preserves tokens/users/payments/requests/idempotency records byte-for-byte (re-run every T2/T5/T6/T8 scenario after an import and confirm replays still work) | pending | 1 |
| T4 | R56-R63 | `src/idempotency.py` | T1 | Unit tests against a stub handler: first use → 201 stored; replay same body → 200 identical body; replay different body → 409 `idempotency_key_reuse`; failed (4xx) attempt stores nothing and a retry with the same key is treated as first use; concurrent identical requests (threaded test, unused key) yield exactly one 201 and the rest 200 with identical bodies | pending | 1 |
| T9 | R5,R6,R7 | `Dockerfile`, `RUN.md` | T1 | `docker build` succeeds with no runtime network reachable afterward; `docker run -e PORT=8080 -p 8080:8080 <image>` becomes healthy within 60s; container works with only the resource limits in R7 | pending | 1 |
| T5 | R23,R26,R27,R30,R31,R56-R63 (via T4),R65-R68,R81 | `src/payments.py` | T1, T2, T3, T4 | Spec's `POST /payments` example reproduced; insufficient funds, self-payment, bad visibility/amount/note each exact code; debit+credit atomicity under 50 concurrent payments from one wallet never drives balance negative (R2) and conserves total (R1); note round-trips unicode/emoji byte-for-byte; `GET /activity` visibility rule (public/sender/receiver only) verified from three callers' viewpoints; full idempotency replay/reuse behavior via T4 | pending | 2 |
| T6 | R24,R25,R28,R69-R77 | `src/requests.py` | T1, T2, T3, T4, T5 | Full request lifecycle: create (no balance check), pay (incl. insufficient-funds 409 leaving state unchanged), decline, cancel, double-decline/double-cancel idempotent-success (200 not error), wrong-caller 403, non-pending 409, replay-after-paid returns 200 original payment and never double-moves money; `GET /requests` direction/status/pagination filters match spec | pending | 2 |
| T7 | R29,R78-R80,R82-R84 | `src/splits.py` | T1, T2, T3, T4, T5, T6 | Every row of the stage-1 rounding table reproduced exactly; caller-only split → one share, `requests: []`; `0`-share participant still gets a request; reordering `participant_handles` moves the extra unit; requests created are visible only to their two parties and not in `GET /activity` | pending | 3 |
| T8 | R94-R101 | `src/settlements.py` | T1, T2, T3, T4, T5, T6 | Non-operator → 403, no token → 401; entry-order precedence (unknown handle / self-transfer / malformed shape before insufficient-funds), proven with a batch whose first bad entry is after a would-be-insufficient one; collective (not pairwise) affordability — a batch legal only when netted is accepted, one illegal when netted per-leg-sequentially is rejected correctly either way per the collective rule; all-or-nothing under a forced failure (no balance/record/idempotency-key change); replay → 200 original complete response; constituent payments carry `settlement_id`, correct `request_id:null`, shared `created_at==committed_at`, ordinary feed visibility | pending | 4 |

## Review batches

- **Batch 1** (`T1,T2,T3,T4,T9`) — builds on: none. Core server scaffold, data model,
  identity/auth, test-control endpoints, the shared idempotency pipeline, and the
  Dockerfile/RUN.md. This is the foundation every later batch needs; it is deliberately the
  only batch that must land first — stage 1's endpoints form a hard dependency chain
  (nothing can authenticate or persist without this batch), so true from-revision-zero
  parallelism is not available here (recorded as a design constraint, not an oversight).
- **Batch 2** (`T5,T6`) — builds on: batch 1. Payments, requests and the activity feed —
  the core money-movement surface, including the shared idempotency pipeline's real-world
  exercise.
- **Batch 3** (`T7`) — builds on: batch 1, batch 2 (splits create requests via the same
  path `T6` builds and read participant wallets via `T1`'s data model).
- **Batch 4** (`T8`) — builds on: batch 1, batch 2 (settlements reuse payment-leg
  creation from `T5` and the idempotency pipeline from `T4`, but nothing from splits).
  **Batch 3 and batch 4 do not depend on each other and can be developed and reviewed in
  parallel once batch 2 is merged.**

## Requirement coverage check

R1-R101 all appear in at least one task's requirement column above. R55/R91 (password
hashing + export fidelity) are cross-cutting and are re-verified in T3's round-trip test as
well as T2.
