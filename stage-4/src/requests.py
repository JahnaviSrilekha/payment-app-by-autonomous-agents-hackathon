"""Money requests: create, pay, decline, cancel, list (spec section 8; R24, R25, R28,
R69-R77). The idempotent paths (POST /requests, POST /requests/{id}/pay) run through
server.run_idempotent; decline/cancel are plain authenticated writes.
"""

import errors
import ids
import payments
import state as state_mod

STATUSES = ("pending", "paid", "declined", "cancelled")


def request_response(service, request):
    requester = service["users"][request["requester_id"]]
    payer = service["users"][request["payer_id"]]
    return {
        "request_id": request["id"],
        "requester_id": request["requester_id"],
        "requester_handle": requester["handle"],
        "payer_id": request["payer_id"],
        "payer_handle": payer["handle"],
        "amount": request["amount"],
        "currency": request["currency"],
        "note": request["note"],
        "status": request["status"],
        "payment_id": request["payment_id"],
        "created_at": request["created_at"],
    }


def _create_request(service, requester_id, payer_handle, amount, note):
    """Insert one pending request. Caller holds STATE_LOCK; balance is never checked
    here (R71)."""
    requester = service["users"][requester_id]
    request = {
        "id": ids.new_id("rq"),
        "requester_id": requester_id,
        "payer_id": service["handles"][payer_handle],
        "amount": amount,
        "currency": service["currency"],
        "note": note,
        "status": "pending",
        "payment_id": None,
        "created_at": state_mod.now_rfc3339(),
        "seq": state_mod.next_seq(service),
    }
    service["requests"][request["id"]] = request
    service["request_order"].append(request["id"])
    return request


def create_request(ctx, user, service):
    body = ctx.parsed
    if "amount" not in body:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(body["amount"])
    state_mod.check_amount_range(amount)
    payer_handle = state_mod.get_string(body, "payer_handle")
    note = state_mod.get_note(body)
    if payer_handle == user["handle"]:
        raise errors.self_request()
    if payer_handle not in service["handles"]:
        raise errors.not_found("no user has that handle")
    request = _create_request(service, user["id"], payer_handle, amount, note)
    return 201, request_response(service, request)


def pay_request(ctx, user, service):
    """POST /requests/{id}/pay — the payer only, idempotent. Visibility is the payer's
    choice made when the money moves."""
    body = ctx.parsed
    visibility = state_mod.get_visibility(body)
    request = service["requests"].get(ctx.params.get("id"))
    if request is None:
        raise errors.not_found("no such request")
    if request["payer_id"] != user["id"]:
        raise errors.forbidden("only the payer may pay this request")
    if request["status"] != "pending":
        raise errors.request_not_pending()
    payer = service["users"][request["payer_id"]]
    # R149: paying a request is evaluated against available (holds are unspendable).
    if state_mod.available(payer["id"], service,
                           state_mod.now_utc()) < request["amount"]:
        raise errors.insufficient_funds()
    payment = payments.append_payment(
        service,
        from_user_id=request["payer_id"],
        to_user_id=request["requester_id"],
        amount=request["amount"],
        note=request["note"],
        visibility=visibility,
        request_id=request["id"],
    )
    request["status"] = "paid"
    request["payment_id"] = payment["id"]
    return 201, payments.payment_response(service, payment)


def decline_request(ctx, user, service):
    request = service["requests"].get(ctx.params.get("id"))
    if request is None:
        raise errors.not_found("no such request")
    if request["payer_id"] != user["id"]:
        raise errors.forbidden("only the payer may decline this request")
    if request["status"] == "declined":
        return 200, request_response(service, request)  # declining twice is not an error
    if request["status"] != "pending":
        raise errors.request_not_pending()
    request["status"] = "declined"
    return 200, request_response(service, request)


def cancel_request(ctx, user, service):
    request = service["requests"].get(ctx.params.get("id"))
    if request is None:
        raise errors.not_found("no such request")
    if request["requester_id"] != user["id"]:
        raise errors.forbidden("only the requester may cancel this request")
    if request["status"] == "cancelled":
        return 200, request_response(service, request)  # cancelling twice is not an error
    if request["status"] != "pending":
        raise errors.request_not_pending()
    request["status"] = "cancelled"
    return 200, request_response(service, request)


def list_requests(ctx, user, service):
    limit = state_mod.parse_limit(ctx.query)
    offset = state_mod.parse_offset(ctx.query)
    direction = ctx.query.get("direction", [None])[0]
    status = ctx.query.get("status", [None])[0]
    if direction is not None and direction not in ("incoming", "outgoing"):
        raise errors.validation_failed("direction must be incoming or outgoing")
    if status is not None and status not in STATUSES:
        raise errors.validation_failed("unknown status value")
    mine = []
    for request_id in reversed(service["request_order"]):  # newest first
        request = service["requests"][request_id]
        if request["requester_id"] != user["id"] and request["payer_id"] != user["id"]:
            continue
        if direction == "incoming" and request["payer_id"] != user["id"]:
            continue
        if direction == "outgoing" and request["requester_id"] != user["id"]:
            continue
        if status is not None and request["status"] != status:
            continue
        mine.append(request)
    page = mine[offset:offset + limit]
    return 200, {
        "requests": [request_response(service, r) for r in page],
        "has_more": len(mine) > offset + limit,
    }