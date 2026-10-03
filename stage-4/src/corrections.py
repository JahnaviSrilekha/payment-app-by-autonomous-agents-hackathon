"""POST /payments/{payment_id}/corrections (idempotent write path 8) and
GET /payments/{payment_id}/revisions (stage-3 spec sections D-I; R219-R245, R272,
R275, R283-R285; design section 19, ADR-006). Stage 4 extends the per-item checks
with R307's refund disjunct and R308's refund_exceeds_payment, factored into
validate_item (design section 28 phase 2, ADR-009).

A correction appends an immutable revision to the payment's history and moves only
the difference between the two wallets, in the same critical section (R234). The
original payment, its original idempotent response and the activity feed are never
touched (R241, R242). Error precedence is A15's, extended by A24/A25: payment
lookup (404) -> sender check (403) -> field validation (422) -> structural
immutability (422 linked_payment_immutable, settlement members/captures/refunds)
-> refund_exceeds_payment (422) -> stale_revision (409) -> effective_at not later
than now (422) -> insufficient_funds (409) -> historical_overdraft (409, explicit
spec order, R236-R237/R285/R284).

The historical-overdraft sweep evaluates a tentative world in which the new revision
is already appended: the append happens under STATE_LOCK and is popped again on any
failure, so no concurrent request can ever observe it (R239) and the live balances
are only mutated at commit (R234).
"""

from datetime import timedelta

import authorizations
import errors
import ledger
import state as state_mod

MAX_AMOUNT = state_mod.MAX_AMOUNT
REASON_MAX = 200


def correction_response(payment, revision):
    """R229's success shape."""
    return {
        "payment_id": payment["id"],
        "revision": revision["revision"],
        "amount": revision["amount"],
        "effective_at": revision["effective_at"],
        "recorded_at": revision["recorded_at"],
        "reason": revision["reason"],
    }


def revision_entry(revision):
    """R243's per-revision shape (design section 17's Revision model)."""
    return {
        "revision": revision["revision"],
        "amount": revision["amount"],
        "effective_at": revision["effective_at"],
        "recorded_at": revision["recorded_at"],
        "reason": revision["reason"],
    }


def next_recorded_at(payment):
    """R230: recorded times for one payment strictly increase. The clock is read
    once and truncated to seconds (the stored format's granularity); if that has
    not advanced past the previous revision's recorded_at (same second, clock
    skew), the next whole second after it is used."""
    candidate = state_mod.now_utc().replace(microsecond=0)
    last = state_mod.parse_rfc3339(payment["revisions"][-1]["recorded_at"])
    if candidate <= last:
        candidate = last + timedelta(seconds=1)
    return candidate.isoformat(timespec="seconds")


def _find(service, payment_id):
    for payment in service["payments"]:
        if payment["id"] == payment_id:
            return payment
    return None


