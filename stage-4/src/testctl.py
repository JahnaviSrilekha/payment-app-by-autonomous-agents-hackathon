"""Test-control endpoints: POST /_test/reset, GET /_test/export, POST /_test/import
(spec sections 3.3 and 10). Unauthenticated. Password hashing for seeded users happens
before STATE_LOCK is taken, like every other hashing call.
"""

import copy

import errors
import idempotency
import ledger
import state as state_mod
from auth import hash_password

TRACK = "pocketful"
FORMAT_VERSION = 3
IMPORT_VERSIONS = (1, 2, 3)
VALID_MINOR_UNITS = (0, 2, 3)
REQUEST_STATUSES = ("pending", "paid", "declined", "cancelled")
AUTHORIZATION_STATUSES = state_mod.AUTHORIZATION_STATUSES


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

    ttl = state_mod.DEFAULT_AUTHORIZATION_TTL
    if "authorization_ttl_seconds" in fixture:
        ttl = state_mod.parse_amount(fixture["authorization_ttl_seconds"],
                                     "authorization_ttl_seconds")
        if ttl < 1:
            raise errors.validation_failed(
                "authorization_ttl_seconds must be a positive integer")

    users = _require_list(fixture, "users")
    payments = _require_list(fixture, "payments")
    request_list = _require_list(fixture, "requests")
    authorization_list = _optional_list(fixture, "authorizations")

    service = state_mod.new_service(currency, minor_units)
    service["authorization_ttl_seconds"] = ttl

    # One reset instant for the whole fixture (R195: omissions use reset time, before
    # subsequent API-created payments; A16: the clock is read once per reset).
    now = state_mod.now_utc()
    reset_time = state_mod.now_rfc3339()

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
        settlement_id = payment.get("settlement_id")
        if settlement_id is not None and not isinstance(settlement_id, str):
            raise errors.validation_failed("settlement_id must be a string")
        # R195: a seeded payment may supply created_at; omission uses the reset time.
        # R193: it must be an RFC 3339 instant with an offset; R196: a future one is
        # 422 with no state change (this builder is pure, so nothing has changed).
        created_at = payment.get("created_at", reset_time)
        try:
            created_parsed = state_mod.parse_rfc3339(created_at)
        except ValueError:
            raise errors.validation_failed(
                "payment created_at must be an RFC 3339 timestamp")
        if created_parsed > now:
            raise errors.validation_failed("payment created_at must not be in the future")
        service["payments"].append({
            "id": payment_id,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "amount": amount,
            "currency": currency,
            "note": note,
            "visibility": visibility,
            "request_id": payment.get("request_id"),
            "settlement_id": settlement_id,
            "authorization_id": None,  # seeded payments are stage-1-shaped (R167)
            # R215: the supplied created_at is also the original recorded/effective
            # time — revision 1 (R214), stored verbatim so it is echoed back exactly.
            "revisions": [ledger.initial_revision(amount, created_at)],
            "created_at": created_at,
            "seq": state_mod.next_seq(service),
        })

    # R216: base_balance is the seeded ending balance minus the net effect of the
    # original seeded payments — the opening balance. Computed once here, never
    # written again outside reset/import (design section 17), so corrections can
    # never change it.
    for user_id, user in service["users"].items():
        net = 0
        for p in service["payments"]:
            if p["to_user_id"] == user_id:
                net += p["amount"]
            elif p["from_user_id"] == user_id:
                net -= p["amount"]
        user["base_balance"] = user["balance"] - net

    # R218 (same atomic reset pass as R196): the seeded history must be consistent
    # and nonnegative — replaying the original payments in effective-time order from
    # their opening balances must keep every balance >= 0 throughout, so the fixture
    # never asserts a balance that the money movement could not have produced.
    running = {user_id: user["base_balance"] for user_id, user in service["users"].items()}
    # The opening balance is iteration 0 of the replay: a negative one asserts an
    # impossible "before anything moved" state even if every payment is affordable.
    for user_id, opening in running.items():
        if opening < 0:
            raise errors.validation_failed(
                "seeded payment history would overdraw user %s before any payment"
                % user_id)
    ordered = sorted(service["payments"],
                     key=lambda p: (state_mod.parse_rfc3339(p["created_at"]), p["id"]))
    for p in ordered:
        running[p["from_user_id"]] -= p["amount"]
        running[p["to_user_id"]] += p["amount"]
        if running[p["from_user_id"]] < 0 or running[p["to_user_id"]] < 0:
            raise errors.validation_failed(
                "seeded payment history would overdraw user %s" % p["from_user_id"])

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

    for authorization in authorization_list:
        if not isinstance(authorization, dict):
            raise errors.malformed_request("each authorization must be an object")
        authorization_id = _require_string(authorization, "id", "authorization")
        from_user_id = _require_string(authorization, "from_user_id", "authorization")
        to_user_id = _require_string(authorization, "to_user_id", "authorization")
        if from_user_id not in service["users"]:
            raise errors.validation_failed("unknown from_user_id %s" % from_user_id)
        if to_user_id not in service["users"]:
            raise errors.validation_failed("unknown to_user_id %s" % to_user_id)
        if "amount" not in authorization:
            raise errors.validation_failed("authorization amount is required")
        amount = state_mod.parse_amount(authorization["amount"], "amount")
        state_mod.check_amount_range(amount)
        note = _seeded_note(authorization)
        visibility = authorization.get("visibility", "public")
        if visibility not in ("public", "private"):
            raise errors.validation_failed("visibility must be public or private")
        if "status" not in authorization:
            raise errors.validation_failed("authorization status is required")
        status = authorization["status"]
        if status not in AUTHORIZATION_STATUSES:
            raise errors.validation_failed(
                "status must be one of %s" % (AUTHORIZATION_STATUSES,))
        if "expires_at" not in authorization:
            raise errors.validation_failed("authorization expires_at is required")
        try:
            state_mod.parse_rfc3339(authorization["expires_at"])
        except ValueError:
            raise errors.validation_failed("expires_at must be an RFC 3339 timestamp")
        if "captured_amount" in authorization:
            captured_amount = state_mod.parse_amount(authorization["captured_amount"],
                                                     "captured_amount")
        elif status == "captured":
            # R153 lists no captured_amount for seeded entries: a seeded captured
            # authorization represents a completed full capture of its amount
            captured_amount = amount
        else:
            captured_amount = 0
        if captured_amount < 0 or captured_amount > amount:
            raise errors.validation_failed(
                "captured_amount must be between 0 and the authorized amount")
        created_at = authorization.get("created_at", reset_time)
        try:
            created_parsed = state_mod.parse_rfc3339(created_at)
        except ValueError:
            raise errors.validation_failed("created_at must be an RFC 3339 timestamp")
        # A hold created in the future would make the (now, now) view report it as
        # "unknown" (not yet known), silently dropping it from held — rejected so the
        # stage-2 wrapper equality holds for every accepted fixture (R286).
        if created_parsed > now:
            raise errors.validation_failed(
                "authorization created_at must not be in the future")
        # R287: a seeded closed hold is stored and read back with its given
        # status/closed_at; no synthetic capture/void record is required. When the
        # fixture omits closed_at the close defaults to creation — the earliest
        # defensible instant, so a stored-expired seed whose expires_at still lies
        # ahead reads back closed (stage-2 semantics). A close in the future of the
        # reset would drop the hold out of the closed view: rejected.
        closed_at = authorization.get("closed_at")
        if closed_at is not None:
            if not isinstance(closed_at, str):
                raise errors.validation_failed("closed_at must be a string")
            try:
                closed_parsed = state_mod.parse_rfc3339(closed_at)
            except ValueError:
                raise errors.validation_failed(
                    "closed_at must be an RFC 3339 timestamp")
            if status == "open":
                raise errors.validation_failed(
                    "a seeded open authorization cannot have closed_at")
            if closed_parsed > now:
                raise errors.validation_failed(
                    "closed_at must not be in the future")
        elif status != "open":
            closed_at = created_at
        payment_ids = authorization.get("payment_ids", [])
        if not isinstance(payment_ids, list) \
                or any(not isinstance(pid, str) for pid in payment_ids):
            raise errors.validation_failed("payment_ids must be an array of payment ids")
        if authorization_id in service["authorizations"]:
            raise errors.validation_failed("duplicate authorization id %s" % authorization_id)
        service["authorizations"][authorization_id] = {
            "id": authorization_id,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "amount": amount,
            "captured_amount": captured_amount,
            "currency": currency,
            "note": note,
            "visibility": visibility,
            "status": status,
            "expires_at": authorization["expires_at"],
            "created_at": created_at,
            # Hold lifecycle events (design section 20): seeded holds carry no
            # reconstructed records (R287) — the unrecorded captured_amount and the
            # stored status/closed_at carry the pre-reset lifecycle instead.
            "captures": [],
            "void": None,
            "closed_at": closed_at,
            "payment_ids": list(payment_ids),
            "seq": state_mod.next_seq(service),
        }
        service["authorization_order"].append(authorization_id)

    # R156: a sum of seeded unexpired open holds larger than a user's balance is a
    # reset error, changing nothing — same pass as the negative-seeded-balance check.
    now = state_mod.now_utc()
    for user_id, user in service["users"].items():
        holds = state_mod.held(user_id, service, now)
        if holds > user["balance"]:
            raise errors.validation_failed(
                "seeded open holds (%d) exceed balance (%d) for user %s"
                % (holds, user["balance"], user["handle"]))

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


