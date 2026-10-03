"""The one shared revision-ledger balance function (stage-3 design section 18, ADR-006).

Every current and historical money read (`GET /me`'s balance, `GET /statement`'s
opening/closing/balance_after, the correction handler's historical-overdraft sweep) is
computed by `balance_view` from the append-only `revisions` list each payment carries.
The live, incrementally maintained `User.balance` stays the fast path for the current,
fully-known view and for every affordability check; `balance_view` is used only when
`as_of`/`known_at` is supplied or for boundary sweeps, and equals it by construction at
(now, now) because every write path updates both.

These functions are pure: they read stored state plus the two injected instants
(`as_of`, `known_at`) and never read the wall clock (design section 18). An instant of
None means unbounded: `known_at` None is "everything known when the read begins" (R248),
`as_of` None is "no upper filter".
"""

import state as state_mod


def initial_revision(amount, created_at):
    """Revision 1 of every payment: the original amount, effective and recorded at the
    payment's created_at, empty reason (R214, R215, R271 — a settlement member's
    committed_at is its created_at, so revision 1 carries it on both axes)."""
    return {
        "revision": 1,
        "amount": amount,
        "effective_at": created_at,
        "recorded_at": created_at,
        "reason": "",
    }


def revisions_of(payment):
    """The payment's revision history. Payments always store one; a row without the
    field (a stage-2-shaped row) is treated as its implicit revision 1, exactly as
    import materializes it (design section 23)."""
    stored = payment.get("revisions")
    if stored:
        return stored
    return [initial_revision(payment["amount"], payment["created_at"])]


def refunded_total(service, payment_id):
    """A22/R300: the cumulative refunded amount of one payment — the sum of the
    `amount` field (refunds are immutable, R307, so a refund payment's creation
    amount is its permanent amount — never a revision lookup) over every payment
    whose `refund_of` names payment_id."""
    return sum(p["amount"] for p in service["payments"]
               if p.get("refund_of") == payment_id)


def payments_touching(service, user_id):
    """Payments the user sent or received — no visibility filter (R213): the activity
    feed's visibility rules do not apply to money views."""
    return [p for p in service["payments"]
            if p["from_user_id"] == user_id or p["to_user_id"] == user_id]


def select_revision(payment, known_at):
    """Latest revision recorded at or before known_at, or None if none was yet recorded
    (R247). known_at None means everything known when the read begins (R248). Revisions
    are stored recorded_at-ascending (R230), so the last candidate is the latest."""
    if known_at is None:
        revisions = revisions_of(payment)
        return revisions[-1] if revisions else None
    selected = None
    for revision in revisions_of(payment):
        if state_mod.parse_rfc3339(revision["recorded_at"]) <= known_at:
            selected = revision
    return selected


def signed_amount(payment, user_id, revision):
    """The payment's signed effect on user_id's balance for the selected revision:
    positive for the receiver, negative otherwise (design section 18)."""
    return +revision["amount"] if payment["to_user_id"] == user_id \
        else -revision["amount"]


def balance_view(user, as_of, known_at, strict_before=False, service=None):
    """The user's balance as it stood at as_of, under revisions known at known_at.

    Starts from `base_balance` — the opening, set once at reset/import (R216, R217) —
    and adds every payment of theirs whose selected revision's effective_at is at or
    before as_of (a payment at exactly as_of counts as happened, R201), or strictly
    before as_of with strict_before=True (R210's "immediately before from/to").
    `as_of`/`known_at` None are unbounded (see module docstring). Pure: stored state
    plus the two injected instants, no wall-clock read."""
    if service is None:
        service = state_mod.get()
    total = user.get("base_balance", 0)
    for payment in payments_touching(service, user["id"]):
        revision = select_revision(payment, known_at)
        if revision is None:
            continue
        effective_at = state_mod.parse_rfc3339(revision["effective_at"])
        if as_of is None or (effective_at < as_of if strict_before
                             else effective_at <= as_of):
            total += signed_amount(payment, user["id"], revision)
    return total