"""Reviewer probe for the batch-3 (T7 /splits) resubmission a937234.

Verifies the precedence fix: note-length validation (422) must run before the
unknown-participant-handle check (404) in create_split, per R79's error table and
design.md section 9 step 4 (field validation) preceding step 5 (resource checks).
This is the exact combined case that caused the first rejection (msg 88e1f942).
"""
import json, sys, urllib.request, urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:57601"
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
        resp = urllib.request.urlopen(r)
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


FIXTURE = {
    "currency": "EUR", "minor_units": 2,
    "users": [
        {"id": "u_sol", "email": "sol@example.com", "password": "correct horse",
         "display_name": "Sol", "handle": "sol", "balance": 10000},
    ],
    "payments": [], "requests": [],
}

s, b = req("POST", "/_test/reset", FIXTURE)
check("reset -> 204", s == 204, (s, b))
s, b = req("POST", "/auth/login", {"email": "sol@example.com", "password": "correct horse"})
token = b.get("token")
check("login ok", s == 200 and token, (s, b))

# The exact combined case from the rejection: unknown handle AND note too long.
# Field validation (note) must win -> 422 validation_failed, not 404 not_found.
s, b = req("POST", "/splits",
           {"amount": 100, "participant_handles": ["nobody"], "note": "x" * 201},
           token=token, idem_key="probe-combined-1")
check("combined case: note>200 + unknown handle -> 422 validation_failed (not 404)",
      s == 422 and b.get("error", {}).get("code") == "validation_failed", (s, b))

# Sanity: unknown handle alone still 404 (resource check still reachable/correct).
s, b = req("POST", "/splits", {"amount": 100, "participant_handles": ["nobody"]}, token=token, idem_key="probe-unknown-1")
check("unknown handle alone -> 404 not_found", s == 404 and b.get("error", {}).get("code") == "not_found", (s, b))

# Sanity: note alone too long, valid handle -> 422.
s, b = req("POST", "/splits", {"amount": 100, "participant_handles": ["sol"], "note": "x" * 201}, token=token, idem_key="probe-note-1")
check("note>200 alone (valid handle) -> 422 validation_failed", s == 422 and b.get("error", {}).get("code") == "validation_failed", (s, b))

print()
print("TOTAL FAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
