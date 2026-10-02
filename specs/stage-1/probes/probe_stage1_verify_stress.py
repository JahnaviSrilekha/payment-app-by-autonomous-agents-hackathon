"""Reviewer stage-1 verification stress probe (independent of unit/acceptance suites).

Fires up to 50 concurrent, mixed, partly-retried requests (payments, requests+pay,
splits, settlements) against a real running container and checks, after the dust
settles, the money invariants the design states: conservation (R1), no balance ever
negative (R2), exactly-once effects for retried idempotency keys (R3), all-or-nothing
settlements (R98), exact rounding (R82-R84), and no 5xx anywhere (R45).
"""
import json, sys, threading, urllib.request, urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:18080"
failures = []
status_log = []
log_lock = threading.Lock()


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
    with log_lock:
        status_log.append(status)
    return status, parsed


def check(name, cond, detail=""):
    if not cond:
        failures.append((name, detail))
        print("FAIL", name, detail)
    else:
        print("ok  ", name)


NAMES = ["alice", "bob", "carl", "dana", "erin", "op"]
STARTING = 100000  # minor units each

FIXTURE = {
    "currency": "EUR", "minor_units": 2,
    "settlement_operator_ids": ["u_op"],
    "users": [
        {"id": "u_%s" % n, "email": "%s@example.com" % n, "password": "correct horse",
         "display_name": n, "handle": n, "balance": STARTING}
        for n in NAMES
    ],
    "payments": [], "requests": [],
}

s, b = req("POST", "/_test/reset", FIXTURE)
check("reset -> 204", s == 204, (s, b))

tokens = {}
for n in NAMES:
    s, b = req("POST", "/auth/login", {"email": "%s@example.com" % n, "password": "correct horse"})
    tokens[n] = b.get("token")
check("all logins ok", all(tokens.values()), tokens)

total_before = STARTING * len(NAMES)

# ---- Build a mixed batch of 50 concurrent ops, including deliberate idempotency
# retries (same key fired twice) to check exactly-once (R3).
import random
random.seed(42)
ops = []

for i in range(15):
    frm, to = random.sample([n for n in NAMES if n != "op"], 2)
    key = "pay-%d" % i
    ops.append(("payment", frm, to, key, False))
    if i % 3 == 0:
        ops.append(("payment", frm, to, key, True))  # exact retry, same key

for i in range(10):
    payer, requester = random.sample([n for n in NAMES if n != "op"], 2)
    key = "split-%d" % i
    parts = random.sample([n for n in NAMES if n != "op"], 3)
    ops.append(("split", requester, parts, key, False))

for i in range(10):
    key = "settle-%d" % i
    legs = []
    pool = [n for n in NAMES if n != "op"]
    a, b2 = random.sample(pool, 2)
    legs.append({"from_handle": a, "to_handle": b2, "amount": 777})
    ops.append(("settle", "op", legs, key, False))
    if i % 4 == 0:
        ops.append(("settle", "op", legs, key, True))

while len(ops) < 50:
    frm, to = random.sample([n for n in NAMES if n != "op"], 2)
    ops.append(("payment", frm, to, "fill-%d" % len(ops), False))

results = {}
results_lock = threading.Lock()


def run_op(idx, op):
    kind, who, arg, key, is_retry = op
    if kind == "payment":
        to = arg
        st, bo = req("POST", "/payments", {"amount": 500, "to_handle": to}, token=tokens[who], idem_key=key)
    elif kind == "split":
        parts = arg
        st, bo = req("POST", "/splits", {"amount": 301, "participant_handles": parts}, token=tokens[who], idem_key=key)
    elif kind == "settle":
        st, bo = req("POST", "/settlements", {"transfers": arg}, token=tokens[who], idem_key=key)
    with results_lock:
        results[idx] = (op, st, bo)


threads = [threading.Thread(target=run_op, args=(i, op)) for i, op in enumerate(ops)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# R45: no 5xx anywhere.
server_errors = [s for s in status_log if s >= 500]
check("R45 no 5xx under concurrent mixed load (%d requests)" % len(status_log), len(server_errors) == 0, server_errors[:5])

# R3: idempotent retries return the identical response as the original.
by_key = {}
for idx, (op, st, bo) in results.items():
    key = op[3]
    by_key.setdefault(key, []).append((st, bo))
for key, pairs in by_key.items():
    if len(pairs) > 1:
        bodies = [bo for st, bo in pairs]
        statuses = [st for st, bo in pairs]
        check("R3 retry of key=%s: identical body, exactly one 201 + rest 200 (replay)" % key,
              all(bo == bodies[0] for bo in bodies)
              and statuses.count(201) == 1 and all(s in (200, 201) for s in statuses),
              pairs)

# R1/R2: conservation + no negative balance, read via export (ground truth).
s, exp = req("GET", "/_test/export")
balances = {u["handle"]: u["balance"] for u in exp["state"]["users"].values()}
total_after = sum(balances.values())
check("R1 conservation: total balance unchanged (%d)" % total_before, total_after == total_before,
      (total_before, total_after, balances))
check("R2 no balance ever negative", all(v >= 0 for v in balances.values()), balances)

# R98: every settlement that committed (201) is reflected by matching payment legs
# with a shared settlement_id and committed_at == created_at on every member (all-or-nothing).
settle_201 = [(op, bo) for idx, (op, st, bo) in results.items() if op[0] == "settle" and st == 201]
for op, bo in settle_201:
    sid = bo.get("settlement_id")
    committed_at = bo.get("committed_at")
    check("R98 settlement %s has %d payments, each created_at==committed_at" % (sid, len(bo.get("payments", []))),
          len(bo.get("payments", [])) > 0 and all(p.get("created_at") == committed_at for p in bo.get("payments", [])),
          bo)

# R82-R84: split shares sum exactly to amount (exact rounding, no drift).
split_201 = [(op, bo) for idx, (op, st, bo) in results.items() if op[0] == "split" and st == 201]
for op, bo in split_201:
    shares = bo.get("shares", [])
    check("R82 split shares sum to amount (301) exactly", sum(sh["amount"] for sh in shares) == 301, shares)

print()
print("ops: %d, statuses seen: %s" % (len(status_log), sorted(set(status_log))))
print("TOTAL FAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
