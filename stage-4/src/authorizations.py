"""Authorizations: create, capture, void, list (stage-2 spec sections "Authorizations
and captures"; R143-R146, R149, R161-R191). Create and capture are idempotent write
paths 6 and 7 (design.md section 13); void is a plain authenticated write like
decline/cancel, no idempotency key (R176). Every handler runs while holding STATE_LOCK,
after authentication (and idempotency-key resolution for the two idempotent paths).

`held`/`available`/effective status are never stored: every read is a pure computation
of stored state plus an injected now (design.md section 11, ADR-004). Only `captured`
and `voided` are ever written as stored transitions; "expired" is always computed.
"""

from datetime import timedelta

import errors
import ids
import payments
import state as state_mod

AUTHORIZATION_STATUSES = state_mod.AUTHORIZATION_STATUSES


def authorization_not_open():
    return errors.ApiError(409, "authorization_not_open",
                           "authorization is not open")


def authorization_expired():
    return errors.ApiError(409, "authorization_expired",
                           "authorization has expired")


def capture_exceeds_authorization():
    return errors.ApiError(422, "capture_exceeds_authorization",
                           "capture exceeds the remaining authorized amount")


# --- historical holds: the (as_of, known_at) views (design section 20, R276-R287) ---


def _parse(value):
    return state_mod.parse_rfc3339(value)


def _known_void(auth, known_at):
    """The void event if it is known at known_at (event time == recorded time, R279)."""
    void = auth.get("void")
    if void is not None and _parse(void["event_time"]) <= known_at:
        return void
    return None


def _known_captures(auth, known_at):
    """The capture events known at known_at (event time == recorded time, R279)."""
    return [c for c in (auth.get("captures") or [])
            if _parse(c["event_time"]) <= known_at]


def known_at_for(auth, now):
    """The known_at a live two-argument read uses for this row. For every row a real
    service can hold (reset rejects future created_at; every write path stamps
    creation with the clock) this is exactly `now`. Synthetic rows whose creation
    postdates the injected now (stage-2's minimal test fixtures) clamp to creation,
    so the two-argument forms degenerate to the stage-2 comparisons instead of
    reporting the row "unknown" — the generalization stays exact, not approximate."""
    created_at = auth.get("created_at")
    if created_at is None:
        return now
    created = _parse(created_at)
    return now if created <= now else created


def effective_status_view(auth, as_of, known_at):
    """Generalized lazy-expiry status (R277-R280) as a pure function of the stored row
    plus the injected (as_of, known_at) pair. "unknown": creation not yet known at
    known_at, contributing nothing wherever it is used (R247). A known void or final
    capture closes the hold at its event time; the expiry deadline is known as soon as
    creation is (R279) and takes effect at expires_at (R278) — so a query beyond now
    sees a currently-open hold expired at its deadline (R280). Seeded closed holds
    carry their close as `closed_at` with no synthetic lifecycle records (R287); a
    closed row with no closing record at all is closed at every instant."""
    created_at = auth.get("created_at")
    if created_at is not None and _parse(created_at) > known_at:
        return "unknown"
    void = _known_void(auth, known_at)
    if void is not None and _parse(void["event_time"]) <= as_of:
        return "voided"
    for capture in _known_captures(auth, known_at):
        if capture.get("final") and _parse(capture["event_time"]) <= as_of:
            return "captured"
    if auth["status"] != "open":
        if not auth.get("captures") and not auth.get("void"):
            # A stored-closed row with no lifecycle event records — a seeded closed
            # hold (R287): its prior lifecycle is deliberately not reconstructed, so
            # it reads back closed at every known instant, exactly as stage-2's
            # two-argument form read the stored status.
            return auth["status"]
        closed_at = auth.get("closed_at")
        if closed_at is not None and _parse(closed_at) <= known_at \
                and _parse(closed_at) <= as_of:
            return auth["status"]
    if as_of >= _parse(auth["expires_at"]):
        return "expired"
    return "open"


