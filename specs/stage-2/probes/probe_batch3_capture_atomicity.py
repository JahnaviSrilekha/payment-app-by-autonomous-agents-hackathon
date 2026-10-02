"""Reviewer probe for batch 3 (T11-T15), stage 2.

Not part of the acceptance suite. Checks an invariant the suite's own concurrency
tests don't isolate: that `available = total - held` (R145) is never observed
inconsistent by a concurrent reader while a receiver fires many small partial
captures (R168 - remainder release must be atomic with the capture's payment,
in the same critical section, for every capture, not just the final one).

Usage: start a stage-2 build, then
    python3 probe_batch3_capture_atomicity.py --base-url http://127.0.0.1:PORT
"""
import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request


def req(base, method, path, body=None, token=None, key=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    args = ap.parse_args()
    base = args.base_url

    fx = {
        "currency": "EUR",
        "minor_units": 2,
        "payments": [],
        "requests": [],
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "hunter2hunter2",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "hunter2hunter2",
             "display_name": "Bob", "handle": "bob", "balance": 10000},
        ],
    }
    status, _ = req(base, "POST", "/_test/reset", fx)
    assert status == 204, f"reset failed: {status}"

    status, ada = req(base, "POST", "/auth/login",
                      {"email": "ada@example.com", "password": "hunter2hunter2"})
    assert status == 200, ada
    status, bob = req(base, "POST", "/auth/login",
                      {"email": "bob@example.com", "password": "hunter2hunter2"})
    assert status == 200, bob
    ada_tok, bob_tok = ada["token"], bob["token"]

    status, authz = req(base, "POST", "/authorizations", {"to_handle": "bob", "amount": 10000},
                        token=ada_tok, key="probe-a1")
    assert status == 201, authz
    aid = authz["authorization_id"]

    violations = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            s, me = req(base, "GET", "/me", token=ada_tok)
            if s != 200:
                violations.append(f"GET /me non-200: {s} {me}")
                continue
            if me["available"] < 0:
                violations.append(f"available went negative: {me}")
            if me["total"] - me["held"] != me["available"]:
                violations.append(f"available != total-held: {me}")

    readers = [threading.Thread(target=reader) for _ in range(8)]
    for t in readers:
        t.start()

    captured_total = 0
    for i in range(100):
        s, p = req(base, "POST", f"/authorizations/{aid}/capture",
                  {"amount": 50, "final": False}, token=bob_tok, key=f"probe-cap-{i}")
        if s == 201:
            captured_total += 50
        elif s == 422 and p.get("error", {}).get("code") == "capture_exceeds_authorization":
            break
        else:
            violations.append(f"unexpected capture response: {s} {p}")

    stop.set()
    for t in readers:
        t.join()

    status, me_final = req(base, "GET", "/me", token=ada_tok)
    assert status == 200
    status, auths = req(base, "GET", "/authorizations", token=ada_tok)
    assert status == 200
    a = next(a for a in auths["authorizations"] if a["authorization_id"] == aid)

    if a["captured_amount"] != captured_total:
        violations.append(
            f"captured_amount mismatch: row says {a['captured_amount']}, "
            f"client counted {captured_total} successful captures")
    if me_final["total"] != 10000 - captured_total:
        violations.append(
            f"conservation: total {me_final['total']} != 10000 - {captured_total}")
    if me_final["held"] != 10000 - captured_total:
        violations.append(
            f"held should equal the uncaptured remainder: {me_final}")

    if violations:
        print(f"PROBE FAILED ({len(violations)} violation(s)):")
        for v in violations[:20]:
            print(" -", v)
        sys.exit(1)
    print(f"PROBE OK: {captured_total} captured in {captured_total // 50} calls, "
          f"available/held/total consistent across {len(readers)} concurrent readers, "
          f"zero negative-available or inconsistency observations.")


if __name__ == "__main__":
    main()
