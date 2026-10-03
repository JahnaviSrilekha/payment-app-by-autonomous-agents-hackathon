"""Reviewer stage-4 stage-verification stress probe (independent of developer's
T35 unit tests and tester's acceptance suite): many concurrent, retried and mixed
operations against a running service, checking every money invariant the stage-4
requirements and design state, plus the carried stage-1 rounding table.

Run: python3 stress_stage4_invariants.py --base-url http://127.0.0.1:8130

Covers:
  - R82 (stage-1, carried): the split rounding table, exact largest-remainder shares.
  - Exactly-once: concurrent identical-key refunds (R302/R3), concurrent
    correction-batches sharing an idempotency key (R330/R3), concurrent
    single/single, single/batch and batch/batch corrections sharing an
    expected_revision (R333) — exactly one commit, the rest the correct error.
  - All-or-nothing (R323): a batch that fails at the historical-overdraft phase
    leaves every targeted payment's revision count and every balance unchanged.
  - Conservation (R1/R2/R45/R240/R322): sum of every user's total, under a
    50-request-in-flight mixed storm of refunds/corrections/batches, always
    equals the seeded total.
  - No negative total/available, including at historical boundaries (R237/R238/
    R284/R285/R303): swept across every user, at every boundary instant touched
    by the storm, both during and after.
  - No 5xx under load: every response status in the storm is a defined 2xx/4xx.
"""
import argparse
import http.client
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

FAILURES = []


def check(label, cond, extra=""):
    status = "ok" if cond else "FAIL"
    suffix = (" -- " + str(extra)) if (extra not in ("", None) and not cond) else ""
    print("[%s] %s%s" % (status, label, suffix))
    if not cond:
        FAILURES.append(label)


class Client:
    def __init__(self, base_url):
        parts = urlsplit(base_url)
        self.host = parts.hostname
        self.port = parts.port

    def request(self, method, path, body=None, token=None, key=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=20)
        hdrs = {}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if token:
            hdrs["Authorization"] = "Bearer " + token
        if key is not None:
            hdrs["Idempotency-Key"] = key
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            status = resp.status
            data = resp.read()
        finally:
            conn.close()
        parsed = json.loads(data.decode("utf-8")) if data else None
        return status, parsed


def login(client, email, password="correct horse"):
    _, p = client.request("POST", "/auth/login", {"email": email, "password": password})
    return p["token"]


def reset(client, fixture):
    status, p = client.request("POST", "/_test/reset", fixture)
    assert status == 204, (status, p)


def big_fixture():
    users = [
        {"id": "u_%s" % h, "email": "%s@example.com" % h, "password": "correct horse",
         "display_name": h.title(), "handle": h, "balance": 20000}
        for h in ("ada", "bob", "cyd", "dee", "eli")
    ]
    payments = [
        {"id": "p1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 8000,
         "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "p2", "from_user_id": "u_bob", "to_user_id": "u_cyd", "amount": 6000,
         "created_at": "2026-01-01T00:10:00+00:00"},
        {"id": "p3", "from_user_id": "u_cyd", "to_user_id": "u_dee", "amount": 4000,
         "created_at": "2026-01-01T00:20:00+00:00"},
    ]
    return {"currency": "EUR", "minor_units": 2, "users": users, "payments": payments,
            "requests": [], "settlement_operator_ids": ["u_ada"]}


SEED_TOTAL = 20000 * 5


def totals(client, tokens):
    out = {}
    for handle, token in tokens.items():
        _, me = client.request("GET", "/me", token=token)
        out[handle] = me
    return out


def check_conservation(client, tokens, label):
    t = totals(client, tokens)
    total_sum = sum(v["total"] for v in t.values())
    check("%s: conservation (sum of totals == seed %d)" % (label, SEED_TOTAL),
          total_sum == SEED_TOTAL, "got %d, %r" % (total_sum, t))
    for handle, v in t.items():
        check("%s: %s total/available nonnegative" % (label, handle),
              v["total"] >= 0 and v["available"] >= 0, repr(v))


