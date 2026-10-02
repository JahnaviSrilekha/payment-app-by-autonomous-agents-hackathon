"""Test-control endpoints: POST /_test/reset, GET /_test/export, POST /_test/import
(spec sections 3.3 and 10). Unauthenticated. Password hashing for seeded users happens
before STATE_LOCK is taken, like every other hashing call.
"""

import copy

import errors
import idempotency
import state as state_mod
from auth import hash_password

TRACK = "pocketful"
FORMAT_VERSION = 1
VALID_MINOR_UNITS = (0, 2, 3)
REQUEST_STATUSES = ("pending", "paid", "declined", "cancelled")


def build_from_fixture(fixture):
    """Validate the fixture and build a complete new service state from it. Pure:
    touches no shared state. Raises ApiError on any invalid input and changes nothing."""
    if not isinstance(fixture, dict):
        raise errors.malformed_request("fixture must be a JSON object")

    if "currency" not in fixture:
        raise errors.validation_failed("currency is required")
    currency = fixture["currency"]
    if not isinstance(currency, str):
        raise errors.malformed_request("currency must be a string")

    if "minor_units" not in fixture:
        raise errors.validation_failed("minor_units is required")
    minor_units = state_mod.parse_amount(fixture["minor_units"], "minor_units")
    if minor_units not in VALID_MINOR_UNITS:
        raise errors.validation_failed("minor_units must be 0, 2 or 3")

    users = _require_list(fixture, "users")
    payments = _require_list(fixture, "payments")
    request_list = _require_list(fixture, "requests")

    service = state_mod.new_service(currency, minor_units)

    for user in users:
        if not isinstance(user, dict):
            raise errors.malformed_request("each user must be an object")
        user_id = _require_string(user, "id", "user")
        email = _require_string(user, "email", "user")
        password = _require_string(user, "password", "user")
        display_name = _require_string(user, "display_name", "user")
        handle = _require_string(user, "handle", "user")
        if not state_mod.HANDLE_RE.fullmatch(handle):
            raise errors.validation_failed("handle must match ^[a-z0-9_]{1,20}$")
        if "balance" not in user:
            raise errors.validation_failed("user balance is required")
        balance = state_mod.parse_amount(user["balance"], "balance")
        if balance < 0:
            raise errors.validation_failed("balance must not be negative")
        if user_id in service["users"]:
            raise errors.validation_failed("duplicate user id %s" % user_id)
        if handle in service["handles"]:
            raise errors.validation_failed("duplicate handle %s" % handle)
        if email in service["emails"]:
            raise errors.validation_failed("duplicate email %s" % email)
        service["users"][user_id] = {
            "id": user_id,
            "email": email,
            "password_hash": hash_password(password),
            "display_name": display_name,
            "handle": handle,
            "balance": balance,
        }
        service["handles"][handle] = user_id
        service["emails"][email] = user_id

    for operator_id in _operator_ids(fixture):
        if operator_id not in service["users"]:
            raise errors.validation_failed("unknown settlement operator id %s" % operator_id)
    service["settlement_operator_ids"] = _operator_ids(fixture)

    for payment in payments:
        if not isinstance(payment, dict):
            raise errors.malformed_request("each payment must be an object")
        payment_id = _require_string(payment, "id", "payment")
        from_user_id = _require_string(payment, "from_user_id", "payment")
        to_user_id = _require_string(payment, "to_user_id", "payment")
        if from_user_id not in service["users"]:
            raise errors.validation_failed("unknown from_user_id %s" % from_user_id)
        if to_user_id not in service["users"]:
            raise errors.validation_failed("unknown to_user_id %s" % to_user_id)
        if "amount" not in payment:
            raise errors.validation_failed("payment amount is required")
        amount = state_mod.parse_amount(payment["amount"], "amount")
        if amount < 0:
            raise errors.validation_failed("payment amount must not be negative")
        note = payment.get("note", "")
        if not isinstance(note, str):
            raise errors.validation_failed("note must be a string")
        visibility = payment.get("visibility", "public")
        if visibility not in ("public", "private"):
            raise errors.validation_failed("visibility must be public or private")
        if any(p["id"] == payment_id for p in service["payments"]):
            raise errors.validation_failed("duplicate payment id %s" % payment_id)
        service["payments"].append({
            "id": payment_id,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "amount": amount,
            "currency": currency,
            "note": note,
            "visibility": visibility,
            "request_id": payment.get("request_id"),
            "settlement_id": None,
            "created_at": state_mod.now_rfc3339(),
            "seq": state_mod.next_seq(service),
        })

    for request in request_list:
        if not isinstance(request, dict):
            raise errors.malformed_request("each request must be an object")
        request_id = _require_string(request, "id", "request")
        requester_id = _require_string(request, "requester_id", "request")
        payer_id = _require_string(request, "payer_id", "request")
        if requester_id not in service["users"]:
            raise errors.validation_failed("unknown requester_id %s" % requester_id)
        if payer_id not in service["users"]:
            raise errors.validation_failed("unknown payer_id %s" % payer_id)
        if "amount" not in request:
            raise errors.validation_failed("request amount is required")
        amount = state_mod.parse_amount(request["amount"], "amount")
        note = request.get("note", "")
        if not isinstance(note, str):
            raise errors.validation_failed("note must be a string")
        status = request.get("status", "pending")
        if status not in REQUEST_STATUSES:
            raise errors.validation_failed("status must be one of %s" % (REQUEST_STATUSES,))
        if request_id in service["requests"]:
            raise errors.validation_failed("duplicate request id %s" % request_id)
        service["requests"][request_id] = {
            "id": request_id,
            "requester_id": requester_id,
            "payer_id": payer_id,
            "amount": amount,
            "currency": currency,
            "note": note,
            "status": status,
            "payment_id": None,
            "created_at": state_mod.now_rfc3339(),
            "seq": state_mod.next_seq(service),
        }
        service["request_order"].append(request_id)

    return service


