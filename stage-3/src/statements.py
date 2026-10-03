"""GET /statement: the caller's money movements over a half-open window (stage-3
spec section C; R205-R213, design section 21). Entries are built once for the full
window and paginated by slicing, so an entry's balance_after and the window's
opening/closing balances never depend on limit/offset (R212). Every read runs while
holding STATE_LOCK, like every other read.

Only payments the caller sent or received appear — the activity feed's visibility
rules do not apply here (R213). Holds are not money movements: authorization,
release and expiry never produce statement entries (R288, batch 5's R289 covers
captures appearing exactly once as ordinary payments).
"""

from datetime import datetime, timezone

import errors
import ids
import ledger
import payments
import state as state_mod

# A14: "opening of the wallet" is an unbounded lower bound — the instant just before
# anything could have moved, so the strict-before opening call yields base_balance.
MIN_INSTANT = datetime.min.replace(tzinfo=timezone.utc)


def _instant(query, name):
    """Optional RFC 3339 instant with an offset; anything else — a naive local time,
    a bare date, an empty value — is 422 (R199's rule, R252)."""
    values = query.get(name)
    if not values:
        return None
    try:
        return state_mod.parse_rfc3339(values[0])
    except ValueError:
        raise errors.validation_failed(
            "%s must be an RFC 3339 timestamp" % name)


def build_statement(service, user, from_instant, to_instant, known_at):
    """Full-window computation (design section 21): entries ordered by selected
    effective_at then payment id (R209, A17), each with the caller's balance
    immediately after it (R207); opening/closing are the balances immediately
    before from/to (R210, strict_before), so opening + sum(delta) == closing by
    construction (R211)."""
    opening = ledger.balance_view(
        user, from_instant if from_instant is not None else MIN_INSTANT,
        known_at, strict_before=True, service=service)
    closing = ledger.balance_view(user, to_instant, known_at, strict_before=True,
                                  service=service)
    rows = []
    for p in ledger.payments_touching(service, user["id"]):
        revision = ledger.select_revision(p, known_at)
        if revision is None:
            continue
        effective_at = state_mod.parse_rfc3339(revision["effective_at"])
        if from_instant is not None and effective_at < from_instant:
            continue
        if effective_at >= to_instant:
            continue
        rows.append((effective_at, p["id"], p, revision))
    rows.sort(key=lambda r: (r[0], r[1]))
    entries = []
    running = opening
    for _effective_at, _pid, p, revision in rows:
        delta = ledger.signed_amount(p, user["id"], revision)
        running += delta
        entry_payment = payments.payment_response(service, p)
        # R256 groundwork (batch 5's corrections make it visible): the entry overlays
        # the selected revision's amount on a copy — the stored payment never changes.
        entry_payment["amount"] = revision["amount"]
        entries.append({
            "payment": entry_payment,
            "delta": delta,
            "balance_after": running,
            "revision": revision["revision"],
            "effective_at": revision["effective_at"],
            "recorded_at": revision["recorded_at"],
        })
    return entries, opening, closing


def statement(ctx, user, service):
    """GET /statement. The clock is read once at the start of the request (A16):
    the default `to` (R205) and the default known_at (R248) are the same
    request-start instant, so every money field in one response is consistent.

    A call not given a `snapshot` computes the full window fresh and freezes it
    under a new opaque token (R260-R261, A19); a call given one pages the stored
    result — only limit/offset may accompany it (R263, checked before the token is
    resolved, A15) — so later payments, corrections and lifecycle actions can never
    change a snapshotted answer (R262, R266, R268, R290)."""
    now = state_mod.now_utc()
    if "snapshot" in ctx.query:
        # R263 (A15): query-combination validation before token resolution (404).
        for name in ("from", "to", "known_at"):
            if ctx.query.get(name):
                raise errors.validation_failed(
                    "%s may not accompany a snapshot" % name)
        record = service["statement_snapshots"].get(ctx.query["snapshot"][0])
        if record is None or record["user_id"] != user["id"]:
            # R264: unknown, another user's, or a pre-reset token — indistinguishable.
            raise errors.not_found("no such statement snapshot")
        limit = state_mod.parse_limit(ctx.query)
        offset = state_mod.parse_offset(ctx.query)
        entries = record["entries"]
        return 200, {
            "opening_balance": record["opening_balance"],
            "entries": entries[offset:offset + limit],
            "closing_balance": record["closing_balance"],
            "has_more": offset + limit < len(entries),
        }
    from_instant = _instant(ctx.query, "from")
    to_instant = _instant(ctx.query, "to")
    known_at = _instant(ctx.query, "known_at")
    if to_instant is None:
        to_instant = now
    if known_at is None:
        known_at = now  # R248: everything known when the read begins
    limit = state_mod.parse_limit(ctx.query)
    offset = state_mod.parse_offset(ctx.query)
    entries, opening, closing = build_statement(service, user, from_instant,
                                                to_instant, known_at)
    token = ids.new_id("snap")  # A19: opaque, server-generated (ADR-003 scheme)
    # R261: the snapshot freezes selected revisions, window, balances and entries.
    # The instants are stored as RFC 3339 strings — the record never leaves memory
    # (R265: it is not exported), but staying JSON-clean keeps every consumer safe.
    now_string = now.isoformat(timespec="seconds")
    service["statement_snapshots"][token] = {
        "user_id": user["id"],
        "entries": entries,
        "opening_balance": opening,
        "closing_balance": closing,
        "from_used": ctx.query.get("from", [None])[0],
        "to_used": ctx.query.get("to", [None])[0] or now_string,
        "known_at_used": ctx.query.get("known_at", [None])[0] or now_string,
    }
    return 200, {
        "opening_balance": opening,
        "entries": entries[offset:offset + limit],
        "closing_balance": closing,
        "has_more": len(entries) > offset + limit,
        "snapshot": token,
    }