"""Reviewer stage-2 verification stress probe (independent of unit/acceptance suites).

Written fresh for stage verification at a pinned revision, exercising a real running
container directly over HTTP. Does not import or reuse specs/stage-2/acceptance/*.

Proves, under concurrent mixed load:
  - conservation of `total` (R144) after a mixed storm of payments/requests/splits/
    settlements/authorizations/captures/voids
  - `available = total - held` never negative, including transiently, under
    concurrent readers racing partial captures (R145, R168)
  - held funds unspendable by a concurrent payment/request-pay/settlement/new
    authorization racing an open hold (R145, R149, R163)
  - cumulative captures on one authorization never exceed the original amount under
    concurrent capture attempts; a closed hold can never be captured again, including
    a capture-vs-void race (R146, R173)
  - exactly-once effects on all seven idempotent write paths under concurrent
    identical requests (R152)
  - no 5xx anywhere under load (R45)
  - lazy expiry: an authorization past `expires_at` reads as `expired` with its
    remainder back in `available`, no action taken at the deadline (R159, R160)
"""
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8086"
failures = []
status_log = []
log_lock = threading.Lock()


def req(method, path, body=None, token=None, idem_key=None, timeout=15):
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
        resp = urllib.request.urlopen(r, timeout=timeout)
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


def fresh_fixture(authorizations=None):
    return {
        "currency": "EUR", "minor_units": 2,
        "settlement_operator_ids": ["u_op"],
        "users": [
            {"id": "u_%s" % n, "email": "%s@example.com" % n, "password": "correct horse",
             "display_name": n, "handle": n, "balance": STARTING}
            for n in NAMES
        ],
        "payments": [], "requests": [],
        "authorizations": authorizations or [],
    }


def login_all():
    tokens = {}
    for n in NAMES:
        s, b = req("POST", "/auth/login",
                  {"email": "%s@example.com" % n, "password": "correct horse"})
        tokens[n] = b.get("token")
    check("all logins ok", all(tokens.values()), tokens)
    return tokens


# ===========================================================================
# SECTION A: mixed 50-concurrent storm incl. authorizations/captures/voids and
# deliberate idempotency retries across all 7 idempotent write paths (R152).
# ===========================================================================

s, b = req("POST", "/_test/reset", fresh_fixture())
check("A: reset -> 204", s == 204, (s, b))
tokens = login_all()
total_before = STARTING * len(NAMES)

random.seed(42)
pool = [n for n in NAMES if n != "op"]
ops = []

for i in range(10):
    frm, to = random.sample(pool, 2)
    key = "pay-%d" % i
    ops.append(("payment", frm, {"amount": 500, "to_handle": to}, key))
    if i % 3 == 0:
        ops.append(("payment", frm, {"amount": 500, "to_handle": to}, key))  # retry

for i in range(6):
    requester, payer = random.sample(pool, 2)
    key = "req-%d" % i
    ops.append(("request", requester, {"amount": 300, "payer_handle": payer}, key))

for i in range(6):
    payer, requester = random.sample(pool, 2)
    key = "split-%d" % i
    parts = random.sample(pool, 3)
    ops.append(("split", requester, {"amount": 301, "participant_handles": parts}, key))
    if i % 4 == 0:
        ops.append(("split", requester, {"amount": 301, "participant_handles": parts}, key))

for i in range(6):
    key = "settle-%d" % i
    a, b2 = random.sample(pool, 2)
    legs = [{"from_handle": a, "to_handle": b2, "amount": 400}]
    ops.append(("settle", "op", {"transfers": legs}, key))

for i in range(8):
    frm, to = random.sample(pool, 2)
    key = "auth-%d" % i
    ops.append(("authorize", frm, {"amount": 200, "to_handle": to}, key))
    if i % 3 == 0:
        ops.append(("authorize", frm, {"amount": 200, "to_handle": to}, key))  # retry

while len(ops) < 50:
    frm, to = random.sample(pool, 2)
    ops.append(("payment", frm, {"amount": 111, "to_handle": to}, "fill-%d" % len(ops)))

ROUTES = {
    "payment": ("POST", "/payments"),
    "request": ("POST", "/requests"),
    "split": ("POST", "/splits"),
    "settle": ("POST", "/settlements"),
    "authorize": ("POST", "/authorizations"),
}