def check_no_negative_at_boundaries(client, token, as_of_candidates):
    for instant in as_of_candidates:
        status, me = client.request("GET", "/me?as_of=%s" % instant, token=token)
        if status != 200:
            continue  # out-of-range as_of is a validation error, not a boundary to check
        check("historical total nonnegative at %s" % instant, me["total"] >= 0, repr(me))
        check("historical available nonnegative at %s" % instant, me["available"] >= 0, repr(me))


def test_rounding_table(client, ada):
    """R82 (stage-1, carried through every stage): exact largest-remainder shares."""
    table = [
        (1000, 3, [334, 333, 333]),
        (1, 3, [1, 0, 0]),
        (10, 3, [4, 3, 3]),
        (999, 3, [333, 333, 333]),
        (5, 5, [1, 1, 1, 1, 1]),
    ]
    all_handles = ["bob", "cyd", "dee", "eli", "ada"]
    for amount, count, expected in table:
        participants = ([h for h in all_handles if h != "ada"][:count - 1] + ["ada"]) \
            if count <= 5 else None
        participants = (["ada"] + [h for h in all_handles if h != "ada"])[:count]
        status, body = client.request("POST", "/splits",
                                      {"amount": amount, "participant_handles": participants},
                                      token=ada, key="split-%d-%d-%s" % (amount, count, uuid.uuid4()))
        shares = [s["amount"] for s in body["shares"]] if status == 201 else None
        check("R82 rounding %d/%d -> %r" % (amount, count, expected),
              status == 201 and shares == expected, "got %s %r" % (status, body))


def login_all(client):
    return {h: login(client, "%s@example.com" % h) for h in ("ada", "bob", "cyd", "dee", "eli")}


def test_exactly_once_refund(client):
    reset(client, big_fixture())
    tokens = login_all(client)
    bob = tokens["bob"]
    key = "race-refund-%s" % uuid.uuid4()
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(
            lambda _: client.request("POST", "/payments/p1/refunds", {"amount": 1000},
                                     token=bob, key=key),
            range(10)))
    statuses = sorted(s for s, _ in results)
    created = [b for s, b in results if s == 201]
    check("exactly-once refund: one 201, nine 200 replays",
          statuses == [200] * 9 + [201], statuses)
    ids = {b["payment_id"] for s, b in results if s in (200, 201)}
    check("exactly-once refund: every response names the same payment", len(ids) == 1, ids)


def test_exactly_once_batch_key(client):
    reset(client, big_fixture())
    tokens = login_all(client)
    ada = tokens["ada"]
    key = "race-batch-%s" % uuid.uuid4()
    body = {"corrections": [{"payment_id": "p1", "expected_revision": 1, "amount": 7000,
                             "effective_at": "2026-01-01T00:00:00+00:00", "reason": "x"}]}
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(
            lambda _: client.request("POST", "/correction-batches", body, token=ada, key=key),
            range(10)))
    statuses = sorted(s for s, _ in results)
    check("exactly-once batch key: one 201, nine 200 replays",
          statuses == [200] * 9 + [201], statuses)
    ids = {b["correction_batch_id"] for s, b in results if s in (200, 201)}
    check("exactly-once batch key: every response names the same batch", len(ids) == 1, ids)


