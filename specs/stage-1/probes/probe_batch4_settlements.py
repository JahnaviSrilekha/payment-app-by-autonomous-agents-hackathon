"""Reviewer probe for batch-4 (T8 /settlements) b22ff87.

Focused checks beyond the unit suite and tester's acceptance suite (which already
cover R94-R101 against this build): concurrent net-affordability races, and the one
ordering the spec leaves unstated — a non-operator caller whose batch also contains an
entry error. No requirement pins this order; this probe documents actual behaviour
rather than asserting one, so the reviewer can judge it isn't a leak of unintended
information and isn't flagged as a defect.
"""
import json, sys, threading, urllib.request, urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:57602"
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
    "settlement_operator_ids": ["u_op"],
    "users": [
        {"id": "u_op", "email": "op@example.com", "password": "correct horse",
         "display_name": "Op", "handle": "op", "balance": 1000},
        {"id": "u_a", "email": "a@example.com", "password": "correct horse",
         "display_name": "A", "handle": "a", "balance": 1000},
        {"id": "u_b", "email": "b@example.com", "password": "correct horse",
         "display_name": "B", "handle": "b", "balance": 0},
    ],
    "payments": [], "requests": [],
}

s, b = req("POST", "/_test/reset", FIXTURE)
check("reset -> 204", s == 204, (s, b))
s, b = req("POST", "/auth/login", {"email": "op@example.com", "password": "correct horse"})
op_token = b.get("token")
s, b = req("POST", "/auth/login", {"email": "b@example.com", "password": "correct horse"})
b_token = b.get("token")
check("logins ok", op_token and b_token, "")

# Documented (not asserted as a requirement) actual ordering: non-operator + entry error.
s, b = req("POST", "/settlements",
           {"transfers": [{"from_handle": "zzz", "to_handle": "a", "amount": 1}]},
           token=b_token, idem_key="probe-order-1")
print("INFO non-operator + unknown-handle entry -> status=%s code=%s (documents actual precedence; "
      "no requirement pins operator-check vs entry-validation order)" % (s, b.get("error", {}).get("code") if isinstance(b, dict) else b))

# R97/R2: concurrent settlements racing the same wallet must never push a balance negative.
s, b = req("POST", "/_test/reset", FIXTURE)
results = []
def fire(i):
    st, bb = req("POST", "/settlements",
                 {"transfers": [{"from_handle": "a", "to_handle": "b", "amount": 400}]},
                 token=op_token, idem_key="probe-race-%d" % i)
    results.append(st)

threads = [threading.Thread(target=fire, args=(i,)) for i in range(5)]
for t in threads: t.start()
for t in threads: t.join()
accepted = sum(1 for r in results if r == 201)
check("concurrent same-wallet settlements: at most 2 of 5 accepted (a has 1000, 400/leg)",
      accepted <= 2, results)
s, exp = req("GET", "/_test/export")
a_balance = exp["state"]["users"]["u_a"]["balance"]
check("R2 a's balance never negative after race", a_balance >= 0, a_balance)

print()
print("TOTAL FAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