def closed_at_view(auth, as_of, known_at):
    """R282: null while open at as_of; the capture/void event time once closed by that
    event; the computed expires_at once lazily expired at its deadline (R278, R280).
    Everything is judged against the injected as_of, never the wall clock: a hold that
    is clock-expired at real now but queried at an earlier as_of was open then."""
    created_at = auth.get("created_at")
    if created_at is not None and _parse(created_at) > known_at:
        return None
    void = _known_void(auth, known_at)
    if void is not None and _parse(void["event_time"]) <= as_of:
        return void["event_time"]
    for capture in _known_captures(auth, known_at):
        if capture.get("final") and _parse(capture["event_time"]) <= as_of:
            return capture["event_time"]
    if auth["status"] != "open":
        closed_at = auth.get("closed_at")
        if closed_at is not None and _parse(closed_at) <= known_at \
                and _parse(closed_at) <= as_of:
            return closed_at
    if as_of >= _parse(auth["expires_at"]):
        return auth["expires_at"]
    return None


def remaining_amount_view(auth, as_of, known_at):
    """Amount still held at as_of under revisions known at known_at: zero for anything
    not open, or once a final capture, void or expiry released the remainder (R277);
    otherwise the authorized amount minus the captures that had happened by as_of.
    Seeded partial captures carry no event records (R287) and count from the hold's
    creation. (That a hold holds nothing before it starts is enforced by held_view's
    R277 gate — a live two-argument read never queries before creation.)"""
    if effective_status_view(auth, as_of, known_at) != "open":
        return 0
    known = _known_captures(auth, known_at)
    captured_by_as_of = sum(c["amount"] for c in known if _parse(c["event_time"]) <= as_of)
    # Only captures with NO event record at all are genuinely unrecorded (R287's
    # seeded pre-reset captures); every event-backed capture is gated by its
    # event_time twice — known_at (R279: known at its server-assigned event time)
    # and as_of (R277: it reduces the hold at capture time) — so the unrecorded
    # remainder is computed against ALL recorded captures, not just the known ones
    # (a capture known_at excludes must not leak back in through this term).
    unrecorded = auth.get("captured_amount", 0) - sum(c["amount"]
                                                       for c in (auth.get("captures") or []))
    if unrecorded > 0:
        captured_by_as_of += unrecorded
    return auth["amount"] - captured_by_as_of


def held_view(user_id, service, as_of, known_at):
    """Sum of the user's holds at the injected (as_of, known_at) pair (R276-R280):
    a hold not yet known at known_at contributes nothing (R247), and so does a hold
    that did not exist yet at as_of — a hold starts at creation (R277). Pure: same
    inputs give the same result, no wall-clock read."""
    total = 0
    for a in service["authorizations"].values():
        if a["from_user_id"] != user_id:
            continue
        created_at = a.get("created_at")
        if created_at is not None and as_of < _parse(created_at):
            continue  # R277: the hold had not started at as_of
        total += remaining_amount_view(a, as_of, known_at)
    return total


def effective_status(auth, now):
    """The stage-2 two-argument form, kept as a thin wrapper so every stage-2 call
    site is untouched: effective_status_view at (now, now) — known_at_for clamps
    synthetic rows only (see it); for real service state this is the identity."""
    return effective_status_view(auth, now, known_at_for(auth, now))