def _operator_ids(fixture):
    if "settlement_operator_ids" not in fixture:
        return []
    operator_ids = fixture["settlement_operator_ids"]
    if not isinstance(operator_ids, list):
        raise errors.validation_failed("settlement_operator_ids must be an array")
    for operator_id in operator_ids:
        if not isinstance(operator_id, str):
            raise errors.validation_failed("settlement_operator_ids must be user ids")
    return list(operator_ids)


def _require_list(container, field):
    if field not in container:
        raise errors.validation_failed("%s is required" % field)
    if not isinstance(container[field], list):
        raise errors.malformed_request("%s must be an array" % field)
    return container[field]


def _require_string(container, field, what):
    if field not in container:
        raise errors.validation_failed("%s is required for each %s" % (field, what))
    value = container[field]
    if not isinstance(value, str):
        raise errors.malformed_request("%s must be a string" % field)
    return value


def export_snapshot():
    """Deep-copy the whole state while holding STATE_LOCK (caller holds it), then wrap.
    The idempotency dict is keyed by (user, method, path, key) tuples in memory and by a
    record list in the exported JSON. Serialization happens outside the lock; later
    writes never change the snapshot."""
    service = state_mod.get()
    snapshot = copy.deepcopy(service)
    snapshot["idempotency"] = idempotency.export_records(service)
    return {
        "track": TRACK,
        "format_version": FORMAT_VERSION,
        "state": snapshot,
    }


def validate_import(payload):
    """Validate an imported export object and rebuild the service state from it without
    touching shared state. Raises ApiError(422) on any invalid input."""
    if not isinstance(payload, dict):
        raise errors.malformed_request("body must be a JSON object")
    if "track" not in payload or payload["track"] != TRACK:
        raise errors.validation_failed("track must be %s" % TRACK)
    version = payload.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != FORMAT_VERSION:
        raise errors.validation_failed("format_version must be %d" % FORMAT_VERSION)
    if "state" not in payload or not isinstance(payload["state"], dict):
        raise errors.validation_failed("state is required")

    raw = payload["state"]
    try:
        return _build_from_state(raw)
    except (KeyError, TypeError, ValueError):
        raise errors.validation_failed("state is invalid")


