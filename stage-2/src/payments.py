"""Payments and the activity feed (spec sections 4, 8; R23, R26, R27, R30, R31,
R65-R68, R81). Every handler runs while holding STATE_LOCK, after authentication and
idempotency-key resolution (server.run_idempotent for POST /payments).
"""

import errors
import ids
import state as state_mod


def payment_response(service, payment):
    sender = service["users"][payment["from_user_id"]]
    receiver = service["users"][payment["to_user_id"]]
    return {
        "payment_id": payment["id"],
        "from_user_id": payment["from_user_id"],
        "from_handle": sender["handle"],
        "to_user_id": payment["to_user_id"],
        "to_handle": receiver["handle"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "note": payment["note"],
        "visibility": payment["visibility"],
        "request_id": payment["request_id"],
        "authorization_id": payment.get("authorization_id"),
        "settlement_id": payment["settlement_id"],
        "created_at": payment["created_at"],
    }


def append_payment(service, from_user_id, to_user_id, amount, note, visibility,
                   request_id=None, settlement_id=None, created_at=None,
                   authorization_id=None):
    """One atomic debit+credit inside the caller's STATE_LOCK acquisition: the ledger
    append and both balance mutations commit together or not at all (R23, R67).
    authorization_id is set only on capture-created payments (R167); request_id and
    authorization_id are never both non-null (A9)."""
    if created_at is None:
        created_at = state_mod.now_rfc3339()
    payment = {
        "id": ids.new_id("p"),
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "amount": amount,
        "currency": service["currency"],
        "note": note,
        "visibility": visibility,
        "request_id": request_id,
        "authorization_id": authorization_id,
        "settlement_id": settlement_id,
        "created_at": created_at,
        "seq": state_mod.next_seq(service),
    }
    sender = service["users"][from_user_id]
    receiver = service["users"][to_user_id]
    sender["balance"] -= amount
    receiver["balance"] += amount
    service["payments"].append(payment)
    return payment


def create_payment(ctx, user, service):
    """POST /payments. Field validation runs here, after idempotency-key resolution
    (design.md section 5 step 6)."""
    body = ctx.parsed
    if "amount" not in body:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(body["amount"])
    state_mod.check_amount_range(amount)
    to_handle = state_mod.get_string(body, "to_handle")
    note = state_mod.get_note(body)
    visibility = state_mod.get_visibility(body)
    if to_handle == user["handle"]:
        raise errors.self_payment()
    if to_handle not in service["handles"]:
        raise errors.not_found("no user has that handle")
    sender = service["users"][user["id"]]
    if sender["balance"] < amount:
        raise errors.insufficient_funds()
    to_user_id = service["handles"][to_handle]
    payment = append_payment(service, user["id"], to_user_id, amount, note, visibility)
    return 201, payment_response(service, payment)


def visible_to(payment, user_id):
    """The feed contract (section 4): public, or the caller is the sender or receiver."""
    return payment["visibility"] == "public" or payment["from_user_id"] == user_id \
        or payment["to_user_id"] == user_id


def activity(ctx, user, service):
    limit = state_mod.parse_limit(ctx.query)
    offset = state_mod.parse_offset(ctx.query)
    visible = [p for p in service["payments"] if visible_to(p, user["id"])]
    visible.reverse()  # newest first (append order == creation order)
    page = visible[offset:offset + limit]
    return 200, {
        "payments": [payment_response(service, p) for p in page],
        "has_more": len(visible) > offset + limit,
    }