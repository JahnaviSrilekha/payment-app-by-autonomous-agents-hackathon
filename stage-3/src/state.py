"""In-memory service state, the global STATE_LOCK and shared field parsing.

All service state is one plain-JSON-compatible dict (design.md section 2), guarded by
STATE_LOCK. Every read or write of the state happens while holding STATE_LOCK, with the
single exception of password hashing, which is always done before the lock is taken
(design.md section 3).
"""

import re
import threading
from datetime import datetime, timezone

import errors

STATE_LOCK = threading.Lock()

HANDLE_RE = re.compile(r"^[a-z0-9_]{1,20}$")

MAX_AMOUNT = 1000000000
NOTE_MAX = 200
KEY_MAX = 255
DEFAULT_AUTHORIZATION_TTL = 600
AUTHORIZATION_STATUSES = ("open", "captured", "voided", "expired")

_state = None


def get():
    """Current service state. Call only while holding STATE_LOCK."""
    return _state


def set_state(service):
    """Replace the whole service state. Call only while holding STATE_LOCK."""
    global _state
    _state = service


def new_service(currency, minor_units):
    return {
        "currency": currency,
        "minor_units": minor_units,
        "users": {},
        "handles": {},
        "emails": {},
        "tokens": {},
        "settlement_operator_ids": [],
        "payments": [],
        "requests": {},
        "request_order": [],
        "authorization_ttl_seconds": DEFAULT_AUTHORIZATION_TTL,
        "authorizations": {},
        "authorization_order": [],
        "statement_snapshots": {},
        "idempotency": {},
        "next_seq": 1,
    }


def next_seq(service):
    value = service["next_seq"]
    service["next_seq"] = value + 1
    return value


def now_rfc3339():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_utc():
    return datetime.now(timezone.utc)


def parse_rfc3339(value):
    """Parse an RFC 3339 timestamp into an aware UTC datetime. Raises ValueError on
    anything that is not a timestamp string carrying a UTC offset."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a UTC offset")
    return parsed.astimezone(timezone.utc)


# --- authorizations: derived holds (design sections 10-11, 20, ADR-004) --------
#
# Stage-3 generalizes these to (as_of, known_at) views implemented once in
# authorizations.py (design section 20); the stage-2 two-argument forms below stay
# as thin delegating wrappers so every stage-2 call site and test is unchanged and
# there is exactly one implementation. The lazy import avoids the module cycle.


def effective_status(authorization, now):
    """Pure function of the stored authorization row and an injected `now`: exactly
    effective_status_view at (now, now) — known_at_for clamps synthetic rows only. An
    open authorization whose expires_at is at or before now is "expired"; nothing is
    ever mutated — "expired" is a computed view (R159)."""
    from authorizations import effective_status_view, known_at_for
    return effective_status_view(authorization, now, known_at_for(authorization, now))


def remaining_amount(authorization, now):
    """Amount still held at the injected `now`: exactly remaining_amount_view at
    (now, now) — known_at_for clamps synthetic rows only."""
    from authorizations import remaining_amount_view, known_at_for
    return remaining_amount_view(authorization, now, known_at_for(authorization, now))


def held(user_id, service, now):
    """Sum of the user's open holds at the injected `now`: exactly held_view at
    (now, now) row by row — known_at_for clamps synthetic rows only (R276-R280
    generalization, one per-row implementation)."""
    from authorizations import remaining_amount_view, known_at_for
    return sum(remaining_amount_view(a, now, known_at_for(a, now))
               for a in service["authorizations"].values()
               if a["from_user_id"] == user_id)


def available(user_id, service, now):
    """`total - held` at the injected `now`. Reset and every write path guarantee this
    never goes negative (R145, R156)."""
    return service["users"][user_id]["balance"] - held(user_id, service, now)


# --- field parsing helpers (spec sections 4, 5) -------------------------------


def require_object(body):
    if not isinstance(body, dict):
        raise errors.malformed_request("body must be a JSON object")
    return body


def get_string(body, field, required=True, default=None):
    """Read a string field. Missing selects the default; wrong JSON type is 400."""
    if field not in body:
        if required:
            raise errors.validation_failed("%s is required" % field)
        return default
    value = body[field]
    if not isinstance(value, str):
        raise errors.malformed_request("%s must be a string" % field)
    return value


def parse_amount(value, field="amount"):
    """Integral numeric value: JSON 1000, 1000.0 and 1e3 are the same amount.
    Booleans and strings are not numbers here (422, spec R41)."""
    if isinstance(value, bool):
        raise errors.validation_failed("%s must be an integer" % field)
    if isinstance(value, int):
        amount = value
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")) or not value.is_integer():
            raise errors.validation_failed("%s must be an integer" % field)
        amount = int(value)
    else:
        raise errors.validation_failed("%s must be an integer" % field)
    return amount


def check_amount_range(amount):
    if amount < 1 or amount > MAX_AMOUNT:
        raise errors.validation_failed("amount must be between 1 and %d" % MAX_AMOUNT)


def get_note(body):
    """note is optional, defaults to "", stored and returned verbatim.
    Any non-string value, including null, is 422 (spec R41)."""
    if "note" not in body:
        return ""
    note = body["note"]
    if not isinstance(note, str):
        raise errors.validation_failed("note must be a string")
    if len(note) > NOTE_MAX:
        raise errors.validation_failed("note must be at most %d characters" % NOTE_MAX)
    return note


def get_visibility(body):
    if "visibility" not in body:
        return "public"
    visibility = body["visibility"]
    if visibility not in ("public", "private"):
        raise errors.validation_failed("visibility must be public or private")
    return visibility


def parse_limit(query):
    return parse_query_int(query, "limit", 50, 1, 200)


def parse_offset(query):
    return parse_query_int(query, "offset", 0, 0, None)


def parse_query_int(query, name, default, minimum, maximum):
    """Integer query parameters are plain decimal digits: 1e9, 4.0 and +4 are 422
    whatever their numeric value (spec R42)."""
    values = query.get(name)
    if not values:
        return default
    raw = values[0]
    if not re.fullmatch(r"[0-9]+", raw):
        raise errors.validation_failed("%s must be a plain decimal integer" % name)
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise errors.validation_failed("%s out of range" % name)
    return value


def derive_handle(email):
    """Handle derived from the email local part (spec section 4)."""
    local = email.split("@", 1)[0].lower()
    chars = []
    for ch in local:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            chars.append(ch)
        else:
            chars.append("_")
    return "".join(chars)[:20]


def valid_email(email):
    if not isinstance(email, str):
        return False
    if email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    return len(local) > 0 and len(domain) > 0