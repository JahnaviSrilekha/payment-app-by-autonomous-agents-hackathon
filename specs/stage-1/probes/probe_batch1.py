import json, sys, threading, urllib.request, urllib.error, re
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:57534"
failures = []

def req(method, path, body=None, token=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        resp = urllib.request.urlopen(r)
        status = resp.status
        raw = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()
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

FIXTURE = {
    "currency": "EUR", "minor_units": 2,
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
         "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
         "display_name": "Bob", "handle": "bob", "balance": 2500},
    ],
    "payments": [], "requests": [],
}

# R11/R32/R33/R34: reset seeds fixture exactly, seeded users can log in immediately
s, b = req("POST", "/_test/reset", FIXTURE)
check("R11 reset seeds fixture -> 204", s == 204, (s, b))

s, b = req("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
check("R33 seeded user logs in", s == 200 and "token" in b, (s, b))
token_ada = b.get("token") if isinstance(b, dict) else None

s, b2 = req("GET", "/me", token=token_ada)
check("R64 GET /me reflects seeded balance (R34 not replayed)", s == 200 and b2.get("balance") == 10000, (s, b2))

# R13: RFC3339 timestamp with offset — probed via signup's absence of timestamp; use export instead later.

# R21/R52: derived handle rules + collision -> 409 handle_taken
s, b = req("POST", "/auth/signup", {"email": "A.D.A!!@EXAMPLE.com", "password": "longenough1", "display_name": "Dup"})
check("R21 derived handle collides with seeded 'ada' -> 409 handle_taken", s == 409 and b["error"]["code"] == "handle_taken", (s, b))

s, b = req("POST", "/auth/signup", {"email": "He.llo-World!@example.com", "password": "longenough1", "display_name": "X"})
check("R21 handle derivation lowercases/replaces/truncates", s == 201, (s, b))
s2, me = req("GET", "/me", token=b.get("token") if isinstance(b, dict) else None)
check("R21 derived handle value correct", me.get("handle") == "he_llo_world_", me)

# R46-R51: signup/login error table
s, b = req("POST", "/auth/signup", {"email": "ada@example.com", "password": "longenough1", "display_name": "X"})
check("R48 email_taken", s == 409 and b["error"]["code"] == "email_taken", (s, b))
s, b = req("POST", "/auth/signup", {"email": "new1@example.com", "password": "short", "display_name": "X"})
check("R49 password<8 -> 422", s == 422 and b["error"]["code"] == "validation_failed", (s, b))
s, b = req("POST", "/auth/signup", {"email": "not-an-email", "password": "longenough1", "display_name": "X"})
check("R50 bad email shape -> 422", s == 422 and b["error"]["code"] == "validation_failed", (s, b))
s, b = req("POST", "/auth/login", {"email": "ada@example.com", "password": "wrong"})
check("R51 wrong password -> 401", s == 401 and b["error"]["code"] == "unauthenticated", (s, b))
s, b = req("POST", "/auth/login", {"email": "nobody@example.com", "password": "whatever1"})
check("R51 unknown email -> 401", s == 401 and b["error"]["code"] == "unauthenticated", (s, b))

# R53: auth required elsewhere
s, b = req("GET", "/me")
check("R53 GET /me with no token -> 401", s == 401, (s, b))
s, b = req("GET", "/me", token="garbage-token")
check("R53 GET /me with unknown token -> 401", s == 401, (s, b))

# R38/R39: error envelope shape on a 404
s, b = req("GET", "/nope", token=token_ada)
check("R38 error envelope shape", s == 404 and set(b["error"].keys()) == {"code", "message"}, (s, b))

# R16: ids opaque <=64 chars
check("R16 user_id <= 64 chars", len(me.get("user_id", "")) <= 64, me.get("user_id"))

# ---- T4 idempotency pipeline, probed via /_test/reset's own idempotent-looking neighbors isn't idempotent;
# use a stub-free approach: the five real idempotent paths don't exist yet in batch 1, so probe the
# *shared* pipeline behavior indirectly is not possible black-box. Instead verify header validation only
# where it is reachable: none of the 5 idempotent endpoints exist yet (expected: batches 2-4).
for path in ("/payments", "/requests", "/splits", "/settlements"):
    s, b = req("POST", path, {}, token=token_ada, headers={"Idempotency-Key": "k1"})
    check("%s not yet implemented (404, not batch-1 surface)" % path, s == 404, (s, b))

# R85/R86/R87/R88/R90/R92/R93: export/import round trip
s, exp = req("GET", "/_test/export")
check("R85 export shape", s == 200 and exp.get("track") == "pocketful" and exp.get("format_version") == 1, (s, exp))

s, b = req("POST", "/_test/import", exp)
check("R86 import accepts its own export unchanged -> 204", s == 204, (s, b))

s, me_after = req("GET", "/me", token=token_ada)
check("R91 token/balance survive export->import round trip", s == 200 and me_after.get("balance") == 10000, (s, me_after))

# R92: import removes previous destination data not in the imported set
s, b = req("POST", "/_test/reset", FIXTURE)
check("reset before import-removal probe -> 204", s == 204, (s, b))
s, tmp_login = req("POST", "/auth/login", {"email": "bob@example.com", "password": "correct horse"})
bob_token_before_import = tmp_login.get("token")
s, b = req("POST", "/_test/import", exp)  # import the earlier snapshot that has no 'bob' token from this fresh login
check("R92 import replaces destination -> 204", s == 204, (s, b))
s, b = req("GET", "/me", token=bob_token_before_import)
check("R92 destination token minted after export is invalidated by import", s == 401, (s, b))

# R93: reset clears imported state
s, b = req("POST", "/_test/reset", FIXTURE)
check("R93 reset after import -> 204", s == 204, (s, b))
s, b = req("GET", "/me", token=token_ada)
check("R93 reset invalidates tokens from before reset (old ada token gone)", s == 401, (s, b))

# R35: negative balance fixture -> 422, no state change
bad_fixture = json.loads(json.dumps(FIXTURE))
bad_fixture["users"][0]["balance"] = -1
s, b = req("POST", "/_test/reset", bad_fixture)
check("R35 negative balance fixture -> 422", s == 422 and b["error"]["code"] == "validation_failed", (s, b))

# confirm reset really changed nothing: previous fixture (ada=10000) should still be active
s, b = req("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
check("R35 no state change after rejected reset (ada can still log in from prior fixture)", s == 200, (s, b))

# R42: query int must be plain digits
s, b = req("POST", "/_test/reset", FIXTURE)
s, b = req("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
tok = b.get("token")

# R12: content-type
s, b = req("GET", "/health")
check("R10/R12 health ok", s == 200 and b == {"status": "ok"}, (s, b))

print()
print("TOTAL FAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
