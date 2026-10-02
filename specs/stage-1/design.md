# Pocketful — Stage 1 design

## 1. Architecture

Single Python 3.12 process, stdlib only (`http.server.ThreadingHTTPServer`, `socketserver`,
`hashlib`, `secrets`, `json`, `threading`). One container, `-e PORT` read from the
environment. See [ADR-001](adr/001-language-and-server.md).

```
request -> thread (ThreadingHTTPServer, request_queue_size=256)
         -> read + JSON-parse body (no shared state touched)
         -> acquire STATE_LOCK (single global threading.Lock)
              -> authenticate (bearer token lookup)
              -> resolve idempotency key (for the 5 idempotent paths)
              -> validate fields / current-resource checks
              -> mutate in-memory state (atomic commit)
              -> capture response to return, and (on 2xx) to store under the key
         -> release STATE_LOCK
         -> write HTTP response
```

Password hashing (`hashlib.scrypt`) is the only expensive per-request operation and is
**never** performed while `STATE_LOCK` is held — see §3.

## 2. Data model (in-memory, guarded by `STATE_LOCK`)

```
Service:
  currency: str
  minor_units: int
  users: dict[user_id -> User]
  handles: dict[handle -> user_id]          # uniqueness index
  emails: dict[email -> user_id]             # uniqueness index
  tokens: dict[token -> user_id]             # bearer tokens, never expire
  settlement_operator_ids: set[user_id]
  payments: list[Payment]                    # append-only ledger, insertion order
  requests: dict[request_id -> Request]      # mutable status, insertion order preserved separately
  request_order: list[request_id]
  idempotency: dict[(user_id, method, path, key) -> IdempotencyRecord]
  next_seq: int                              # monotonic counter for id suffixes and tie-breaking

User:    id, email, password_hash (scrypt), display_name, handle, balance (int)
Payment: id, from_user_id, to_user_id, amount, currency, note, visibility,
         request_id (nullable), settlement_id (nullable), created_at (RFC3339), seq (int)
Request: id, requester_id, payer_id, amount, currency, note, status, payment_id (nullable),
         created_at, seq
IdempotencyRecord: key, user_id, method, path, body (parsed JSON), response_status,
                   response_body
```

`balance` is **not** a stored field recomputed from the ledger; it is maintained as a live
integer per user, mutated only inside `STATE_LOCK` alongside the ledger append, so a single
commit updates both the ledger entry and the two wallets atomically (R1, R2, R23, R67).

## 3. Concurrency and the critical section

`STATE_LOCK` is a single `threading.Lock`. Every handler that reads or writes service state
acquires it for the full duration of that read/write and releases it before writing the HTTP
response. Because the lock fully serializes state access, **the lock itself is the
idempotency-key claim**: there is no separate "claimed" marker. The thread holding the lock
is the only thread that can observe or create a record for a given `(user, method, path,
key)`; a concurrent identical request simply blocks on the lock and, once admitted, finds
either the first thread's stored 2xx result (→ replays it as 200) or no record at all if the
first thread's attempt failed validation (→ proceeds as a first use). This directly satisfies
R61 and R59's "failed request frees the key" rule — a failed (4xx) attempt never writes an
`IdempotencyRecord`, so there is nothing to find.

Order inside the lock, exactly as required (R63): parse (done before acquiring the lock, it
touches no shared state) → authenticate the bearer token → resolve the idempotency key
(for the 5 idempotent paths) → validate fields / current-resource state → commit.

**Password hashing exception.** `POST /auth/signup` and `POST /auth/login` do not hold
`STATE_LOCK` while hashing:
- **Signup:** hash the password first (no lock). Then acquire the lock, check
  email/handle uniqueness, insert the user and token, release the lock. If the handle
  turns out to be taken, the hash is simply discarded — hashing is pure and has no
  observable side effect outside the lock.
- **Login:** acquire the lock only to copy out the user's stored hash (or `None` if the
  email is unknown), release the lock, verify the password outside the lock (constant-time
  compare via `hmac.compare_digest` after re-deriving the scrypt hash), then acquire the
  lock again only to mint and store a new token.

Signup/login have no idempotency key (they are not among the five idempotent paths), so no
replay semantics apply to them; concurrent signups with the same email are serialized by the
lock the same way the uniqueness indices (`emails`, `handles`) are checked-and-inserted
atomically, so two concurrent signups for the same email can never both succeed (R48).

## 4. Invariant enforcement

| Invariant | Enforced by |
|---|---|
| R1 conservation | Every mutation is a paired debit+credit (or multi-leg settlement) inside one lock acquisition; never a partial update. No endpoint creates or destroys a unit: payments move between existing wallets only. |
| R2 no negative balance, ever | Balance checked and decremented in the same lock acquisition that reads it; `POST /payments`, `POST /requests/{id}/pay` compare `balance >= amount` before mutating; `POST /settlements` computes every wallet's net delta over the whole batch and requires every resulting balance ≥ 0 before applying any leg (R97). |
| R3 / exactly-once | §3's lock-as-claim design; `IdempotencyRecord` written only on 2xx commit. |
| R82-R84 rounding | Single pure function `split_shares(amount, n)` implementing the largest-remainder rule in participant order (see §6); no other code computes shares. |
| R98 all-or-nothing settlement | Settlement validates every transfer entry and the collective affordability check before any wallet is mutated; mutation is a single loop over pre-validated legs with no early return once started. |

