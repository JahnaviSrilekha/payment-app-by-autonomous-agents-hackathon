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
        "payment_ids": [],
        "seq": state_mod.next_seq(service),
    }
    service["authorizations"][authz["id"]] = authz
    service["authorization_order"].append(authz["id"])
    return 201, authorization_response(service, authz,
                                       state_mod.parse_rfc3339(created_at))