def validate_item(service, payment_id, body, now, allow_settlement=False):
    """The per-item correction checks (design section 28 phase 2 / A26), shared by
    the single-correction endpoint and the batch endpoint so the two paths can
    never diverge (ADR-009): payment lookup (404) -> field shape (422) ->
    linked_payment_immutable (422) -> refund_exceeds_payment (422) ->
    stale_revision (409) -> effective_at not later than now (422). Returns
    (payment, amount, delta, effective_raw) on success; raises errors.ApiError
    otherwise, with no state change.

    allow_settlement=False keeps R307's settlement-member disjunct for the single
    endpoint (A24); the batch endpoint is the only path that may correct a
    settlement member (R315/R316), so it passes allow_settlement=True and the
    check reduces to captures and refunds."""
    payment = _find(service, payment_id)
    if payment is None:
        raise errors.not_found("no such payment")  # R221
    # R222-R227: field shapes, in the body's own order.
    if "expected_revision" not in body:
        raise errors.validation_failed("expected_revision is required")
    expected_revision = state_mod.parse_amount(body["expected_revision"],
                                               "expected_revision")
    if expected_revision < 1:
        raise errors.validation_failed(
            "expected_revision must be a positive integer")
    if "amount" not in body:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(body["amount"])
    if amount < 0 or amount > MAX_AMOUNT:
        raise errors.validation_failed(
            "amount must be between 0 and %d" % MAX_AMOUNT)
    # R225/R226 have endpoint-specific rules, so a wrong JSON type is that
    # endpoint's 422, never the generic 400 (SA-8, stage-1 design section 9 step
    # 4's rule) — get_string would give 400, so the type is checked here.
    if "reason" not in body:
        raise errors.validation_failed("reason is required")
    if not isinstance(body["reason"], str):
        raise errors.validation_failed("reason must be a string")
    reason = body["reason"]
    if not 1 <= len(reason) <= REASON_MAX:
        raise errors.validation_failed(
            "reason must be %d..%d characters" % (1, REASON_MAX))
    if "effective_at" not in body:
        raise errors.validation_failed("effective_at is required")
    if not isinstance(body["effective_at"], str):
        raise errors.validation_failed(
            "effective_at must be an RFC 3339 timestamp")
    effective_raw = body["effective_at"]
    try:
        effective_at = state_mod.parse_rfc3339(effective_raw)
    except ValueError:
        raise errors.validation_failed(
            "effective_at must be an RFC 3339 timestamp")
    # R272/R275/R307: settlement members (single endpoint only, A24), captures
    # and refunds are immutable linked payments.
    if (payment.get("settlement_id") is not None and not allow_settlement) \
            or payment.get("authorization_id") is not None \
            or payment.get("refund_of") is not None:
        raise errors.ApiError(422, "linked_payment_immutable",
                              "linked payments (settlement members, captures and "
                              "refunds) cannot be corrected")
    # R308/A25: a correction cannot reduce a payment below its already-refunded
    # amount — the requested amount (the new total) against refunded_total, a
    # 422 checked immediately after the structural immutability check and before
    # the caller-supplied expected_revision (A25's 422-before-409 bucket).
    if amount < ledger.refunded_total(service, payment["id"]):
        raise errors.ApiError(422, "refund_exceeds_payment",
                              "correction would reduce the payment below its "
                              "already-refunded amount")
    # R231: staleness against the live revision history.
    if expected_revision != len(payment["revisions"]):
        raise errors.conflict("stale_revision",
                              "expected_revision %d, current revision %d"
                              % (expected_revision, len(payment["revisions"])))
    if effective_at > now:
        raise errors.validation_failed(  # R226/R326
            "effective_at must not be later than now")
    previous_amount = payment["revisions"][-1]["amount"]
    delta = amount - previous_amount
    return payment, amount, delta, effective_raw


def create_correction(ctx, user, service):
    """POST /payments/{payment_id}/corrections — design section 19's step 6
    (field/business validation) and step 7 (commit), after idempotency-key
    resolution (replay/reuse handled by the shared pipeline). The per-item
    checks are corrections.validate_item (ADR-009: one implementation, shared
    with the batch endpoint's phase 2)."""
    payment = _find(service, ctx.params.get("payment_id"))
    if payment is None:
        raise errors.not_found("no such payment")  # R221
    if payment["from_user_id"] != user["id"]:
        raise errors.forbidden(  # R220: only the original sender
            "only the original sender may correct a payment")
    now = state_mod.now_utc()
    payment, amount, delta, effective_raw = validate_item(
        service, ctx.params.get("payment_id"), ctx.parsed, now)
    reason = ctx.parsed["reason"]
    # R236/R285: a currently unaffordable debit is insufficient_funds, checked
    # before the historical sweep (A15's explicit order). The debtor is the original
    # sender for an increase, the original receiver for a decrease (R235).
    if delta != 0:
        debtor = payment["from_user_id"] if delta > 0 else payment["to_user_id"]
        if state_mod.available(debtor, service, now) < abs(delta):
            raise errors.insufficient_funds()
    recorded_at = next_recorded_at(payment)
    tentative = {
        "revision": len(payment["revisions"]) + 1,
        "amount": amount,
        "effective_at": effective_raw,
        "recorded_at": recorded_at,
        "reason": reason,
    }
    payment["revisions"].append(tentative)
    try:
        if delta != 0:  # a no-op restatement moves no money (stage-3's shortcut)
            _sweep_historical_overdraft(
                (payment["from_user_id"], payment["to_user_id"]), now)
    except errors.ApiError:
        payment["revisions"].pop()  # R239: nothing was ever committed
        raise
    # Commit (design section 19 step 9): the revision is already appended; move the
    # difference between the same two wallets in this same critical section (R234).
    service["users"][payment["from_user_id"]]["balance"] -= delta
    service["users"][payment["to_user_id"]]["balance"] += delta
    return 201, correction_response(payment, tentative)