def authorization_response(service, authz, now):
    """One authorization payload (R162's create shape) with the fields every
    authorization response carries (R172): remaining_amount and the ordered
    payment_ids. status is the effective (lazy-expiry) status, never the stored one."""
    payer = service["users"][authz["from_user_id"]]
    receiver = service["users"][authz["to_user_id"]]
    return {
        "authorization_id": authz["id"],
        "from_user_id": authz["from_user_id"],
        "from_handle": payer["handle"],
        "to_user_id": authz["to_user_id"],
        "to_handle": receiver["handle"],
        "amount": authz["amount"],
        "captured_amount": authz["captured_amount"],
        "remaining_amount": state_mod.remaining_amount(authz, now),
        "currency": authz["currency"],
        "note": authz["note"],
        "visibility": authz["visibility"],
        "status": state_mod.effective_status(authz, now),
        "expires_at": authz["expires_at"],
        "closed_at": closed_at_view(authz, now, now),
        "payment_id": authz["payment_ids"][-1] if authz["payment_ids"] else None,
        "payment_ids": list(authz["payment_ids"]),
        "created_at": authz["created_at"],
    }


def _get(service, authz_id):
    return service["authorizations"].get(authz_id)


# --- POST /authorizations (idempotent path 6) -----------------------------------


def create_authorization(ctx, user, service):
    """POST /authorizations — the caller is the payer; a hold moves no money (R144).
    Validation order is design.md section 13's canonical order (R163, A8): amount
    shape, note shape, visibility shape, self_payment, unknown handle, then
    insufficient_funds last (it needs a shape-valid amount to compare against)."""
    body = ctx.parsed
    if "amount" not in body:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(body["amount"])
    state_mod.check_amount_range(amount)
    note = state_mod.get_note(body)
    visibility = state_mod.get_visibility(body)
    to_handle = state_mod.get_string(body, "to_handle")
    if to_handle == user["handle"]:
        raise errors.self_payment()
    if to_handle not in service["handles"]:
        raise errors.not_found("no user has that handle")
    now = state_mod.now_utc()
    if state_mod.available(user["id"], service, now) < amount:
        raise errors.insufficient_funds()
    created_at = state_mod.now_rfc3339()
    expires_at = (state_mod.parse_rfc3339(created_at)
                  + timedelta(seconds=service["authorization_ttl_seconds"])
                  ).isoformat(timespec="seconds")
    authz = {
        "id": ids.new_id("a"),
        "from_user_id": user["id"],
        "to_user_id": service["handles"][to_handle],
        "amount": amount,
        "captured_amount": 0,
        "currency": service["currency"],
        "note": note,
        "visibility": visibility,
        "status": "open",
        "expires_at": expires_at,
        "created_at": created_at,
        "captures": [],
        "void": None,
        "closed_at": None,
        "payment_ids": [],
        "seq": state_mod.next_seq(service),
    }
    service["authorizations"][authz["id"]] = authz
    service["authorization_order"].append(authz["id"])
    return 201, authorization_response(service, authz,
                                       state_mod.parse_rfc3339(created_at))


# --- POST /authorizations/{id}/capture (idempotent path 7) -----------------------


def capture_authorization(ctx, user, service):
    """POST /authorizations/{id}/capture — only the receiver may capture (R165).
    Validation order (R175, A8, design.md section 13): 404 exists, 403 receiver,
    409 authorization_expired before the generic 409 authorization_not_open (an
    expired hold never reports the wrong code), then amount shape, then
    capture_exceeds_authorization against the live remainder, last.

    Commit is one critical-section write: append the capture's payment, bump
    captured_amount, and close the authorization if this capture was final or it
    exhausts the remainder — so the payment moving and the remainder releasing are
    observable in the same instant (R168)."""
    authz = _get(service, ctx.params.get("id"))
    if authz is None:
        raise errors.not_found("no such authorization")
    if authz["to_user_id"] != user["id"]:
        raise errors.forbidden("only the receiver may capture this authorization")
    now = state_mod.now_utc()
    if state_mod.effective_status(authz, now) == "expired":
        raise authorization_expired()
    if authz["status"] != "open":
        raise authorization_not_open()
    body = ctx.parsed
    remaining = state_mod.remaining_amount(authz, now)
    if "amount" in body:
        amount = state_mod.parse_amount(body["amount"])
        if amount < 1:
            raise errors.validation_failed("amount must be at least 1")
    else:
        amount = remaining
    if "final" in body and not isinstance(body["final"], bool):
        raise errors.validation_failed("final must be a boolean")
    final = body.get("final", True)
    if amount > remaining:
        raise capture_exceeds_authorization()
    payment = payments.append_payment(
        service,
        from_user_id=authz["from_user_id"],
        to_user_id=authz["to_user_id"],
        amount=amount,
        note=authz["note"],
        visibility=authz["visibility"],
        authorization_id=authz["id"],
    )
    authz["captured_amount"] += amount
    authz["payment_ids"].append(payment["id"])
    # R279: the capture's event time is the payment's created_at — both its effective
    # and its recorded time (the settlement-member convention, R271, on hold events).
    event_time = payment["created_at"]
    authz.setdefault("captures", []).append({
        "payment_id": payment["id"], "amount": amount, "final": bool(final),
        "event_time": event_time,
    })
    if final or authz["captured_amount"] >= authz["amount"]:
        authz["status"] = "captured"
        # R282: closed_at is the closing event's time; null while open.
        authz["closed_at"] = event_time
    return 201, payments.payment_response(service, payment)


