"""Atomic net settlements (spec section 11; R94-R101). POST /settlements is an
idempotent write path reserved to operators. Per-entry validation runs in input order
and entirely before the collective affordability check (A2); the batch commits either
all legs or none.
"""

import errors
import ids
import payments
import state as state_mod

MAX_TRANSFERS = 32


def create_settlement(ctx, user, service):
    body = ctx.parsed
    if "transfers" not in body:
        raise errors.validation_failed("transfers is required")
    transfers = body["transfers"]
    if not isinstance(transfers, list):
        raise errors.validation_failed("transfers must be an array")
    if not 1 <= len(transfers) <= MAX_TRANSFERS:
        raise errors.validation_failed("transfers must contain 1 to %d objects" % MAX_TRANSFERS)

    legs = _validate_entries(service, transfers)

    if user["id"] not in service["settlement_operator_ids"]:
        raise errors.forbidden("only settlement operators may submit settlements")

    _check_affordability(service, legs)

    committed_at = state_mod.now_rfc3339()
    settlement_id = ids.new_id("st")
    receipts = []
    for leg in legs:
        payment = payments.append_payment(
            service,
            from_user_id=service["handles"][leg["from_handle"]],
            to_user_id=service["handles"][leg["to_handle"]],
            amount=leg["amount"],
            note=leg["note"],
            visibility=leg["visibility"],
            settlement_id=settlement_id,
            created_at=committed_at,
        )
        receipts.append(payments.payment_response(service, payment))
    return 201, {
        "settlement_id": settlement_id,
        "committed_at": committed_at,
        "payments": receipts,
    }


def _validate_entries(service, transfers):
    """Validate every entry in input order (A2), before any affordability check. Raises
    on the first failing entry: shape/field rules (422 validation_failed), then
    self-transfer (422 self_payment), then unknown handles (404 not_found)."""
    legs = []
    for entry in transfers:
        if not isinstance(entry, dict):
            raise errors.validation_failed("each transfer must be an object")
        from_handle = entry.get("from_handle")
        to_handle = entry.get("to_handle")
        if not isinstance(from_handle, str) or not isinstance(to_handle, str):
            raise errors.validation_failed("from_handle and to_handle are required strings")
        if "amount" not in entry:
            raise errors.validation_failed("amount is required")
        amount = state_mod.parse_amount(entry["amount"])
        state_mod.check_amount_range(amount)
        note = state_mod.get_note(entry)
        visibility = state_mod.get_visibility(entry)
        if from_handle == to_handle:
            raise errors.self_payment()
        if from_handle not in service["handles"] or to_handle not in service["handles"]:
            raise errors.not_found("no user has that handle")
        legs.append({
            "from_handle": from_handle,
            "to_handle": to_handle,
            "amount": amount,
            "note": note,
            "visibility": visibility,
        })
    return legs


def _check_affordability(service, legs):
    """Collective, not pairwise (R97): every wallet's balance after all incoming and
    outgoing transfers of the whole batch must stay nonnegative."""
    deltas = {}
    for leg in legs:
        deltas[leg["from_handle"]] = deltas.get(leg["from_handle"], 0) - leg["amount"]
        deltas[leg["to_handle"]] = deltas.get(leg["to_handle"], 0) + leg["amount"]
    for handle, delta in deltas.items():
        balance = service["users"][service["handles"][handle]]["balance"]
        if balance + delta < 0:
            raise errors.insufficient_funds()