## 5. Idempotent-write pipeline (shared by all 5 endpoints)

1. Read request body bytes, parse as JSON. **Only** "does not parse" or "parses but is not
   a JSON object" is checked here → `400 malformed_request` (no lock needed yet). Per-field
   JSON-type checks (e.g. `amount` is a string) are deferred to step 6 — they must not run
   before the idempotency key is resolved, or a replay with a since-mutated/invalid body
   would wrongly short-circuit to 400 instead of 409 (R63; fixed after tester completeness
   review, see requirements.md A6/A7 and the stage-1 follow-up commit).
2. Acquire `STATE_LOCK`.
3. Authenticate: missing/malformed/unknown bearer token → `401 unauthenticated` (this
   precedes the idempotency-key check below — A6).
4. `Idempotency-Key` header absent/empty → `400 missing_idempotency_key`. Present but over
   255 characters → `422 validation_failed` (R44); neither case reads or writes an
   `IdempotencyRecord` (A7 — an over-length key is never "claimed").
5. Look up `IdempotencyRecord` for `(user_id, method, path, key)`.
   - Found, body equals stored body (parsed-JSON equality, key order/whitespace
     irrelevant) → release lock, return the stored response body with status **200**
     (the record always stores the *original* 2xx status, e.g. 201, for step 8/9's first-use
     response, but every replay response is 200 regardless of that stored status).
   - Found, body differs → `409 idempotency_key_reuse`.
   - Not found → continue.
6. Endpoint-specific field validation, run now (not in step 1): wrong JSON type for a field
   with endpoint-specific rules → that endpoint's 422 code; wrong type with no
   endpoint-specific rule → `400 malformed_request`; then the rest of each endpoint's
   422/404/403/409 table.
7. Commit mutation.
8. Store `IdempotencyRecord` with the **original** response status/body (e.g. 201) under the
   key.
9. Release lock. Return the original status/body (the caller that created it gets 201; a
   later replayer gets 200 with the same body per step 5).

A request that fails at step 3, step 4 or step 6 releases the lock without writing a record
(frees the key, R59).

## 6. Rounding — `split_shares(amount, participant_handles)`

```
base = amount // n
remainder = amount % n
shares[i] = base + 1 for i in [0, remainder)   # first `remainder` participants, in given order
shares[i] = base     for i in [remainder, n)
```

Matches the stage-1 table exactly (1000/3 → 334,333,333; 1/3 → 1,0,0; 10/3 → 4,3,3; 999/3 →
333,333,333; 5/5 → 1,1,1,1,1).

## 7. Settlement algorithm (`POST /settlements`)

1. Validate shape: `transfers` is an array of 1..32 objects → else `422 validation_failed`.
2. In input order, validate each entry: unknown `from_handle`/`to_handle` → `404 not_found`;
   `from_handle == to_handle` → `422 self_payment`; entry missing/invalid
   amount/note/visibility → `422 validation_failed` (same per-field rules as `POST
   /payments`, default note `""`, visibility `"public"`). First failing entry wins (A2) —
   this whole pass happens before the affordability check.
3. Compute each wallet's net delta (sum of incoming minus outgoing amounts within the
   batch). For every wallet touched, `current_balance + net_delta >= 0` must hold for all
   of them simultaneously → else `409 insufficient_funds`, nothing changes.
4. Commit: apply every leg as an ordinary payment (debit/credit), each with
   `settlement_id` set to the new id, `request_id: null`, `created_at == committed_at`
   (the same server timestamp for every member).
5. Return `201 {settlement_id, committed_at, payments: [...]}` in input order.

## 8. Export / import format

`format_version: 1`. `state` is the entire `Service` structure from §2 serialized as JSON
(password hashes and tokens included verbatim — exports are private test artifacts per
spec). Import validates `track == "pocketful"` and `format_version == 1`; anything else, or
a structurally invalid `state`, → `422 validation_failed`, destination unchanged. Import
replaces the whole in-memory `Service` object atomically (inside `STATE_LOCK`) — old data
and credentials are fully discarded (R92). Export takes a deep copy while holding the lock
just long enough to snapshot (not to serialize), so export never blocks writers for the
whole JSON-encode duration and sees a point-in-time-consistent state (R90).

This is the version-1 baseline that every later stage's export/import must remain able to
read and produce a superset of — later stages add fields to `state`, never remove or repurpose
stage-1 fields, and bump `format_version`.

## 9. HTTP error pipeline (applies to every endpoint)

1. Unparseable JSON body → `400 malformed_request`.
2. Auth: missing/unknown/malformed bearer token → `401 unauthenticated` (skipped for the 4
   public endpoints).
3. For idempotent endpoints: idempotency-key resolution (§5 steps 4-5).
4. Field-level validation in the order each endpoint's table lists it (wrong JSON type for a
   field with endpoint-specific rules → that endpoint's 422 code, not generic 400; wrong
   type with no endpoint-specific rule → `400 malformed_request`, R41/R43).
5. Resource/permission checks (`404 not_found`, `403 forbidden`, `409` business rules).

## Decision records

- [ADR-001: language, HTTP server and concurrency model](adr/001-language-and-server.md)
- [ADR-002: password hashing](adr/002-password-hashing.md)
- [ADR-003: id format](adr/003-id-format.md)