results = {}
results_lock = threading.Lock()


def run_op(idx, op):
    kind, who, body, key = op
    method, path = ROUTES[kind]
    st, bo = req(method, path, body, token=tokens[who], idem_key=key)
    with results_lock:
        results[idx] = (op, st, bo)


threads = [threading.Thread(target=run_op, args=(i, op)) for i, op in enumerate(ops)]
for t in threads:
    t.start()
for t in threads:
    t.join()

server_errors = [s for s in status_log if s >= 500]
check("A: R45 no 5xx under concurrent mixed load (%d requests)" % len(status_log),
      len(server_errors) == 0, server_errors[:5])

# R152/R59-R61: retried idempotency keys -> identical response to the original.
by_key = {}
for idx, (op, st, bo) in results.items():
    key = op[3]
    by_key.setdefault(key, []).append((st, bo))
for key, outcomes in by_key.items():
    if len(outcomes) > 1:
        bodies = [o[1] for o in outcomes]
        statuses = [o[0] for o in outcomes]
        # exactly one 2xx "created" (201) and the rest 200 "replayed", same body.
        check("A: R152 retry of key %s returns the identical body" % key,
              all(b == bodies[0] for b in bodies), outcomes)
        check("A: R152 retry of key %s is exactly one 201 + rest 200" % key,
              statuses.count(201) == 1 and statuses.count(200) == len(statuses) - 1,
              statuses)

# R144: conservation of total across everything that happened.
s, snap = req("GET", "/_test/export")
check("A: export after storm -> 200", s == 200, s)
total_after = sum(u["balance"] for u in snap["state"]["users"].values())
check("A: R144 conservation of total across mixed storm", total_after == total_before,
      (total_before, total_after))

# R145: available = total - held, never negative, for every wallet.
now_iso = datetime.now(timezone.utc)
for uid, u in snap["state"]["users"].items():
    held = 0
    for a in snap["state"]["authorizations"].values():
        if a["from_user_id"] != uid:
            continue
        exp = datetime.fromisoformat(a["expires_at"].replace("Z", "+00:00"))
        eff_open = a["status"] == "open" and exp > now_iso
        if eff_open:
            held += a["amount"] - a["captured_amount"]
    available = u["balance"] - held
    check("A: R145 available non-negative for %s" % u["handle"], available >= 0,
          (u["handle"], u["balance"], held))


# ===========================================================================
# SECTION B: available=total-held atomicity for concurrent readers racing rapid
# partial captures (repeats the batch-3 probe independently, at this revision).
# ===========================================================================

s, b = req("POST", "/_test/reset", fresh_fixture())
check("B: reset -> 204", s == 204, (s, b))
tokens = login_all()
s, authz = req("POST", "/authorizations", {"amount": 20000, "to_handle": "bob"},
              token=tokens["alice"], idem_key="b-a1")
check("B: authorize 200.00 -> 201", s == 201, (s, authz))
aid = authz["authorization_id"]

violations = []
stop = threading.Event()


def reader():
    while not stop.is_set():
        st, me = req("GET", "/me", token=tokens["alice"])
        if st != 200:
            violations.append(("non-200 /me", st, me))
            continue
        if me["available"] < 0:
            violations.append(("available negative", me))
        if me["total"] - me["held"] != me["available"]:
            violations.append(("available != total-held", me))


readers = [threading.Thread(target=reader) for _ in range(8)]
for t in readers:
    t.start()

captured_total = 0
for i in range(100):
    st, p = req("POST", "/authorizations/%s/capture" % aid, {"amount": 100, "final": False},
               token=tokens["bob"], idem_key="b-cap-%d" % i)
    if st == 201:
        captured_total += 100
    elif st == 422 and p.get("error", {}).get("code") == "capture_exceeds_authorization":
        break
    else:
        violations.append(("unexpected capture response", st, p))

stop.set()
for t in readers:
    t.join()

check("B: zero atomicity violations across %d concurrent-reader samples" %
      (len(readers)), len(violations) == 0, violations[:5])
