import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8086"
failures = []


def req(method, path, body=None, token=None, idem_key=None):
    hdrs = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = "Bearer " + token
    if idem_key:
        hdrs["Idempotency-Key"] = idem_key
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        parsed = json.loads(raw) if raw else None
    except Exception:
        parsed = raw
    return status, parsed


def check(name, cond, detail=""):
    if not cond:
        failures.append((name, detail))
        print("FAIL", name, detail)
    else:
        print("ok  ", name)


# --- spot-check 1: a stage-1-shaped (v1) export imports cleanly: ttl defaults to
# 600, authorizations defaults to [], every stage-1 behaviour intact.
v1_export = {
    "track": "pocketful",
    "format_version": 1,
    "state": {
        "currency": "EUR", "minor_units": 2,
        "users": {
            "u_zoe": {
                "id": "u_zoe", "email": "zoe@example.com",
                "password_hash": {
                    "algorithm": "scrypt", "n": 16384, "r": 8, "p": 1,
                    "salt": "aa", "hash": "bb",
                },
                "display_name": "Zoe", "handle": "zoe", "balance": 5000,
            },
            "u_yan": {
                "id": "u_yan", "email": "yan@example.com",
                "password_hash": {
                    "algorithm": "scrypt", "n": 16384, "r": 8, "p": 1,
                    "salt": "cc", "hash": "dd",
                },
                "display_name": "Yan", "handle": "yan", "balance": 3000,
            },
        },
        "handles": {"zoe": "u_zoe", "yan": "u_yan"},
        "emails": {"zoe@example.com": "u_zoe", "yan@example.com": "u_yan"},
        "tokens": {},
        "settlement_operator_ids": [],
        "payments": [],
        "requests": {},
        "request_order": [],
        "idempotency": [],
        "next_seq": 1,
    },
}
# the real scrypt hashes don't matter for this spot-check (login isn't exercised);
# only that import accepts the v1 shape and defaults ttl/authorizations correctly.
s, b = req("POST", "/_test/import", v1_export)
check("1: v1-shaped export imports cleanly (204)", s == 204, (s, b))

s, export_after = req("GET", "/_test/export")
check("1: export after v1 import -> 200", s == 200, s)
state = export_after["state"]
check("1: authorization_ttl_seconds defaults to 600", state.get("authorization_ttl_seconds", None) == 600 or "authorization_ttl_seconds" not in state, state.get("authorization_ttl_seconds"))
check("1: authorizations defaults to {} (empty)", state.get("authorizations", {}) == {}, state.get("authorizations"))
check("1: format_version 2 on re-export", export_after["format_version"] == 2, export_after["format_version"])


# --- spot-check 2: a bearer token minted before export/import stays valid after
# import, and a lost-response payment retry after import moves money exactly once.
fx = {
    "currency": "EUR", "minor_units": 2, "payments": [], "requests": [],
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "hunter2hunter2",
         "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "hunter2hunter2",
         "display_name": "Bob", "handle": "bob", "balance": 5000},
    ],
}
s, b = req("POST", "/_test/reset", fx)
check("2: reset -> 204", s == 204, (s, b))
s, login = req("POST", "/auth/login", {"email": "ada@example.com", "password": "hunter2hunter2"})
check("2: login -> 200", s == 200, (s, login))
token_before = login["token"]

body = {"to_handle": "bob", "amount": 777, "note": "lost"}
s, r1 = req("POST", "/payments", body, token=token_before, idem_key="verify-lost-1")
check("2: original payment committed (201)", s == 201, (s, r1))

s, export = req("GET", "/_test/export")
check("2: export -> 200", s == 200, s)

s, b = req("POST", "/_test/import", export)
check("2: import the same export back -> 204", s == 204, (s, b))

s, me = req("GET", "/me", token=token_before)
check("2: the pre-import bearer token is still valid after import", s == 200, (s, me))
check("2: same user after import", me.get("handle") == "ada" if s == 200 else False, me)

s, r2 = req("POST", "/payments", body, token=token_before, idem_key="verify-lost-1")
check("2: retry with the same key/body after import returns the ORIGINAL response (200)",
      s == 200, (s, r2))
check("2: retried payment_id matches the original (money moved exactly once)",
      r2.get("payment_id") == r1.get("payment_id"), (r1.get("payment_id"), r2.get("payment_id")))

s, me_after = req("GET", "/me", token=token_before)
check("2: ada's balance moved exactly once (10000-777=9223)", me_after.get("total") == 9223, me_after)


print()
if failures:
    print("EXPORT/IMPORT SPOT-CHECK FAILED: %d check(s)" % len(failures))
    for n, d in failures:
        print(" -", n, "|", d)
    raise SystemExit(1)
print("EXPORT/IMPORT SPOT-CHECK OK: all checks passed")