def _sweep_historical_overdraft(parties, now):
    """R237-R238/R284: with every caller's tentative revision already appended, each
    party's total and available must be nonnegative at every boundary instant (A18:
    the parties' payment effective instants plus their holds' lifecycle instants —
    creation, captures, void, expiry — are the only instants where either can
    change), under the latest known revisions (known_at = now). The caller appends
    its tentative revision(s) to the payments' histories before calling and pops
    them on failure, so this function only ever reads committed-plus-tentative
    state; the caller holds STATE_LOCK throughout. parties is any iterable of user
    ids (the single correction passes its payment's two parties, design section 19;
    the batch endpoint passes the union across every item, design section 28
    phase 6) — duplicates are collapsed in first-appearance order."""
    seen = []
    for user_id in parties:
        if user_id not in seen:
            seen.append(user_id)
    parties = seen
    boundaries = set()
    for user_id in parties:
        for p in ledger.payments_touching(service=state_mod.get(), user_id=user_id):
            revision = ledger.select_revision(p, now)
            if revision is not None:
                boundaries.add(state_mod.parse_rfc3339(revision["effective_at"]))
        for a in state_mod.get()["authorizations"].values():
            if a["from_user_id"] == user_id or a["to_user_id"] == user_id:
                boundaries.add(state_mod.parse_rfc3339(a["created_at"]))
                for capture in a.get("captures") or []:
                    boundaries.add(state_mod.parse_rfc3339(capture["event_time"]))
                if a.get("void"):
                    boundaries.add(state_mod.parse_rfc3339(a["void"]["event_time"]))
                expires_at = state_mod.parse_rfc3339(a["expires_at"])
                if expires_at <= now:
                    boundaries.add(expires_at)
    for instant in boundaries:
        for user_id in parties:
            user = state_mod.get()["users"][user_id]
            total = ledger.balance_view(user, instant, now,
                                        service=state_mod.get())
            if total < 0:
                raise errors.conflict("historical_overdraft",
                                      "total would be negative at %s"
                                      % instant.isoformat(timespec="seconds"))
            held = authorizations.held_view(user_id, state_mod.get(), instant, now)
            if total - held < 0:
                raise errors.conflict("historical_overdraft",
                                      "available would be negative at %s"
                                      % instant.isoformat(timespec="seconds"))


def list_revisions(ctx, user, service):
    """GET /payments/{payment_id}/revisions — R243-R245: the two parties only (a
    third party gets 404 even for a public payment), every revision in order
    including revision 1 with reason \"\"."""
    payment = _find(service, ctx.params.get("payment_id"))
    if payment is None:
        raise errors.not_found("no such payment")
    if user["id"] != payment["from_user_id"] and user["id"] != payment["to_user_id"]:
        raise errors.not_found("no such payment")  # R244: exclusion, not refusal
    return 200, {"revisions": [revision_entry(r) for r in payment["revisions"]]}