"""POST /payments/{payment_id}/refunds (idempotent write path 9; R294-R305, R332;
design section 26, ADR-008, A21).

A refund is an ordinary payment in the opposite direction (ADR-008): created through
the same `append_payment` helper as every other payment, with `refund_of` naming the
target. The refund's debtor is the target's original receiver (target["to_user_id"]),
so affordability is checked against their `available` funds, not bare balance (R303).
Error precedence is A21's: payment lookup (404) -> ownership (403) -> amount field
shape (422) -> refund-of-refund (422 invalid_refund_target) -> refund_exceeds_payment
(422) -> insufficient_funds (409). A failing check leaves every balance, record and
idempotency key unchanged; the key itself is claimed by the shared pipeline only on a
commit.
"""

import errors
import ledger
import payments
import state as state_mod


def _find(service, payment_id):
    for payment in service["payments"]:
        if payment["id"] == payment_id:
            return payment
    return None


def create_refund(ctx, user, service):
    """POST /payments/{payment_id}/refunds — design section 26's steps 1-7, after
    idempotency-key resolution (replay/reuse handled by the shared pipeline)."""
    target = _find(service, ctx.params.get("payment_id"))
    if target is None:
        raise errors.not_found("no such payment")  # R296
    if target["to_user_id"] != user["id"]:
        raise errors.forbidden(  # R295: only the original receiver may refund
            "only the original receiver may refund a payment")
    # R299: the same integer-range rule as every other amount field (stage-1 design
    # section 6) — parse_amount for the JSON shape, check_amount_range for 1..MAX.
    if "amount" not in ctx.parsed:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(ctx.parsed["amount"])
    state_mod.check_amount_range(amount)
    # R297/R298/A23: a direct payment, request payment or capture is a valid target
    # with no special check; only a payment that is itself a refund is excluded.
    if target.get("refund_of") is not None:
        raise errors.ApiError(422, "invalid_refund_target",
                              "a refund payment cannot itself be refunded")
    # R300/A22: refunds cumulatively may not exceed the payment's current corrected
    # amount — its latest revision's amount, against the immutable creation amounts
    # of every refund already made against it.
    current = ledger.revisions_of(target)[-1]["amount"]
    if amount + ledger.refunded_total(service, target["id"]) > current:
        raise errors.ApiError(422, "refund_exceeds_payment",
                              "refunds cumulatively exceed the payment's "
                              "current corrected amount")
    # R303: the refunder is the original receiver, giving existing money back, so
    # the debit is theirs and is checked against available (balance minus open
    # holds), like every other debit.
    if state_mod.available(target["to_user_id"], service,
                           state_mod.now_utc()) < amount:
        raise errors.insufficient_funds()
    # R301/R304/R332: commit through the ordinary append_payment with the direction
    # reversed and refund_of set; request_id/authorization_id/settlement_id keep
    # their null defaults, and no request, authorization, hold or settlement
    # membership is touched.
    payment = payments.append_payment(
        service,
        from_user_id=target["to_user_id"],
        to_user_id=target["from_user_id"],
        amount=amount,
        note=target["note"],
        visibility=target["visibility"],
        refund_of=target["id"],
    )
    return 201, payments.payment_response(service, payment)