def _build_from_state(raw):
    currency = raw["currency"]
    if not isinstance(currency, str):
        raise ValueError("currency")
    minor_units = raw["minor_units"]
    if isinstance(minor_units, bool) or not isinstance(minor_units, int) or minor_units not in VALID_MINOR_UNITS:
        raise ValueError("minor_units")
    if not isinstance(raw["users"], dict) or not isinstance(raw["handles"], dict) \
            or not isinstance(raw["emails"], dict) or not isinstance(raw["tokens"], dict):
        raise ValueError("indexes")
    if not isinstance(raw["payments"], list) or not isinstance(raw["requests"], dict) \
            or not isinstance(raw["request_order"], list):
        raise ValueError("records")
    if not isinstance(raw["settlement_operator_ids"], list):
        raise ValueError("settlement_operator_ids")
    if isinstance(raw["next_seq"], bool) or not isinstance(raw["next_seq"], int):
        raise ValueError("next_seq")

    service = state_mod.new_service(currency, minor_units)
    service["next_seq"] = raw["next_seq"]
    service["settlement_operator_ids"] = list(raw["settlement_operator_ids"])

    for user_id, user in raw["users"].items():
        _require_fields(user, ("id", "email", "password_hash", "display_name", "handle", "balance"))
        if not isinstance(user["id"], str) or not isinstance(user["email"], str) \
                or not isinstance(user["display_name"], str) or not isinstance(user["handle"], str):
            raise ValueError("user fields")
        balance = user["balance"]
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
            raise ValueError("balance")
        _require_password_hash(user["password_hash"])
        service["users"][user_id] = copy.deepcopy(user)
        service["handles"][user["handle"]] = user_id
        service["emails"][user["email"]] = user_id
    for handle, user_id in raw["handles"].items():
        if user_id not in service["users"]:
            raise ValueError("handles")
        service["handles"][handle] = user_id
    for email, user_id in raw["emails"].items():
        if user_id not in service["users"]:
            raise ValueError("emails")
        service["emails"][email] = user_id
    for token, user_id in raw["tokens"].items():
        if user_id not in service["users"]:
            raise ValueError("tokens")
        service["tokens"][token] = user_id

    for payment in raw["payments"]:
        _require_fields(payment, ("id", "from_user_id", "to_user_id", "amount", "currency",
                                  "note", "visibility", "request_id", "settlement_id",
                                  "created_at", "seq"))
        if payment["from_user_id"] not in service["users"] or payment["to_user_id"] not in service["users"]:
            raise ValueError("payment users")
        service["payments"].append(copy.deepcopy(payment))
    for request_id, request in raw["requests"].items():
        _require_fields(request, ("id", "requester_id", "payer_id", "amount", "currency",
                                  "note", "status", "payment_id", "created_at", "seq"))
        if request["requester_id"] not in service["users"] or request["payer_id"] not in service["users"]:
            raise ValueError("request users")
        service["requests"][request_id] = copy.deepcopy(request)
    for request_id in raw["request_order"]:
        if request_id not in service["requests"]:
            raise ValueError("request_order")
        service["request_order"].append(request_id)

    service["idempotency"] = idempotency.import_records(raw["idempotency"])
    return service


def _require_fields(obj, fields):
    if not isinstance(obj, dict):
        raise ValueError("object")
    for field in fields:
        if field not in obj:
            raise ValueError("missing %s" % field)


def _require_password_hash(record):
    if not isinstance(record, dict):
        raise ValueError("password_hash")
    _require_fields(record, ("algorithm", "n", "r", "p", "salt", "hash"))
    if not isinstance(record["salt"], str) or not isinstance(record["hash"], str):
        raise ValueError("password_hash encoding")
    for field in ("n", "r", "p"):
        if isinstance(record[field], bool) or not isinstance(record[field], int):
            raise ValueError("password_hash params")