def _optional_list(container, field):
    """A fixture array that may be omitted altogether: omission means an empty list
    (R158). Present but not an array is 400, like the required arrays."""
    if field not in container:
        return []
    if not isinstance(container[field], list):
        raise errors.malformed_request("%s must be an array" % field)
    return container[field]


def _seeded_note(authorization):
    if "note" not in authorization:
        return ""
    note = authorization["note"]
    if not isinstance(note, str):
        raise errors.validation_failed("note must be a string")
    if len(note) > state_mod.NOTE_MAX:
        raise errors.validation_failed(
            "note must be at most %d characters" % state_mod.NOTE_MAX)
    return note


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
    writes never change the snapshot. statement_snapshots are never exported (design
    section 23, R265: a snapshot's loss across a restart is already permitted — a
    re-paged snapshot after a restart is simply a fresh GET /statement call)."""
    service = state_mod.get()
    snapshot = copy.deepcopy(service)
    snapshot.pop("statement_snapshots", None)
    # A20: every exported user carries base_balance as a stored field (accounts
    # opened after reset hold an implicit zero, R217).
    for user in snapshot["users"].values():
        user.setdefault("base_balance", 0)
    snapshot["idempotency"] = idempotency.export_records(service)
    return {
        "track": TRACK,
        "format_version": FORMAT_VERSION,
        "state": snapshot,
    }


def validate_import(payload):
    """Validate an imported export object and rebuild the service state from it without
    touching shared state. Raises ApiError(422) on any invalid input. Accepts
    format_version 1 (a stage-1 export: no authorizations, ttl defaults, payment
    authorization_id defaults null) or 2; any other version is 422."""
    if not isinstance(payload, dict):
        raise errors.malformed_request("body must be a JSON object")
    if "track" not in payload or payload["track"] != TRACK:
        raise errors.validation_failed("track must be %s" % TRACK)
    version = payload.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) \
            or version not in IMPORT_VERSIONS:
        raise errors.validation_failed(
            "format_version must be one of %s" % (IMPORT_VERSIONS,))
    if "state" not in payload or not isinstance(payload["state"], dict):
        raise errors.validation_failed("state is required")

    raw = payload["state"]
    try:
        return _build_from_state(raw, version)
    except (KeyError, TypeError, ValueError):
        raise errors.validation_failed("state is invalid")


def _validate_stored_revisions(payment):
    """A format-3 payment carries its full revision history as stored data (A20):
    revision 1 is the original amount at the original instant with reason \"\" (R214),
    revision numbers ascend contiguously from 1, recorded_at strictly increases
    (R230) and every amount is a nonnegative integer."""
    revisions = payment["revisions"]
    if not isinstance(revisions, list) or not revisions:
        raise ValueError("payment revisions")
    for revision in revisions:
        if not isinstance(revision, dict):
            raise ValueError("revision entry")
        number = revision["revision"]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("revision number")
        amount = revision["amount"]
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("revision amount")
        if not isinstance(revision["reason"], str):
            raise ValueError("revision reason")
        try:
            state_mod.parse_rfc3339(revision["effective_at"])
            state_mod.parse_rfc3339(revision["recorded_at"])
        except ValueError:
            raise ValueError("revision timestamps")
    if [r["revision"] for r in revisions] != list(range(1, len(revisions) + 1)):
        raise ValueError("revision numbering")
    for earlier, later in zip(revisions, revisions[1:]):
        if state_mod.parse_rfc3339(earlier["recorded_at"]) \
                >= state_mod.parse_rfc3339(later["recorded_at"]):
            raise ValueError("recorded_at must strictly increase")
    first = revisions[0]
    if first["amount"] != payment["amount"] \
            or first["effective_at"] != payment["created_at"] \
            or first["recorded_at"] != payment["created_at"]:
        raise ValueError("revision 1 must be the original payment")


def _build_from_state(raw, version):
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

    ttl = raw.get("authorization_ttl_seconds", state_mod.DEFAULT_AUTHORIZATION_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        raise ValueError("authorization_ttl_seconds")
    service["authorization_ttl_seconds"] = ttl

    for user_id, user in raw["users"].items():
        _require_fields(user, ("id", "email", "password_hash", "display_name", "handle", "balance"))
        if not isinstance(user["id"], str) or not isinstance(user["email"], str) \
                or not isinstance(user["display_name"], str) or not isinstance(user["handle"], str):
            raise ValueError("user fields")
        balance = user["balance"]
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
            raise ValueError("balance")
        if version >= 3:
            # A20: a format-3 export carries base_balance as stored data — taken
            # verbatim, never re-derived (a corrected balance cannot recover it).
            base_balance = user.get("base_balance")
            if isinstance(base_balance, bool) or not isinstance(base_balance, int) \
                    or base_balance < 0:
                raise ValueError("user base_balance")
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

    # Authorizations (stage-2 exports). A stage-1 (format_version 1) state has no
    # authorizations key: omission means an empty list/dict (R158).
    raw_authorizations = raw.get("authorizations", {})
    if not isinstance(raw_authorizations, dict):
        raise ValueError("authorizations")
    for authorization_id, authorization in raw_authorizations.items():
        _require_fields(authorization, ("id", "from_user_id", "to_user_id", "amount",
                                        "captured_amount", "currency", "note",
                                        "visibility", "status", "expires_at",
                                        "created_at", "seq", "payment_ids"))
        if authorization["from_user_id"] not in service["users"] \
                or authorization["to_user_id"] not in service["users"]:
            raise ValueError("authorization users")
        amount = authorization["amount"]
        if isinstance(amount, bool) or not isinstance(amount, int) \
                or amount < 1 or amount > state_mod.MAX_AMOUNT:
            raise ValueError("authorization amount")
        captured_amount = authorization["captured_amount"]
        if isinstance(captured_amount, bool) or not isinstance(captured_amount, int) \
                or captured_amount < 0 or captured_amount > amount:
            raise ValueError("authorization captured_amount")
        if authorization["status"] not in AUTHORIZATION_STATUSES:
            raise ValueError("authorization status")
        try:
            state_mod.parse_rfc3339(authorization["expires_at"])
            state_mod.parse_rfc3339(authorization["created_at"])
        except ValueError:
            raise ValueError("authorization timestamps")
        payment_ids = authorization["payment_ids"]
        if not isinstance(payment_ids, list) \
                or any(not isinstance(pid, str) for pid in payment_ids):
            raise ValueError("authorization payment_ids")
        if isinstance(authorization["seq"], bool) or not isinstance(authorization["seq"], int):
            raise ValueError("authorization seq")
        service["authorizations"][authorization_id] = copy.deepcopy(authorization)
    raw_authorization_order = raw.get("authorization_order", [])
    if not isinstance(raw_authorization_order, list):
        raise ValueError("authorization_order")
    for authorization_id in raw_authorization_order:
        if authorization_id not in service["authorizations"]:
            raise ValueError("authorization_order")
        service["authorization_order"].append(authorization_id)

    for payment in raw["payments"]:
        _require_fields(payment, ("id", "from_user_id", "to_user_id", "amount", "currency",
                                  "note", "visibility", "request_id", "settlement_id",
                                  "created_at", "seq"))
        if payment["from_user_id"] not in service["users"] or payment["to_user_id"] not in service["users"]:
            raise ValueError("payment users")
        authorization_id = payment.get("authorization_id")
        if authorization_id is not None \
                and (not isinstance(authorization_id, str)
                     or authorization_id not in service["authorizations"]):
            raise ValueError("payment authorization_id")
        stored_payment = copy.deepcopy(payment)
        stored_payment.setdefault("authorization_id", None)  # stage-1 payments: null
        if version >= 3:
            # A20: the exported revisions are the one source of truth — carried
            # through verbatim (a stage-3 round trip preserves correction history,
            # R241 extended to survive import).
            _validate_stored_revisions(stored_payment)
        else:
            # R273: stages 1-2 carry no correction history — materialize the
            # implicit revision 1 (R214); a settlement member's created_at is the
            # settlement's committed_at, carried on both axes (R271).
            stored_payment.setdefault("revisions", [ledger.initial_revision(
                stored_payment["amount"], stored_payment["created_at"])])
        service["payments"].append(stored_payment)
    if version < 3:
        # R216: base_balance is derived exactly as reset derives it — from the
        # imported balance minus the net of the imported payments (correct for
        # formats 1-2: they carry no correction history to lose).
        for user_id, user in service["users"].items():
            net = 0
            for p in service["payments"]:
                if p["to_user_id"] == user_id:
                    net += p["amount"]
                elif p["from_user_id"] == user_id:
                    net -= p["amount"]
            user["base_balance"] = user["balance"] - net
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

    # The same invariant reset enforces (R156/R145): no imported state may leave a
    # user's available balance negative.
    now = state_mod.now_utc()
    for user_id, user in service["users"].items():
        if state_mod.held(user_id, service, now) > user["balance"]:
            raise ValueError("available would be negative")
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