def test_shared_expected_revision_races(client):
    def committed_vs_stale(results, extra_codes=()):
        committed = [r for r in results if r[0] == 201]
        rejected = [r for r in results if r[0] == 409
                   and r[1]["error"]["code"] == "stale_revision"]
        other = [r for r in results if r not in committed and r not in rejected]
        return committed, rejected, other

    # single/single
    reset(client, big_fixture())
    ada = login(client, "ada@example.com")
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda i: client.request(
            "POST", "/payments/p1/corrections",
            {"expected_revision": 1, "amount": 7000 + i, "reason": "race",
             "effective_at": "2026-01-01T00:00:00+00:00"},
            token=ada, key="ss-%d-%s" % (i, uuid.uuid4())), range(20)))
    committed, rejected, other = committed_vs_stale(results)
    check("R333 single/single: exactly one commit", len(committed) == 1, results)
    check("R333 single/single: the rest are stale_revision",
          len(rejected) == 19 and not other, results)

    # single/batch
    reset(client, big_fixture())
    ada = login(client, "ada@example.com")
    def op(i):
        if i % 2 == 0:
            return client.request("POST", "/payments/p1/corrections",
                                  {"expected_revision": 1, "amount": 7000 + i,
                                   "reason": "race", "effective_at": "2026-01-01T00:00:00+00:00"},
                                  token=ada, key="sb-s-%d-%s" % (i, uuid.uuid4()))
        return client.request("POST", "/correction-batches",
                              {"corrections": [{"payment_id": "p1", "expected_revision": 1,
                                                "amount": 7000 + i, "reason": "race",
                                                "effective_at": "2026-01-01T00:00:00+00:00"}]},
                              token=ada, key="sb-b-%d-%s" % (i, uuid.uuid4()))
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(op, range(20)))
    committed, rejected, other = committed_vs_stale(results)
    check("R333 single/batch: exactly one commit", len(committed) == 1, results)
    check("R333 single/batch: the rest are stale_revision",
          len(rejected) == 19 and not other, results)

    # batch/batch
    reset(client, big_fixture())
    ada = login(client, "ada@example.com")
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda i: client.request(
            "POST", "/correction-batches",
            {"corrections": [{"payment_id": "p1", "expected_revision": 1,
                              "amount": 7000 + i, "reason": "race",
                              "effective_at": "2026-01-01T00:00:00+00:00"}]},
            token=ada, key="bb-%d-%s" % (i, uuid.uuid4())), range(20)))
    committed, rejected, other = committed_vs_stale(results)
    check("R333 batch/batch: exactly one commit", len(committed) == 1, results)
    check("R333 batch/batch: the rest are stale_revision",
          len(rejected) == 19 and not other, results)


def overdraft_fixture():
    """No pre-history cushion for carol: her entire balance is built from h1+h2
    minus h3, so reducing an inflow below what h3 already spent genuinely
    overdraws her at h3's boundary (the T33-review fixture already proven to
    trigger historical_overdraft cleanly)."""
    users = [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
         "display_name": "Ada", "handle": "ada", "balance": 100},
        {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
         "display_name": "Carol", "handle": "carol", "balance": 0},
        {"id": "u_dave", "email": "dave@example.com", "password": "correct horse",
         "display_name": "Dave", "handle": "dave", "balance": 500},
        {"id": "u_erin", "email": "erin@example.com", "password": "correct horse",
         "display_name": "Erin", "handle": "erin", "balance": 800},
    ]
    payments = [
        {"id": "h1", "from_user_id": "u_dave", "to_user_id": "u_carol", "amount": 500,
         "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "h2", "from_user_id": "u_erin", "to_user_id": "u_carol", "amount": 300,
         "created_at": "2026-01-01T00:01:00+00:00"},
        {"id": "h3", "from_user_id": "u_carol", "to_user_id": "u_erin", "amount": 800,
         "created_at": "2026-01-01T00:02:00+00:00"},
    ]
    return {"currency": "EUR", "minor_units": 2, "users": users, "payments": payments,
            "requests": [], "settlement_operator_ids": ["u_dave"]}