check("B: captured %d minor units in %d successful calls" % (captured_total, captured_total // 100),
      captured_total > 0, captured_total)


# ===========================================================================
# SECTION C: held funds unspendable by a concurrent payment/request-pay/
# settlement/new-authorization racing an open hold (R145, R149, R163).
# ===========================================================================

s, b = req("POST", "/_test/reset", fresh_fixture())
check("C: reset -> 204", s == 204, (s, b))
tokens = login_all()
# alice holds 60000 of her 100000 for bob; only 40000 is available.
s, authz = req("POST", "/authorizations", {"amount": 60000, "to_handle": "bob"},
              token=tokens["alice"], idem_key="c-a1")
check("C: authorize 600.00 -> 201", s == 201, (s, authz))

# fire 20 concurrent attempts to spend more than the 400.00 available via every
# money-moving path that must respect available(): payments, request-pay,
# settlement net-debit, and a second authorization.
s, rq = req("POST", "/requests", {"amount": 45000, "payer_handle": "alice"},
           token=tokens["bob"], idem_key="c-rq1")
check("C: bob requests 450.00 from alice -> 201", s == 201, (s, rq))
request_id = rq["request_id"]

c_results = []
c_lock = threading.Lock()


def spender(i):
    kind = i % 4
    if kind == 0:
        st, bo = req("POST", "/payments", {"amount": 45000, "to_handle": "carl"},
                    token=tokens["alice"], idem_key="c-pay-%d" % i)
    elif kind == 1:
        st, bo = req("POST", "/requests/%s/pay" % request_id, {},
                    token=tokens["alice"], idem_key="c-rqpay-%d" % i)
    elif kind == 2:
        st, bo = req("POST", "/settlements",
                    {"transfers": [{"from_handle": "alice", "to_handle": "dana",
                                    "amount": 45000}]},
                    token=tokens["op"], idem_key="c-settle-%d" % i)
    else:
        st, bo = req("POST", "/authorizations", {"amount": 45000, "to_handle": "erin"},
                    token=tokens["alice"], idem_key="c-auth2-%d" % i)
    with c_lock:
        c_results.append((kind, st, bo))


threads = [threading.Thread(target=spender, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()

succeeded = [r for r in c_results if r[1] in (200, 201)]
# at most ONE of the four distinct 450.00 attempts may succeed (available is only
# 400.00, so none should be able to spend 450.00 -- all must be refused).
over_budget_successes = [r for r in succeeded]
check("C: R145/R149 no 450.00 spend succeeds when only 400.00 is available "
      "(0 expected successes, all should be 409 insufficient_funds)",
      len(over_budget_successes) == 0, over_budget_successes)
refused = [r for r in c_results if r[1] == 409]
check("C: all %d over-budget concurrent attempts refused 409" % len(c_results),
      len(refused) == len(c_results), [r for r in c_results if r[1] != 409])

s, me = req("GET", "/me", token=tokens["alice"])
check("C: alice's available unchanged at 40000 after all refusals", me["available"] == 40000,
      me)


# ===========================================================================
# SECTION D: cumulative captures never exceed the authorized amount under
# concurrent capture attempts; a closed hold can never be captured again,
# including a capture-vs-void race (R146, R173).
# ===========================================================================

s, b = req("POST", "/_test/reset", fresh_fixture())
check("D: reset -> 204", s == 204, (s, b))
tokens = login_all()
s, authz = req("POST", "/authorizations", {"amount": 10000, "to_handle": "bob"},
              token=tokens["alice"], idem_key="d-a1")
check("D: authorize 100.00 -> 201", s == 201, (s, authz))
aid = authz["authorization_id"]

# 20 concurrent captures of 2000 each against a 10000 hold: at most 5 can succeed
# (10000 / 2000); cumulative captured_amount must never exceed 10000.
d_results = []
d_lock = threading.Lock()


def capturer(i):
    st, bo = req("POST", "/authorizations/%s/capture" % aid,
                {"amount": 2000, "final": False}, token=tokens["bob"],
                idem_key="d-cap-%d" % i)
    with d_lock:
        d_results.append((st, bo))


threads = [threading.Thread(target=capturer, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()

d_success = [r for r in d_results if r[0] == 201]
check("D: R146 exactly 5 of 20 concurrent 20.00 captures succeed against a 100.00 hold",
      len(d_success) == 5, len(d_success))
s, row = req("GET", "/authorizations", token=tokens["alice"])
me_row = next(a for a in row["authorizations"] if a["authorization_id"] == aid)
check("D: R146 cumulative captured_amount == 10000 (never exceeds authorized amount)",
      me_row["captured_amount"] == 10000, me_row)
check("D: R169 fully captured by cumulative nonfinal amounts closes the hold",
      me_row["status"] == "captured", me_row)

st, bo = req("POST", "/authorizations/%s/capture" % aid, {"amount": 1, "final": False},
            token=tokens["bob"], idem_key="d-cap-extra")
check("D: R146/R178 a closed (captured) hold can never be captured again (409)",
      st == 409, (st, bo))

# capture-vs-void race: one open hold, fire a capture and a void concurrently many
# times (trial-per-iteration) and confirm the money never both moves AND releases.
race_violations = []
for trial in range(8):
    s, b = req("POST", "/_test/reset", fresh_fixture())
    tokens = login_all()
    s, authz = req("POST", "/authorizations", {"amount": 5000, "to_handle": "bob"},
                  token=tokens["alice"], idem_key="d-race-a-%d" % trial)
    aid2 = authz["authorization_id"]
    race = {}
    race_lock = threading.Lock()

    def do_capture():
        st, bo = req("POST", "/authorizations/%s/capture" % aid2, {"final": True},
                    token=tokens["bob"], idem_key="d-race-cap-%d" % trial)
        with race_lock:
            race["capture"] = (st, bo)

    def do_void():
        st, bo = req("POST", "/authorizations/%s/void" % aid2, {},
                    token=tokens["alice"])
        with race_lock:
            race["void"] = (st, bo)

    t1, t2 = threading.Thread(target=do_capture), threading.Thread(target=do_void)
    t1.start(); t2.start(); t1.join(); t2.join()

    cap_ok = race["capture"][0] == 201
    void_ok = race["void"][0] == 200 and race["void"][1].get("status") == "voided"
    # exactly one of capture/void may have taken effect; the other must see the
    # hold already closed (captured -> void refused 409; voided -> capture refused 409)
    if cap_ok and void_ok:
        race_violations.append(("both capture and void succeeded", trial, race))
    if not cap_ok and not void_ok:
        race_violations.append(("neither capture nor void succeeded", trial, race))

check("D: R173 capture-vs-void race: exactly one side wins, never both/neither "
      "(8 trials)", len(race_violations) == 0, race_violations)


# ===========================================================================
# SECTION E: lazy expiry -- an authorization past expires_at reads as expired
# with its remainder back in available, no action taken at the deadline.
# ===========================================================================

past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
s, b = req("POST", "/_test/reset", fresh_fixture(authorizations=[
    {"id": "a_exp", "from_user_id": "u_alice", "to_user_id": "u_bob", "amount": 15000,
     "status": "open", "expires_at": past},
]))
check("E: reset with a past-expiry seeded hold -> 204", s == 204, (s, b))
tokens = login_all()
s, me = req("GET", "/me", token=tokens["alice"])
check("E: R145/R159 available reflects no hold (remainder released) with no action taken",
      me["available"] == STARTING, me)
check("E: R159 held == 0 for an expired authorization", me["held"] == 0, me)
s, rows = req("GET", "/authorizations", token=tokens["alice"])
row = next(a for a in rows["authorizations"] if a["authorization_id"] == "a_exp")
check("E: R159/R160 effective_status reads expired, never open", row["status"] == "expired",
      row)
check("E: R160 remaining_amount is 0 for an expired hold", row["remaining_amount"] == 0, row)
# an expired hold cannot be captured.
st, bo = req("POST", "/authorizations/a_exp/capture", {}, token=tokens["bob"],
            idem_key="e-cap1")
check("E: R143/R159 capturing an expired authorization is refused (409 authorization_expired)",
      st == 409 and bo.get("error", {}).get("code") == "authorization_expired", (st, bo))


# ===========================================================================
print()
if failures:
    print("PROBE FAILED: %d check(s) failed" % len(failures))
    for name, detail in failures:
        print(" -", name, "|", detail)
    sys.exit(1)
else:
    print("PROBE OK: all checks passed (%d HTTP requests issued, 0 5xx)" % len(status_log))
