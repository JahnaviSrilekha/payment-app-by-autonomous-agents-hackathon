# ADR-002: password hashing

## Context

R55 requires passwords stored via bcrypt, scrypt, Argon2 "or an equivalent"; plaintext is
forbidden. No outbound network at runtime, so any non-stdlib dependency must be vendored
into the image at build time via pip (network is available during `docker build`).

## Options

1. `hashlib.scrypt` (stdlib, available since Python 3.6).
2. `bcrypt` PyPI package, installed at build time.
3. A hand-rolled PBKDF2 wrapper via `hashlib.pbkdf2_hmac`.

## Decision

Option 1: `hashlib.scrypt(password, salt=secrets.token_bytes(16), n=2**14, r=8, p=1,
dklen=32)`, with the random salt and parameters stored alongside the derived key per user.
Verification re-derives with the stored salt/parameters and compares with
`hmac.compare_digest`.

## Reasoning

- Satisfies R55 directly: scrypt is explicitly listed as acceptable.
- Avoids a build-time dependency on PyPI availability/mirroring for the judged isolated
  build, lowering build-time risk for no behavioural gain — stays inside the stdlib
  default per the technical direction.
- `n=2**14` keeps a single hash under ~100ms on typical hardware, bounding the
  non-locked portion of signup/login latency well inside the 5 s request timeout even
  under concurrent signups.

## Consequences

- Password hashes are not bcrypt-formatted strings; export/import must carry the salt and
  scrypt parameters per user (not just a hash), so stage 2+ exports include
  `{algorithm: "scrypt", n, r, p, salt, hash}` per user rather than a single opaque
  string — still satisfies R91 (hashed-password login preserved across import) since the
  full verification material round-trips.