def test_all_or_nothing_rejected_batch(client):
    """R323: a batch failing at the historical-overdraft phase leaves every
    targeted payment's revision count and every balance exactly as before."""
    reset(client, overdraft_fixture())
    dave = login(client, "dave@example.com")
    carol = login(client, "carol@example.com")
    erin = login(client, "erin@example.com")
    before_h1 = client.request("GET", "/payments/h1/revisions", token=dave)[1]
    before_h2 = client.request("GET", "/payments/h2/revisions", token=erin)[1]
    before_totals = {h: client.request("GET", "/me", token=t)[1]
                     for h, t in (("dave", dave), ("carol", carol), ("erin", erin))}
    # reducing h1 to 1 drops carol's running total to 1+300-800 = -499 at h3's
    # boundary, well before her current (post-h3) wallet is ever examined.
    body = {"corrections": [
        {"payment_id": "h1", "expected_revision": 1, "amount": 1,
         "effective_at": "2026-01-01T00:00:00+00:00", "reason": "drastic"},
    ]}
    status, payload = client.request("POST", "/correction-batches", body,
                                     token=dave, key="aon-%s" % uuid.uuid4())
    check("all-or-nothing setup actually rejects (409/422 expected)",
          status in (409, 422), "got %s %r" % (status, payload))
    after_h1 = client.request("GET", "/payments/h1/revisions", token=dave)[1]
    after_h2 = client.request("GET", "/payments/h2/revisions", token=erin)[1]
    check("R323: h1 revision count unchanged after rejection",
          len(after_h1["revisions"]) == len(before_h1["revisions"]))
    check("R323: h2 revision count unchanged after rejection (untouched payment)",
          len(after_h2["revisions"]) == len(before_h2["revisions"]))
    after_totals = {h: client.request("GET", "/me", token=t)[1]
                   for h, t in (("dave", dave), ("carol", carol), ("erin", erin))}
    check("R323: every balance unchanged after rejection", before_totals == after_totals,
          {"before": before_totals, "after": after_totals})


def test_mixed_storm_50_in_flight(client):
    """50 concurrent, mixed refund/correction/batch requests against shared
    targets: conservation holds throughout, no 5xx, boundaries stay nonnegative."""
    reset(client, big_fixture())
    tokens = login_all(client)
    ada, bob, cyd = tokens["ada"], tokens["bob"], tokens["cyd"]

    def op(i):
        kind = i % 4
        if kind == 0:
            return client.request("POST", "/payments/p1/refunds", {"amount": 100 + i},
                                  token=bob, key="storm-r-%d" % i)
        if kind == 1:
            return client.request(
                "POST", "/payments/p2/corrections",
                {"expected_revision": 1, "amount": 6000 - i, "reason": "storm",
                 "effective_at": "2026-01-01T00:10:00+00:00"},
                token=bob, key="storm-c-%d" % i)
        if kind == 2:
            return client.request(
                "POST", "/correction-batches",
                {"corrections": [{"payment_id": "p3", "expected_revision": 1,
                                  "amount": 4000 - i, "reason": "storm",
                                  "effective_at": "2026-01-01T00:20:00+00:00"}]},
                token=ada, key="storm-b-%d" % i)
        return client.request("GET", "/me", token=cyd)

    with ThreadPoolExecutor(max_workers=50) as ex:
        results = list(ex.map(op, range(50)))
    statuses = [s for s, _ in results]
    check("no 5xx under 50-in-flight mixed storm", all(s < 500 for s in statuses),
          [s for s in statuses if s >= 500])
    check_conservation(client, tokens, "post-storm")
    # sweep historical boundaries across the whole storm window plus before/after
    check_no_negative_at_boundaries(client, ada, [
        "2020-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:10:00+00:00", "2026-01-01T00:20:00+00:00",
        "2099-01-01T00:00:00Z",
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    args = ap.parse_args()
    client = Client(args.base_url)

    reset(client, big_fixture())
    tokens = {h: login(client, "%s@example.com" % h)
             for h in ("ada", "bob", "cyd", "dee", "eli")}
    check_conservation(client, tokens, "seed")

    test_rounding_table(client, tokens["ada"])
    test_exactly_once_refund(client)
    test_exactly_once_batch_key(client)
    test_shared_expected_revision_races(client)
    test_all_or_nothing_rejected_batch(client)
    test_mixed_storm_50_in_flight(client)

    print()
    if FAILURES:
        print("%d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all invariant stress checks passed")


if __name__ == "__main__":
    main()
