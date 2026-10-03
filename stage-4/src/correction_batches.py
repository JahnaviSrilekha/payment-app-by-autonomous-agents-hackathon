"""POST /correction-batches (idempotent write path 10; R310-R331, R333; design
section 28, A26, ADR-009).

A correction batch atomically appends one new revision to each of up to 32 payments.
The six phases of A26 run in order: request shape (422) -> per-item validation in
input order via the shared `corrections.validate_item` (404/422/409, first failing
item wins) -> operator permission (403, after the items, mirroring
`create_settlement`'s real ordering) -> settlement completeness (422
incomplete_settlement / validation_failed, offset-tolerant instants) -> combined
affordability (409, net per-user delta) -> combined historical-overdraft sweep (409,
every tentative revision trial-appended at once and all popped on any violation).

Nothing mutates until phase 6's trial append, and that is fully undone before any
response is sent, so a rejected batch is indistinguishable from one never attempted
(R323). On success the whole batch commits inside the caller's single STATE_LOCK
critical section with one shared recorded_at (A27) and one fresh correction_batch_id
(A27/R325) stamped on every new revision.
"""

import corrections as corrections_mod
import errors
import ids
import state as state_mod

MAX_ITEMS = 32


def create_correction_batch(ctx, user, service):
    """POST /correction-batches — design section 28's six phases, after
    idempotency-key resolution (replay/reuse handled by the shared pipeline)."""
    now = state_mod.now_utc()  # one instant for the whole request (A16)
    # Phase 1 — request shape (R311/R312), mirroring settlements' own shape checks.
    if "corrections" not in ctx.parsed:
        raise errors.validation_failed("corrections is required")
    items = ctx.parsed["corrections"]
    if not isinstance(items, list):
        raise errors.validation_failed("corrections must be an array")
    if not 1 <= len(items) <= MAX_ITEMS:
        raise errors.validation_failed(
            "corrections must contain 1 to %d objects" % MAX_ITEMS)
    for item in items:
        if not isinstance(item, dict):
            raise errors.validation_failed("each correction must be an object")
        if not isinstance(item.get("payment_id"), str):
            raise errors.validation_failed("payment_id must be a string")
    payment_ids = [item["payment_id"] for item in items]
    if len(set(payment_ids)) != len(payment_ids):
        raise errors.validation_failed(
            "payment_ids in one batch must be pairwise distinct")  # R312
    # Phase 2 — per-item validation in input order; first failing item wins (R313,
    # R314, R315, R319, R326, A26 phase 2). Nothing has mutated yet, so every item's
    # expected_revision is compared against its payment's current revision count.
    validated = []
    for item in items:
        payment, amount, delta, effective_raw = corrections_mod.validate_item(
            service, item["payment_id"], item, now, allow_settlement=True)
        validated.append((payment, amount, delta, effective_raw, item))
    # Phase 3 — operator permission, after the items (R310, A26 phase 3).
    if user["id"] not in service["settlement_operator_ids"]:
        raise errors.forbidden(
            "only settlement operators may submit correction batches")
    # Phase 4 — settlement completeness (R316/R317, A26 phase 4): groups visited in
    # the order their first member appears in the input.
    _check_settlement_completeness(service, validated)
    # Phase 5 — combined affordability (R321/R322, A26 phase 5): the batch's net
    # per-user delta against current available funds, the same collective shape
    # settlements._check_affordability uses for settlement legs.
    deltas = {}
    for payment, _, delta, _, _ in validated:
        deltas[payment["from_user_id"]] = \
            deltas.get(payment["from_user_id"], 0) - delta
        deltas[payment["to_user_id"]] = deltas.get(payment["to_user_id"], 0) + delta
    for user_id, net in deltas.items():
        if net < 0 and state_mod.available(user_id, service, now) + net < 0:
            raise errors.insufficient_funds()
    # Phase 6 — combined historical sweep (R320's final phase, R323, A26 phase 6):
    # one shared recorded_at first (A27: computed before any tentative append, as
    # the max of each member's next_recorded_at candidate), then every tentative
    # revision trial-appended at once, swept over the union of the parties, and all
    # popped together on any violation.
    batch_id = ids.new_id("cb")
    recorded_at = max(state_mod.parse_rfc3339(
        corrections_mod.next_recorded_at(payment))
        for payment, _, _, _, _ in validated).isoformat(timespec="seconds")
    parties = []
    for payment, _, _, _, _ in validated:
        for user_id in (payment["from_user_id"], payment["to_user_id"]):
            if user_id not in parties:
                parties.append(user_id)
    tentatives = []
    try:
        for payment, amount, delta, effective_raw, item in validated:
            tentative = {
                "revision": len(payment["revisions"]) + 1,
                "amount": amount,
                "effective_at": effective_raw,
                "recorded_at": recorded_at,
                "reason": item["reason"],
                "correction_batch_id": batch_id,
            }
            payment["revisions"].append(tentative)
            tentatives.append((payment, tentative, delta))
        corrections_mod._sweep_historical_overdraft(parties, now)
    except errors.ApiError:
        for payment, _, _ in reversed(tentatives):
            payment["revisions"].pop()  # R323: nothing was ever left half-applied
        raise
    # Commit: the tentative revisions are already appended and survived the sweep;
    # make them permanent and move each item's difference between its two wallets
    # (same mechanism as the single correction's step 9, all in this critical
    # section, R234).
    for payment, _, delta in tentatives:
        service["users"][payment["from_user_id"]]["balance"] -= delta
        service["users"][payment["to_user_id"]]["balance"] += delta
    # R324/R325: 201 with the batch id, the shared recorded_at and every new
    # revision in input order, each entry exposing correction_batch_id.
    entries = []
    for payment, tentative, _ in tentatives:
        entry = corrections_mod.correction_response(payment, tentative)
        entry["correction_batch_id"] = batch_id
        entries.append(entry)
    return 201, {
        "correction_batch_id": batch_id,
        "recorded_at": recorded_at,
        "revisions": entries,
    }


def _check_settlement_completeness(service, validated):
    """R316/R317: correcting any member of a settlement requires every member of
    that settlement in the same batch (422 incomplete_settlement), and every
    included member's effective_at must parse to the same instant (422
    validation_failed) — compared as parsed aware datetimes, so +00:00, Z and any
    equivalent offset spelling are equal."""
    groups = {}
    order = []
    for payment, _, _, effective_raw, _ in validated:
        settlement_id = payment.get("settlement_id")
        if settlement_id is None:
            continue
        if settlement_id not in groups:
            groups[settlement_id] = []
            order.append(settlement_id)
        groups[settlement_id].append((payment, effective_raw))
    for settlement_id in order:
        included = {payment["id"] for payment, _ in groups[settlement_id]}
        for member in service["payments"]:
            if member.get("settlement_id") == settlement_id \
                    and member["id"] not in included:
                raise errors.ApiError(422, "incomplete_settlement",
                                      "correcting a settlement member requires "
                                      "every member of the settlement in the same "
                                      "batch")
        instants = {state_mod.parse_rfc3339(effective_raw)
                    for _, effective_raw in groups[settlement_id]}
        if len(instants) > 1:
            raise errors.validation_failed(
                "members of one settlement must share one effective instant")