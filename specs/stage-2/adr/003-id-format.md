# ADR-003: id format

## Context

R16: IDs are opaque strings, at most 64 characters, "their format is yours." Fixtures use
prefixed ids (`u_ada`, `p_1`, `rq_1`).

## Decision

Server-generated ids use a short type prefix plus a random urlsafe token:
`u_<8 hex>`, `p_<8 hex>`, `rq_<8 hex>`, `sp_<8 hex>`, `st_<8 hex>`, from
`secrets.token_hex(8)` (16 hex chars + 2-3 char prefix, well under 64). Seeded ids from the
fixture are kept verbatim (R20, R34) and never regenerated.

## Reasoning

- Readable in logs/tests, collision-safe (`secrets.token_hex` is CSPRNG-backed), fits the
  64-char ceiling with large margin, and matches the fixture's own convention so mixed
  seeded/generated ids look consistent.

## Consequences

- Tokens (bearer auth) use a separate, longer `secrets.token_urlsafe(32)` with no prefix,
  since they are credentials, not resource ids, and must not be guessable.
