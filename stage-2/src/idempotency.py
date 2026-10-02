"""The shared idempotent-write pipeline (design.md section 5, spec section 7).

The caller runs this whole pipeline while holding STATE_LOCK: the lock itself is the
idempotency-key claim. A record is stored only on a 2xx commit; a failed (4xx) attempt
writes nothing, so the key stays free (R59).
"""

import copy

import errors


def key_scoped(user_id, method, path, key):
    return (user_id, method, path, key)


def bodies_equal(a, b):
    """Same JSON value after parsing; key order and whitespace do not matter (R60).
    Booleans never equal numbers, even though Python's == says True == 1."""
    return _equal(a, b)


def _equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_equal(x, y) for x, y in zip(a, b))
    return a == b


def resolve(state, user_id, method, path, key, body):
    """Look up an already-claimed key.

    Returns ("replay", response_body) when the same user replays the same method, path
    and body; ("new", None) when the key is unused. Raises 409 idempotency_key_reuse when
    the same key was used with a different body."""
    record = state["idempotency"].get(key_scoped(user_id, method, path, key))
    if record is None:
        return "new", None
    if bodies_equal(record["body"], body):
        return "replay", copy.deepcopy(record["response_body"])
    raise errors.idempotency_key_reuse()


def store(state, user_id, method, path, key, body, response_status, response_body):
    """Store the original response under the key. Only ever called for a 2xx commit."""
    state["idempotency"][key_scoped(user_id, method, path, key)] = {
        "user_id": user_id,
        "method": method,
        "path": path,
        "key": key,
        "body": copy.deepcopy(body),
        "response_status": response_status,
        "response_body": copy.deepcopy(response_body),
    }


def export_records(state):
    return [copy.deepcopy(record) for record in state["idempotency"].values()]


def import_records(records):
    """Rebuild the idempotency dict from exported records. Raises ValueError on a
    structurally invalid record list."""
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("idempotency record must be an object")
        for field in ("user_id", "method", "path", "key", "body", "response_status", "response_body"):
            if field not in record:
                raise ValueError("idempotency record missing %s" % field)
        scoped = key_scoped(record["user_id"], record["method"], record["path"], record["key"])
        result[scoped] = copy.deepcopy(record)
    return result


def check_key_header(headers):
    """Missing or empty -> 400; longer than 255 characters -> 422 (spec R44)."""
    key = headers.get("Idempotency-Key")
    if key is None or key == "":
        raise errors.missing_idempotency_key()
    if len(key) > 255:
        raise errors.validation_failed("Idempotency-Key must be 1 to 255 characters")
    return key