# --- POST /authorizations/{id}/void (no key, like decline/cancel) -----------------


def void_authorization(ctx, user, service):
    """POST /authorizations/{id}/void — only the payer may void (R176). Voiding an
    already-voided authorization is 200 with the current state (R177); a captured or
    clock-expired one is 409 authorization_not_open (R178). Voiding a partially
    captured open authorization releases only the remainder and preserves the capture
    records (R173)."""
    authz = _get(service, ctx.params.get("id"))
    if authz is None:
        raise errors.not_found("no such authorization")
    if authz["from_user_id"] != user["id"]:
        raise errors.forbidden("only the payer may void this authorization")
    now = state_mod.now_utc()
    if authz["status"] == "voided":
        return 200, authorization_response(service, authz, now)
    if authz["status"] == "captured" \
            or state_mod.effective_status(authz, now) == "expired":
        raise authorization_not_open()
    authz["status"] = "voided"
    # R279: the void's server-assigned event time, both effective and recorded.
    event_time = state_mod.now_rfc3339()
    authz["void"] = {"event_time": event_time}
    authz["closed_at"] = event_time  # R282
    return 200, authorization_response(service, authz, now)


# --- GET /authorizations ----------------------------------------------------------


def list_authorizations(ctx, user, service):
    """GET /authorizations — only authorizations involving the caller, newest first
    (R180, R181). Filters: direction (caller as payer/receiver), status against the
    effective status so a clock-expired row matches expired, never open (R182);
    limit/offset/has_more exactly as GET /requests (R183)."""
    limit = state_mod.parse_limit(ctx.query)
    offset = state_mod.parse_offset(ctx.query)
    direction = ctx.query.get("direction", [None])[0]
    status = ctx.query.get("status", [None])[0]
    if direction is not None and direction not in ("incoming", "outgoing"):
        raise errors.validation_failed("direction must be incoming or outgoing")
    if status is not None and status not in AUTHORIZATION_STATUSES:
        raise errors.validation_failed("unknown status value")
    now = state_mod.now_utc()
    mine = []
    for authz_id in reversed(service["authorization_order"]):
        authz = service["authorizations"][authz_id]
        if authz["from_user_id"] != user["id"] and authz["to_user_id"] != user["id"]:
            continue  # exclusion, not refusal (R180)
        if direction == "incoming" and authz["to_user_id"] != user["id"]:
            continue
        if direction == "outgoing" and authz["from_user_id"] != user["id"]:
            continue
        if status is not None and state_mod.effective_status(authz, now) != status:
            continue
        mine.append(authz)
    page = mine[offset:offset + limit]
    return 200, {
        "authorizations": [authorization_response(service, a, now) for a in page],
        "has_more": len(mine) > offset + limit,
    }