"""Bill splits (spec sections 8, 9; R29, R78-R80, R82-R84). POST /splits is an
idempotent write path; the split itself stores nothing beyond the pending requests it
creates — replays are served by the idempotency record.
"""

import errors
import ids
import requests as requests_endpoints
import state as state_mod


def split_shares(amount, count):
    """Largest-remainder equal split in participant order (R82): the first
    amount % count participants get one extra minor unit. Shares sum to amount and
    differ by at most one."""
    base = amount // count
    remainder = amount % count
    return [base + 1 if i < remainder else base for i in range(count)]


def create_split(ctx, user, service):
    body = ctx.parsed
    if "amount" not in body:
        raise errors.validation_failed("amount is required")
    amount = state_mod.parse_amount(body["amount"])
    state_mod.check_amount_range(amount)
    participants = body.get("participant_handles")
    if participants is None and "participant_handles" in body:
        # explicit null is a wrong JSON type
        raise errors.malformed_request("participant_handles must be an array")
    if participants is None:
        raise errors.validation_failed("participant_handles is required")
    if not isinstance(participants, list):
        raise errors.malformed_request("participant_handles must be an array")
    if len(participants) == 0:
        raise errors.validation_failed("participant_handles must not be empty")
    if len(set(participants)) != len(participants):
        raise errors.validation_failed("participant_handles must not contain duplicates")
    for handle in participants:
        # a non-string is not the handle of any user; the caller's own handle always exists
        if not isinstance(handle, str) or (handle != user["handle"]
                                           and handle not in service["handles"]):
            raise errors.not_found("no user has that handle")
    note = state_mod.get_note(body)

    shares = split_shares(amount, len(participants))
    share_list = [{"handle": handle, "amount": share}
                  for handle, share in zip(participants, shares)]
    created = []
    for handle, share in zip(participants, shares):
        if handle == user["handle"]:
            continue
        request = requests_endpoints._create_request(service, user["id"], handle, share, note)
        created.append(requests_endpoints.request_response(service, request))

    return 201, {
        "split_id": ids.new_id("sp"),
        "amount": amount,
        "currency": service["currency"],
        "note": note,
        "shares": share_list,
        "requests": created,
        "created_at": state_mod.now_rfc3339(),
    }