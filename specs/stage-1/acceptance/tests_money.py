"""Stage-1 acceptance tests: payments, requests, splits, settlements, export/import."""
import json

from harness import (RFC3339, Resp, assert_conservation, fu, run_concurrently, std_fixture, test,
                     unique_key, Users)


def feed(h, token, limit=200):
    r = h.request("GET", "/activity?limit=%d" % limit, token=token)
    return r.json or {}


def feed_ids(h, token):
    return [p.get("payment_id") or p.get("id") for p in feed(h, token).get("payments", [])]


# ------------------------------------------------------------------ payments

@test("r65_payment_create_schema", "R65", "R64", "R23", needs=('payments', 'me'))
def r65_payment_schema(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 1500, "note": "dinner", "visibility": "public"})
    ck.eq(p.status, 201, "payment created")
    j = p.json or {}
    ck.eq(j.get("from_user_id"), "u_ada", "from_user_id")
    ck.eq(j.get("from_handle"), "ada", "from_handle")
    ck.eq(j.get("to_user_id"), "u_bob", "to_user_id")
    ck.eq(j.get("to_handle"), "bob", "to_handle")
    ck.eq(j.get("amount"), 1500, "amount")
    ck.eq(j.get("currency"), "EUR", "currency")
    ck.eq(j.get("note"), "dinner", "note")
    ck.eq(j.get("visibility"), "public", "visibility")
    ck.true("request_id" in j and j.get("request_id") is None, "request_id null")
    ck.true(isinstance(j.get("payment_id"), str) and j["payment_id"], "payment_id")
    ck.true(RFC3339.match(j.get("created_at") or ""), "created_at RFC3339")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 8500, "ada debited")
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), 4000, "bob credited")


@test("r65_payment_defaults", "R65", "R41", needs=('payments',))
def r65_payment_defaults(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "cy", "amount": 10})
    ck.eq(p.status, 201, "note/visibility omitted accepted")
    j = p.json or {}
    ck.eq(j.get("note"), "", "note default empty string")
    ck.eq(j.get("visibility"), "public", "visibility default public")


@test("r18_amount_integral_numeric", "R18", "R4", "R41", needs=('payments',))
def r18_amount_integral(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    moved = 0
    r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 1500})
    ck.eq(r.status, 201, "JSON integer 1500 accepted")
    ck.eq((r.json or {}).get("amount"), 1500, "amount value preserved")
    moved += 1500
    r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 1000.0})
    ck.eq(r.status, 201, "1000.0 accepted (integral numeric value)")
    ck.eq((r.json or {}).get("amount"), 1000, "1000.0 -> 1000")
    moved += 1000
    r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  raw='{"to_handle": "bob", "amount": 1e3}')
    ck.eq(r.status, 201, "1e3 accepted (integral numeric)")
    ck.eq((r.json or {}).get("amount"), 1000, "1e3 -> 1000")
    moved += 1000
    for bad in ['"1000"', "true", "1000.5"]:
        r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      raw='{"to_handle": "bob", "amount": %s}' % bad)
        ck.is_err(r, 422, "validation_failed", "amount %s is not an integral number" % bad)
    r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  raw='{"to_handle": "bob", "amount": null}')
    ck.true(r.status in (400, 422), "amount null rejected (400 or 422)", str(r.status))
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), 2500 + moved,
          "bob credited exactly the integral amounts")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r66_payment_errors", "R66", needs=('payments',))
def r66_payment_errors(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    cases = [
        ({"to_handle": "bob", "amount": 10001}, 409, "insufficient_funds", "insufficient funds"),
        ({"to_handle": "bob", "amount": 0}, 422, "validation_failed", "amount 0"),
        ({"to_handle": "bob", "amount": -5}, 422, "validation_failed", "negative amount"),
        ({"to_handle": "bob", "amount": 1000000001}, 422, "validation_failed", "amount above 1e9"),
        ({"to_handle": "bob", "amount": "50"}, 422, "validation_failed", "amount string"),
        ({"to_handle": "bob", "amount": True}, 422, "validation_failed", "amount boolean"),
        ({"to_handle": "ada", "amount": 5}, 422, "self_payment", "self payment"),
        ({"to_handle": "bob", "amount": 5, "note": "x" * 201}, 422, "validation_failed", "note 201 chars"),
        ({"to_handle": "bob", "amount": 5, "visibility": "friends"}, 422, "validation_failed", "bad visibility"),
        ({"to_handle": "nosuch", "amount": 5}, 404, "not_found", "unknown handle"),
    ]
    for body, status, code, what in cases:
        r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()}, body=body)
        ck.is_err(r, status, code, what)
    ok200 = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"to_handle": "bob", "amount": 5, "note": "x" * 200})
    ck.eq(ok200.status, 201, "note exactly 200 chars accepted")


@test("r67_payment_atomic_no_trace_on_failure", "R67", "R1", "R2", needs=('payments', 'me', 'activity'))
def r67_payment_atomic(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    bal_ada = (h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance")
    bal_bob = (h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance")
    feed_before = feed_ids(h, u.t("cy"))
    fail = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "bob", "amount": 999999})
    ck.eq(fail.status, 409, "failing payment")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), bal_ada,
          "sender unchanged after failed payment")
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), bal_bob,
          "receiver unchanged after failed payment")
    ck.eq(feed_ids(h, u.t("cy")), feed_before, "no feed trace after failed payment")
    ok = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": 500})
    ck.eq(ok.status, 201, "successful payment")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), bal_ada - 500, "debit exact")
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), bal_bob + 500, "credit exact")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r68_note_verbatim_unicode", "R68", needs=('payments', 'activity'))
def r68_note_verbatim(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    note = "café ☕ 🍕  double space — ünïcödé ✨ \t tab kept"
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 15, "note": note, "visibility": "public"})
    ck.eq(p.status, 201, "payment with unicode note")
    ck.eq((p.json or {}).get("note"), note, "note verbatim in response")
    pid = (p.json or {}).get("payment_id")
    for hd in ("bob", "cy"):
        items = {q.get("payment_id") or q.get("id"): q for q in feed(h, u.t(hd)).get("payments", [])}
        ck.eq((items.get(pid) or {}).get("note"), note, "note verbatim in %s's feed" % hd)


@test("r27_activity_feed_contract", "R27", "R30", "R26", needs=('payments', 'requests', 'activity'))
def r27_feed_contract(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    pub = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                    body={"to_handle": "bob", "amount": 10, "visibility": "public"})
    priv = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "bob", "amount": 11, "visibility": "private"})
    pub2 = h.request("POST", "/payments", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "cy", "amount": 12, "visibility": "public"})
    ck.eq(pub.status, 201, "public payment")
    ck.eq(priv.status, 201, "private payment")
    ck.eq(pub2.status, 201, "second public payment")
    pub_id = (pub.json or {}).get("payment_id")
    priv_id = (priv.json or {}).get("payment_id")
    pub2_id = (pub2.json or {}).get("payment_id")
    ada_ids = set(feed_ids(h, u.t("ada")))
    ck.true({pub_id, priv_id, pub2_id} <= ada_ids,
            "sender sees own public+private, and third-party public payments")
    cy_ids = set(feed_ids(h, u.t("cy")))
    ck.eq(cy_ids, {pub_id, pub2_id}, "third party sees only public payments")
    dave_ids = set(feed_ids(h, u.t("dave")))
    ck.eq(dave_ids, {pub_id, pub2_id}, "unrelated user sees only public payments")
    bob_view = {q.get("payment_id") or q.get("id"): q for q in feed(h, u.t("bob")).get("payments", [])}
    ada_view = {q.get("payment_id") or q.get("id"): q for q in feed(h, u.t("ada")).get("payments", [])}
    ck.eq((bob_view.get(priv_id) or {}).get("visibility"), "private", "receiver sees private payment")
    ck.eq((ada_view.get(priv_id) or {}).get("visibility"), "private", "sender sees same visibility value")
    # requests never appear in the feed
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 20})
    ck.eq(rq.status, 201, "request created")
    ck.eq(set(feed_ids(h, u.t("ada"))), ada_ids, "creating a request adds nothing to any feed")


@test("r26_payer_chooses_visibility_on_pay", "R26", "R72", "R30", needs=('requests', 'pay', 'activity'))
def r26_payer_visibility(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("cy"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 30})
    rid = (rq.json or {}).get("request_id")
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={"visibility": "private"})
    ck.eq(pay.status, 201, "payer pays with private visibility")
    j = pay.json or {}
    ck.eq(j.get("visibility"), "private", "payment visibility private (payer's choice)")
    ck.eq(j.get("request_id"), rid, "payment carries request_id")
    cy_ids = set(feed_ids(h, u.t("cy")))
    ck.true(j.get("payment_id") in cy_ids, "requester (receiver) sees the payment")
    ck.true(j.get("payment_id") not in set(feed_ids(h, u.t("dave"))), "third party does not")
    r2 = h.request("GET", "/requests?status=paid", token=u.t("cy")).json or {}
    mine = [q for q in r2.get("requests", []) if (q.get("request_id") or q.get("id")) == rid]
    ck.true(mine and mine[0].get("payment_id") == j.get("payment_id"), "request carries payment_id")


@test("r81_activity_pagination", "R81", "R44", "R77", needs=('payments', 'activity'))
def r81_activity_pagination(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    ids = []
    for i in range(5):
        p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"to_handle": "cy", "amount": 10 + i})
        ids.append((p.json or {}).get("payment_id"))
    r = h.request("GET", "/activity?limit=2", token=u.t("ada"))
    j = r.json or {}
    ck.eq(len(j.get("payments", [])), 2, "limit respected")
    ck.eq(j.get("has_more"), True, "has_more true")
    r2 = h.request("GET", "/activity?limit=2&offset=2", token=u.t("ada"))
    j2 = r2.json or {}
    ck.eq(j2.get("has_more"), True, "has_more still true at offset 2")
    r3 = h.request("GET", "/activity?limit=2&offset=4", token=u.t("ada"))
    j3 = r3.json or {}
    ck.eq(j3.get("has_more"), False, "has_more false at last page")
    all_ids = sorted([q.get("payment_id") for q in j["payments"] + j2["payments"] + j3["payments"]])
    ck.eq(all_ids, sorted(ids), "pages cover every payment exactly once")
    times = [q.get("created_at") for q in j["payments"] + j2["payments"] + j3["payments"]]
    ck.eq(times, sorted(times, reverse=True), "newest first by created_at (ties in any order)")
    ck.eq(h.request("GET", "/activity?limit=2&offset=5", token=u.t("ada")).json,
          {"payments": [], "has_more": False}, "offset beyond end -> empty page")


@test("r31_amount_bounds", "R31", "R66", needs=('payments', 'me'))
def r31_amount_bounds(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(balances={"ada": 1000000000, "bob": 2500, "cy": 0, "dave": 0})).status,
          204, "reset: ada holds exactly 1e9")
    u = Users(h, ck)
    big = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                    body={"to_handle": "bob", "amount": 1000000000})
    ck.eq(big.status, 201, "amount exactly 1e9 accepted")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 0, "exact integer debit")
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), 1000000000 + 2500,
          "exact integer credit")
    zero = h.request("POST", "/payments", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "cy", "amount": 0})
    ck.is_err(zero, 422, "validation_failed", "amount 0 rejected")
    over = h.request("POST", "/payments", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "cy", "amount": 1000000001})
    ck.is_err(over, 422, "validation_failed", "amount 1e9+1 rejected")
    assert_conservation(h, ck, 1000000000 + 2500, u.tokens)


@test("r31_exact_big_int_arithmetic", "R31", "R1", needs=('payments',))
def r31_big_int(h, ck):
    big = 9007199254740991  # 2^53 - 1: beyond float64 integer precision, odd
    f = std_fixture(balances={"ada": big, "bob": 1, "cy": 0, "dave": 0})
    ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset with 2^53-1 balance")
    u = Users(h, ck)
    m = h.request("GET", "/me", token=u.t("ada")).json or {}
    ck.eq(m.get("balance"), big, "odd 2^53-1 balance exact (no float rounding)")
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 1000000000})
    ck.eq(p.status, 201, "large-balance payment ok")
    m2 = h.request("GET", "/me", token=u.t("ada")).json or {}
    ck.eq(m2.get("balance"), big - 1000000000, "balance stays exact odd integer")
    assert_conservation(h, ck, big + 1, u.tokens)


@test("r35_negative_fixture_balance_422", "R35", "R11", needs=('me',))
def r35_negative_fixture(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset good fixture")
    bad = {"currency": "EUR", "minor_units": 2, "payments": [], "requests": [],
           "users": [fu("u_neg", "neg@example.com", "neg", -5)]}
    r = h.request("POST", "/_test/reset", body=bad)
    ck.is_err(r, 422, "validation_failed", "negative fixture balance rejected")
    u = Users(h, ck)
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 10000,
          "destination unchanged after failed reset")


@test("r37_no_admin_balance_endpoint", "R37", needs=('me',))
def r37_no_admin_endpoint(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck, handles=("ada",))
    for path in ["/balance", "/balances", "/admin/balance", "/wallets"]:
        r = h.request("GET", path, token=u.t("ada"))
        ck.true(r.status in (404, 405), "no admin balance endpoint at %s" % path,
                "got %s %.120s" % (r.status, r.text))
        ra = h.request("GET", path)
        ck.true(ra.status in (401, 404, 405), "unauth hit on %s reveals nothing" % path,
                str(ra.status))


# ------------------------------------------------------------------ requests

@test("r69_request_create_schema", "R69", "R24", needs=('requests',))
def r69_request_schema(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 1200, "note": "taxi"})
    ck.eq(rq.status, 201, "request created")
    j = rq.json or {}
    ck.eq(j.get("requester_id"), "u_bob", "requester_id (caller)")
    ck.eq(j.get("requester_handle"), "bob", "requester_handle")
    ck.eq(j.get("payer_id"), "u_ada", "payer_id")
    ck.eq(j.get("payer_handle"), "ada", "payer_handle")
    ck.eq(j.get("amount"), 1200, "amount")
    ck.eq(j.get("note"), "taxi", "note")
    ck.eq(j.get("status"), "pending", "status pending")
    ck.true("payment_id" in j and j.get("payment_id") is None, "payment_id null")
    ck.true(RFC3339.match(j.get("created_at") or ""), "created_at RFC3339")
    d = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                  body={"payer_handle": "ada", "amount": 5})
    ck.eq((d.json or {}).get("note"), "", "note default empty")


@test("r70_request_errors", "R70", needs=('requests',))
def r70_request_errors(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    cases = [
        ({"payer_handle": "ada", "amount": "5"}, 422, "validation_failed", "amount string"),
        ({"payer_handle": "ada", "amount": 0}, 422, "validation_failed", "amount 0"),
        ({"payer_handle": "ada", "amount": 1000000001}, 422, "validation_failed", "amount > 1e9"),
        ({"payer_handle": "bob", "amount": 5}, 422, "self_request", "payer_handle is caller"),
        ({"payer_handle": "ada", "amount": 5, "note": "x" * 201}, 422, "validation_failed", "note 201"),
        ({"payer_handle": "nosuch", "amount": 5}, 404, "not_found", "unknown payer handle"),
    ]
    for body, status, code, what in cases:
        r = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()}, body=body)
        ck.is_err(r, status, code, what)


@test("r25_request_over_balance_pending_then_payable", "R25", "R71", needs=('requests', 'pay', 'payments'))
def r25_over_balance(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(balances={"ada": 100, "bob": 2500, "cy": 0, "dave": 50000})).status,
          204, "reset: ada holds only 100")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 30000})
    ck.eq(rq.status, 201, "request far above payer balance created normally")
    ck.eq((rq.json or {}).get("status"), "pending", "stays pending")
    rid = (rq.json or {}).get("request_id")
    short = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                      headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(short, 409, "insufficient_funds", "paying while short is 409")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 100,
          "nothing changed by failed pay")
    still = h.request("GET", "/requests?status=pending", token=u.t("ada")).json or {}
    ck.true(any((q.get("request_id") or q.get("id")) == rid for q in still.get("requests", [])),
            "request still pending")
    fund = h.request("POST", "/payments", token=u.t("dave"), headers={"Idempotency-Key": unique_key()},
                     body={"to_handle": "ada", "amount": 50000})
    ck.eq(fund.status, 201, "money arrives later")
    now = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={})
    ck.eq(now.status, 201, "same request now payable")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 20100,
          "exact debit after fund")
    assert_conservation(h, ck, 52600, u.tokens)


@test("r72_pay_body_and_replay_sensitivity", "R72", "R59", needs=('requests', 'pay'))
def r72_pay_body_sensitivity(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    r1 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 10})
    p1 = h.request("POST", "/requests/%s/pay" % (r1.json or {}).get("request_id"), token=u.t("ada"),
                   headers={"Idempotency-Key": unique_key()}, body={})
    ck.eq(p1.status, 201, "pay with {} accepted, visibility default public")
    ck.eq((p1.json or {}).get("visibility"), "public", "default visibility public")
    r2 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 11})
    rid2 = (r2.json or {}).get("request_id")
    k = unique_key()
    first = h.request("POST", "/requests/%s/pay" % rid2, token=u.t("ada"),
                      headers={"Idempotency-Key": k}, body={"visibility": "public"})
    ck.eq(first.status, 201, "pay with explicit public")
    same = h.request("POST", "/requests/%s/pay" % rid2, token=u.t("ada"),
                     headers={"Idempotency-Key": k}, body={"visibility": "public"})
    ck.eq(same.status, 200, "identical body replay -> 200")
    diff = h.request("POST", "/requests/%s/pay" % rid2, token=u.t("ada"),
                     headers={"Idempotency-Key": k}, body={})
    ck.is_err(diff, 409, "idempotency_key_reuse", "{} vs {visibility:public} are different JSON values")
    r3 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 12})
    k3 = unique_key()
    p3 = h.request("POST", "/requests/%s/pay" % (r3.json or {}).get("request_id"), token=u.t("ada"),
                   headers={"Idempotency-Key": k3}, body={})
    diff3 = h.request("POST", "/requests/%s/pay" % (r3.json or {}).get("request_id"), token=u.t("ada"),
                      headers={"Idempotency-Key": k3}, body={"visibility": "public"})
    ck.is_err(diff3, 409, "idempotency_key_reuse", "replay with other body on first-pay path too")


@test("r73_pay_error_table", "R73", needs=('requests', 'pay', 'decline'))
def r73_pay_errors(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 12000})
    rid = (rq.json or {}).get("request_id")
    short = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                      headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(short, 409, "insufficient_funds", "insufficient funds on pay")
    dec = h.request("POST", "/requests/%s/decline" % rid, token=u.t("ada"))
    ck.eq(dec.status, 200, "declined")
    notp = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                     headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(notp, 409, "request_not_pending", "pay declined request")
    rq2 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "ada", "amount": 20})
    rid2 = (rq2.json or {}).get("request_id")
    other = h.request("POST", "/requests/%s/pay" % rid2, token=u.t("cy"),
                      headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(other, 403, "forbidden", "non-payer cannot pay")
    missing = h.request("POST", "/requests/rq_missing/pay", token=u.t("ada"),
                        headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(missing, 404, "not_found", "unknown request")


@test("r74_pay_replay_after_paid", "R74", "R62", "R3", needs=('requests', 'pay'))
def r74_pay_replay(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 250})
    rid = (rq.json or {}).get("request_id")
    k = unique_key()
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": k}, body={})
    ck.eq(pay.status, 201, "paid")
    bal = (h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance")
    replay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                       headers={"Idempotency-Key": k}, body={})
    ck.eq(replay.status, 200, "replay after paid -> 200, not 409 request_not_pending")
    ck.eq(replay.json, pay.json, "original payment body returned")
    ck.eq((h.request("GET", "/me", token=u.t("bob")).json or {}).get("balance"), bal, "no extra money")
    rnow = h.request("GET", "/requests", token=u.t("bob")).json or {}
    mine = [q for q in rnow.get("requests", []) if (q.get("request_id") or q.get("id")) == rid]
    ck.true(mine and mine[0].get("payment_id") == (pay.json or {}).get("payment_id"),
            "request payment_id unchanged")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r75_decline_lifecycle", "R75", "R24", needs=('requests', 'decline', 'pay'))
def r75_decline(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 20})
    rid = (rq.json or {}).get("request_id")
    wrong = h.request("POST", "/requests/%s/decline" % rid, token=u.t("bob"))
    ck.is_err(wrong, 403, "forbidden", "requester cannot decline (payer only)")
    d1 = h.request("POST", "/requests/%s/decline" % rid, token=u.t("ada"))
    ck.eq(d1.status, 200, "payer declines")
    ck.eq((d1.json or {}).get("status"), "declined", "status declined")
    d2 = h.request("POST", "/requests/%s/decline" % rid, token=u.t("ada"))
    ck.eq(d2.status, 200, "double decline is 200, not an error")
    ck.eq((d2.json or {}).get("status"), "declined", "still declined")
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(pay, 409, "request_not_pending", "cannot pay declined request")
    cancel = h.request("POST", "/requests/%s/cancel" % rid, token=u.t("bob"))
    ck.is_err(cancel, 409, "request_not_pending", "cannot cancel declined request")


@test("r76_cancel_lifecycle", "R76", "R24", needs=('requests', 'cancel', 'pay'))
def r76_cancel(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 20})
    rid = (rq.json or {}).get("request_id")
    wrong = h.request("POST", "/requests/%s/cancel" % rid, token=u.t("ada"))
    ck.is_err(wrong, 403, "forbidden", "payer cannot cancel (requester only)")
    c1 = h.request("POST", "/requests/%s/cancel" % rid, token=u.t("bob"))
    ck.eq(c1.status, 200, "requester cancels")
    ck.eq((c1.json or {}).get("status"), "cancelled", "status cancelled")
    c2 = h.request("POST", "/requests/%s/cancel" % rid, token=u.t("bob"))
    ck.eq(c2.status, 200, "double cancel is 200")
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={})
    ck.is_err(pay, 409, "request_not_pending", "cannot pay cancelled request")
    dec = h.request("POST", "/requests/%s/decline" % rid, token=u.t("ada"))
    ck.is_err(dec, 409, "request_not_pending", "cannot decline cancelled request")


@test("r24_request_lifecycle_states", "R24", "R75", "R76", needs=('requests', 'pay', 'decline', 'cancel'))
def r24_lifecycle(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 40})
    rid = (rq.json or {}).get("request_id")
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={})
    ck.eq(pay.status, 201, "paid")
    dec = h.request("POST", "/requests/%s/decline" % rid, token=u.t("ada"))
    ck.is_err(dec, 409, "request_not_pending", "decline after paid")
    can = h.request("POST", "/requests/%s/cancel" % rid, token=u.t("bob"))
    ck.is_err(can, 409, "request_not_pending", "cancel after paid")


@test("r77_requests_listing_filters", "R77", "R28", needs=('requests', 'pay', 'list_requests'))
def r77_requests_filters(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq1 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "ada", "amount": 100})
    id1 = (rq1.json or {}).get("request_id")
    rq2 = h.request("POST", "/requests", token=u.t("cy"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "bob", "amount": 200})
    id2 = (rq2.json or {}).get("request_id")
    rq3 = h.request("POST", "/requests", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "cy", "amount": 300})
    id3 = (rq3.json or {}).get("request_id")
    rq4 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "ada", "amount": 400})
    id4 = (rq4.json or {}).get("request_id")
    ck.eq(h.request("POST", "/requests/%s/pay" % id4, token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()}, body={}).status, 201, "pay rq4")

    def ids_for(resp):
        return [q.get("request_id") or q.get("id") for q in (resp.json or {}).get("requests", [])]

    both = ids_for(h.request("GET", "/requests", token=u.t("bob")))
    ck.eq(set(both), {id1, id2, id4}, "bob sees requests where he is requester or payer, no others")
    incoming = ids_for(h.request("GET", "/requests?direction=incoming", token=u.t("bob")))
    ck.eq(set(incoming), {id2}, "incoming = caller is payer")
    outgoing = ids_for(h.request("GET", "/requests?direction=outgoing", token=u.t("bob")))
    ck.eq(set(outgoing), {id1, id4}, "outgoing = caller is requester")
    paid = ids_for(h.request("GET", "/requests?status=paid", token=u.t("bob")))
    ck.eq(set(paid), {id4}, "status filter")
    pending = ids_for(h.request("GET", "/requests?status=pending", token=u.t("bob")))
    ck.eq(set(pending), {id1, id2}, "pending filter")
    ck.is_err(h.request("GET", "/requests?status=nope", token=u.t("bob")), 422, "validation_failed",
              "unknown status -> 422")
    ck.is_err(h.request("GET", "/requests?direction=sideways", token=u.t("bob")), 422, "validation_failed",
              "unknown direction -> 422")
    dave = ids_for(h.request("GET", "/requests", token=u.t("dave")))
    ck.eq(dave, [], "uninvolved user sees no requests")
    page = h.request("GET", "/requests?limit=2", token=u.t("bob")).json or {}
    ck.eq(len(page.get("requests", [])), 2, "limit 2")
    ck.eq(page.get("has_more"), True, "has_more true")
    ck.is_err(h.request("GET", "/requests?limit=0", token=u.t("bob")), 422, "validation_failed", "limit 0")


# ------------------------------------------------------------------ splits

@test("r82_rounding_table_exact", "R82", "R78", "R83", needs=('splits',))
def r82_rounding_table(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(balances={"ada": 100, "bob": 100, "cy": 100, "dave": 100})).status,
          204, "reset")
    u = Users(h, ck, handles=("ada", "bob", "cy", "dave"))
    rows = [(1000, ["ada", "bob", "cy"], [334, 333, 333]),
            (1, ["ada", "bob", "cy"], [1, 0, 0]),
            (10, ["ada", "bob", "cy"], [4, 3, 3]),
            (999, ["ada", "bob", "cy"], [333, 333, 333]),
            (5, ["ada", "bob", "cy", "dave", "frank"], [1, 1, 1, 1, 1])]
    for amount, participants, want in rows:
        r = h.request("POST", "/splits", token=u.t(participants[0]), headers={"Idempotency-Key": unique_key()},
                      body={"amount": amount, "participant_handles": participants})
        ck.eq(r.status, 201, "split %s/%d" % (amount, len(participants)))
        j = r.json or {}
        got = [(s.get("handle"), s.get("amount")) for s in j.get("shares", [])]
        ck.eq(got, list(zip(participants, want)), "shares for %s among %d" % (amount, len(participants)))
        ck.eq(sum(s.get("amount", 0) for s in j.get("shares", [])), amount, "shares sum to amount")
        expect_requests = [(hd, w) for hd, w in zip(participants, want) if hd != participants[0]]
        got_requests = [(q.get("payer_handle"), q.get("amount")) for q in j.get("requests", [])]
        ck.eq(got_requests, expect_requests, "requests: every participant except caller, same order")


@test("r83_reorder_moves_extra_unit", "R83", needs=('splits',))
def r83_reorder(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    a = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"amount": 10, "participant_handles": ["ada", "bob", "cy"]})
    ck.eq([(s["handle"], s["amount"]) for s in (a.json or {}).get("shares", [])],
          [("ada", 4), ("bob", 3), ("cy", 3)], "first participant gets extra unit")
    b = h.request("POST", "/splits", token=u.t("cy"), headers={"Idempotency-Key": unique_key()},
                  body={"amount": 10, "participant_handles": ["cy", "bob", "ada"]})
    ck.eq([(s["handle"], s["amount"]) for s in (b.json or {}).get("shares", [])],
          [("cy", 4), ("bob", 3), ("ada", 3)], "reordered: extra unit moved")


@test("r83_zero_share_still_requests", "R83", needs=('splits',))
def r83_zero_share(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    r = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"amount": 1, "participant_handles": ["ada", "bob", "cy"]})
    ck.eq(r.status, 201, "split 1 among 3 accepted")
    reqs = (r.json or {}).get("requests", [])
    ck.eq([(q.get("payer_handle"), q.get("amount")) for q in reqs], [("bob", 0), ("cy", 0)],
          "zero shares still produce requests")
    ck.eq(len(reqs), 2, "two requests created")


@test("r79_split_errors", "R79", needs=('splits',))
def r79_split_errors(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    cases = [
        ({"amount": 30, "participant_handles": []}, 422, "validation_failed", "empty participants"),
        ({"amount": 30, "participant_handles": ["bob", "bob"]}, 422, "validation_failed", "duplicate handle"),
        ({"amount": 30, "participant_handles": ["zzz"]}, 404, "not_found", "unknown handle"),
        ({"amount": 0, "participant_handles": ["bob"]}, 422, "validation_failed", "amount 0"),
        ({"amount": "30", "participant_handles": ["bob"]}, 422, "validation_failed", "amount string"),
        ({"amount": 1000000001, "participant_handles": ["bob"]}, 422, "validation_failed", "amount > 1e9"),
        ({"amount": 30, "participant_handles": ["bob"], "note": "x" * 201}, 422, "validation_failed", "note 201"),
        ({"amount": 30, "participant_handles": "bob"}, 422, "validation_failed", "participants not a list"),
    ]
    for body, status, code, what in cases:
        r = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()}, body=body)
        ck.is_err(r, status, code, what)


@test("r80_split_caller_only_valid", "R80", "R78", needs=('splits',))
def r80_caller_only(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    r = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"amount": 500, "participant_handles": ["ada"]})
    ck.eq(r.status, 201, "caller-only split valid")
    j = r.json or {}
    ck.eq(j.get("shares"), [{"handle": "ada", "amount": 500}], "one share")
    ck.eq(j.get("requests"), [], "zero requests")
    # no balance checks anywhere in splits
    f = std_fixture(balances={"ada": 0, "bob": 0, "cy": 0, "dave": 0})
    ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset everyone to 0")
    u2 = Users(h, ck)
    big = h.request("POST", "/splits", token=u2.t("cy"), headers={"Idempotency-Key": unique_key()},
                    body={"amount": 1000000000, "participant_handles": ["cy", "bob"]})
    ck.eq(big.status, 201, "split never checks balances")


@test("r78_split_forms_and_order", "R78", needs=('splits',))
def r78_forms(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    with_caller = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                            body={"amount": 1000, "participant_handles": ["ada", "bob", "cy"], "note": "n"})
    j = with_caller.json or {}
    ck.eq([(s["handle"], s["amount"]) for s in j.get("shares", [])],
          [("ada", 334), ("bob", 333), ("cy", 333)], "shares include caller in given order")
    ck.eq([q.get("payer_handle") for q in j.get("requests", [])], ["bob", "cy"], "requests exclude caller, same order")
    ck.eq([q.get("requester_handle") for q in j.get("requests", [])], ["ada", "ada"], "caller is requester")
    ck.eq(j.get("note"), "n", "note echoed")
    ck.true(RFC3339.match(j.get("created_at") or ""), "created_at RFC3339")
    without = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                        body={"amount": 1000, "participant_handles": ["bob", "cy"]})
    j2 = without.json or {}
    ck.eq([(s["handle"], s["amount"]) for s in j2.get("shares", [])],
          [("bob", 500), ("cy", 500)], "caller omitted: shares over listed participants")
    ck.eq([q.get("payer_handle") for q in j2.get("requests", [])], ["bob", "cy"], "requests in input order")


@test("r84_splits_paid_conserve", "R84", "R1", needs=('splits', 'requests', 'pay'))
def r84_conserve(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(balances={"ada": 10000, "bob": 2500, "cy": 2000, "dave": 2000})).status,
          204, "reset: all participants funded")
    u = Users(h, ck)
    s1 = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"amount": 1000, "participant_handles": ["ada", "bob", "cy"]})
    for q in (s1.json or {}).get("requests", []):
        pr = h.request("POST", "/requests/%s/pay" % q.get("request_id"), token=u.t(q.get("payer_handle")),
                       headers={"Idempotency-Key": unique_key()}, body={})
        ck.eq(pr.status, 201, "pay share %s" % q.get("payer_handle"))
    assert_conservation(h, ck, 16500, u.tokens)
    s2 = h.request("POST", "/splits", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"amount": 999, "participant_handles": ["bob", "cy", "dave"]})
    for q in (s2.json or {}).get("requests", []):
        pr = h.request("POST", "/requests/%s/pay" % q.get("request_id"), token=u.t(q.get("payer_handle")),
                       headers={"Idempotency-Key": unique_key()}, body={})
        ck.eq(pr.status, 201, "pay share %s" % q.get("payer_handle"))
    assert_conservation(h, ck, 16500, u.tokens)


@test("r29_split_not_a_feed_item", "R29", "R26", needs=('splits', 'requests', 'activity'))
def r29_split_not_feed(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    before_ada = feed_ids(h, u.t("ada"))
    before_bob = feed_ids(h, u.t("bob"))
    before_dave = feed_ids(h, u.t("dave"))
    sp = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"amount": 300, "participant_handles": ["ada", "bob", "cy"]})
    ck.eq(sp.status, 201, "split created")
    ck.eq(feed_ids(h, u.t("ada")), before_ada, "split adds nothing to splitter's feed")
    ck.eq(feed_ids(h, u.t("bob")), before_bob, "nothing to participant feed")
    ck.eq(feed_ids(h, u.t("dave")), before_dave, "nothing to bystander feed")
    inc = h.request("GET", "/requests?direction=incoming", token=u.t("bob")).json or {}
    ck.eq(len(inc.get("requests", [])), 1, "split request visible to payer via /requests")
    out = h.request("GET", "/requests?direction=outgoing", token=u.t("ada")).json or {}
    ck.eq(len(out.get("requests", [])), 2, "both split requests visible to requester via /requests")
    dave = h.request("GET", "/requests", token=u.t("dave")).json or {}
    ck.eq(dave.get("requests", []), [], "split requests invisible to third parties")


# ------------------------------------------------------------------ settlements

def _settle_fixture():
    return std_fixture(operators=["u_ada"], balances={"ada": 1000, "bob": 800, "cy": 0, "dave": 0})


@test("r94_operator_permissions", "R94", needs=('settlements', 'activity', 'list_requests'))
def r94_operator(h, ck):
    seeded_priv = {"id": "p_priv", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 50,
                   "note": "hush", "visibility": "private"}
    seeded_rq_other = {"id": "rq_other", "requester_id": "u_cy", "payer_id": "u_dave", "amount": 10,
                       "note": "x", "status": "pending"}
    f = _settle_fixture()
    f["payments"] = [seeded_priv]
    f["requests"] = [seeded_rq_other]
    ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset with operator u_ada")
    u = Users(h, ck)
    ok = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 10}]})
    ck.eq(ok.status, 201, "operator can settle")
    denied = h.request("POST", "/settlements", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                       body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 10}]})
    ck.is_err(denied, 403, "forbidden", "non-operator cannot settle")
    act = feed_ids(h, u.t("ada"))
    ck.true("p_priv" not in act, "operator permission does not expose others' private payments")
    rqs = h.request("GET", "/requests", token=u.t("ada")).json or {}
    rids = {q.get("request_id") or q.get("id") for q in rqs.get("requests", [])}
    ck.true("rq_other" not in rids, "operator permission does not expose others' requests")


@test("r95_settlement_auth_key_shape", "R95", "R56", needs=('settlements',))
def r95_shape(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5}]}
    noauth = h.request("POST", "/settlements", body=body, headers={"Idempotency-Key": unique_key()})
    ck.is_err(noauth, 401, "unauthenticated", "no token")
    nokey = h.request("POST", "/settlements", token=u.t("ada"), body=body)
    ck.is_err(nokey, 400, "missing_idempotency_key", "no key")
    ck.is_err(h.request("POST", "/settlements", token=u.t("bob"), body=body,
                        headers={"Idempotency-Key": unique_key()}), 403, "forbidden", "non-operator")
    cases = [(None, 422, "transfers missing"),
             ({"transfers": "x"}, 422, "transfers not array"),
             ({"transfers": []}, 422, "empty transfers"),
             ({"transfers": [{"from_handle": "ada"}]}, 422, "entry missing fields"),
             ({"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": "5"}]}, 422, "amount string"),
             ({"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 0}]}, 422, "amount 0"),
             ({"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5, "visibility": "x"}]},
              422, "bad visibility"),
             ({"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5, "note": None}]},
              422, "note null"),
             ({"transfers": ["nope"]}, 422, "entry not object"),
             ({"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}] * 33}, 422, "33 entries"),
             ({}, 422, "empty body object")]
    for b, status, what in cases:
        r = h.request("POST", "/settlements", token=u.t("ada"), body=b, headers={"Idempotency-Key": unique_key()})
        ck.eq(r.status, status, what)
    ok = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}] * 32})
    ck.eq(ok.status, 201, "32 entries accepted (boundary)")


@test("r96_entry_error_precedence", "R96", "A2", needs=('settlements',))
def r96_precedence(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    cases = [
        ([{"from_handle": "ada", "to_handle": "ada", "amount": 1},
          {"from_handle": "zzz", "to_handle": "bob", "amount": 1}], 422, "self_payment",
         "entry 1 self-transfer wins over entry 2 unknown handle"),
        ([{"from_handle": "zzz", "to_handle": "bob", "amount": 1},
          {"from_handle": "ada", "to_handle": "ada", "amount": 1}], 404, "not_found",
         "entry 1 unknown handle wins in input order"),
        ([{"from_handle": "ada", "to_handle": "bob", "amount": 1200},
          {"from_handle": "ada", "to_handle": "ada", "amount": 1}], 422, "self_payment",
         "entry errors precede affordability check (entry 1 would be insufficient)"),
        ([{"from_handle": "ada", "to_handle": "bob", "amount": 1200},
          {"from_handle": "zzz", "to_handle": "bob", "amount": 1}], 404, "not_found",
         "unknown handle precedes affordability"),
        ([{"from_handle": "ada", "to_handle": "bob", "amount": 1200},
          {"from_handle": "bob", "to_handle": "cy", "amount": 50}], 409, "insufficient_funds",
         "all entries valid + collective shortfall -> insufficient_funds"),
        ([{"from_handle": "ada", "to_handle": "bob", "amount": 5, "zzz": 1}], 201, None,
         "unknown fields in entry ignored"),
    ]
    for transfers, status, code, what in cases:
        r = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"transfers": transfers})
        ck.eq(r.status, status, what)
        if code:
            ck.eq(r.code, code, what + " (code)")
    shape = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      raw=b'{"transfers": "not-a-list", "zzz": 2}')
    ck.is_err(shape, 422, "validation_failed", "malformed batch shape -> 422 (endpoint rule)")
    unk = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                    body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5}], "zzz": 1})
    ck.eq(unk.status, 201, "unknown body fields ignored")


@test("r97_collective_not_sequential", "R97", "R98", needs=('settlements',))
def r97_collective(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(operators=["u_ada"],
                                     balances={"ada": 500, "bob": 0, "cy": 0, "dave": 0})).status,
          204, "reset: ada 500, others 0")
    u = Users(h, ck)
    net_ok = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                       body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 800},
                                           {"from_handle": "bob", "to_handle": "ada", "amount": 800}]})
    ck.eq(net_ok.status, 201, "net-zero batch accepted even though sequential execution would overdraw ada")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 500, "ada net unchanged")
    shortfall = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                          body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 600},
                                              {"from_handle": "bob", "to_handle": "cy", "amount": 600}]})
    ck.is_err(shortfall, 409, "insufficient_funds", "net-negative batch rejected")
    assert_conservation(h, ck, 500, u.tokens)


@test("r98_settlement_all_or_nothing", "R98", "R59", needs=('settlements',))
def r98_all_or_nothing(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    before = {hd: (h.request("GET", "/me", token=u.t(hd)).json or {}).get("balance")
              for hd in ("ada", "bob", "cy", "dave")}
    feed_before = feed_ids(h, u.t("ada"))
    k = unique_key()
    fail = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                     body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1500}]})
    ck.is_err(fail, 409, "insufficient_funds", "unaffordable settlement fails")
    after = {hd: (h.request("GET", "/me", token=u.t(hd)).json or {}).get("balance")
             for hd in ("ada", "bob", "cy", "dave")}
    ck.eq(after, before, "no wallet changed after failed settlement")
    ck.eq(feed_ids(h, u.t("ada")), feed_before, "no payment created after failed settlement")
    retry = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                      body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 10}]})
    ck.eq(retry.status, 201, "failed settlement claimed no idempotency key (same key is first use)")
    k2 = unique_key()
    bad = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k2},
                    body={"transfers": [{"from_handle": "zzz", "to_handle": "bob", "amount": 1}]})
    ck.is_err(bad, 404, "not_found", "validation failure")
    retry2 = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k2},
                       body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 3}]})
    ck.eq(retry2.status, 201, "validation-failed key reusable as first use")
    assert_conservation(h, ck, 1800, u.tokens)


@test("r99_settlement_response_shape", "R99", "R95", needs=('settlements', 'payments', 'activity', 'me'))
def r99_response(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    r = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100,
                                       "note": "net", "visibility": "private"},
                                      {"from_handle": "bob", "to_handle": "cy", "amount": 50}]})
    ck.eq(r.status, 201, "settlement created")
    j = r.json or {}
    ck.true(isinstance(j.get("settlement_id"), str) and j.get("settlement_id"), "settlement_id present")
    ck.true(RFC3339.match(j.get("committed_at") or ""), "committed_at RFC3339")
    pays = j.get("payments", [])
    ck.eq(len(pays), 2, "one receipt per transfer")
    ck.eq([(p.get("from_handle"), p.get("to_handle")) for p in pays],
          [("ada", "bob"), ("bob", "cy")], "payments in input order")
    for p in pays:
        ck.eq(p.get("settlement_id"), j.get("settlement_id"), "member carries settlement_id")
        ck.true(p.get("request_id") is None, "member request_id null")
        ck.eq(p.get("created_at"), j.get("committed_at"), "member created_at == committed_at")
    ck.eq(pays[0].get("visibility"), "private", "per-transfer visibility honoured")
    ck.eq(pays[1].get("note"), "", "per-transfer note defaults empty")
    ck.eq(pays[1].get("visibility"), "public", "per-transfer visibility defaults public")
    plain = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"to_handle": "cy", "amount": 5})
    ck.true((plain.json or {}).get("payment_id") in feed_ids(h, u.t("ada")), "plain payment appears in feed")
    plain_feed = {q.get("payment_id"): q for q in feed(h, u.t("ada")).get("payments", [])}.get(
        (plain.json or {}).get("payment_id"), {})
    ck.true("settlement_id" in plain_feed and plain_feed.get("settlement_id") is None,
            "nonmember payment exposes settlement_id null in feed")


@test("r100_settlement_replay_and_visibility", "R100", "R62", needs=('settlements', 'activity'))
def r100_replay_visibility(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    r = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                  body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100,
                                       "visibility": "private"},
                                      {"from_handle": "bob", "to_handle": "cy", "amount": 50}]})
    ck.eq(r.status, 201, "settlement created")
    balances_now = {hd: (h.request("GET", "/me", token=u.t(hd)).json or {}).get("balance")
                    for hd in ("ada", "bob", "cy", "dave")}
    replay = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                       body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100,
                                            "visibility": "private"},
                                           {"from_handle": "bob", "to_handle": "cy", "amount": 50}]})
    ck.eq(replay.status, 200, "replay -> 200")
    ck.eq(replay.json, r.json, "original complete response returned")
    ck.eq({hd: (h.request("GET", "/me", token=u.t(hd)).json or {}).get("balance")
           for hd in ("ada", "bob", "cy", "dave")}, balances_now, "replay moves no money")
    dave_ids = set(feed_ids(h, u.t("dave")))
    ck.eq(len(dave_ids), 1, "dave sees only the public leg of the settlement")
    ck.eq([(p.get("visibility"), p.get("settlement_id") is not None)
           for p in feed(h, u.t("dave")).get("payments", [])], [("public", True)],
          "constituent follows ordinary feed visibility, carries settlement_id")
    ck.eq(len(feed_ids(h, u.t("bob"))), 2, "bob (party) sees both legs")


@test("r61_concurrent_settlement_same_key", "R61", "R98", needs=('settlements',))
def r61_settlement_concurrent(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100},
                          {"from_handle": "bob", "to_handle": "cy", "amount": 100}]}
    fns = [lambda: h.request("POST", "/settlements", token=u.t("ada"),
                             headers={"Idempotency-Key": k}, body=body) for _ in range(12)]
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    statuses = [r.status for r in results if isinstance(r, Resp)]
    ck.eq(statuses.count(201), 1, "exactly one 201")
    ck.eq(statuses.count(200), 11, "rest 200")
    ck.eq((h.request("GET", "/me", token=u.t("ada")).json or {}).get("balance"), 900, "ada moved once")
    ck.eq((h.request("GET", "/me", token=u.t("cy")).json or {}).get("balance"), 100, "cy credited once")
    assert_conservation(h, ck, 1800, u.tokens)


@test("r101_settlement_preserved_by_import", "R101", "R91", needs=('settlements', 'export', 'import'))
def r101_preserved(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=_settle_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    r = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                  body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100}]})
    ck.eq(r.status, 201, "settlement created")
    e = h.request("GET", "/_test/export")
    ck.eq(e.status, 200, "export")
    ck.eq(h.request("POST", "/_test/import", body=e.json).status, 204, "import")
    replay = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": k},
                       body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100}]})
    ck.eq(replay.status, 200, "settlement replay still 200 after import")
    ck.eq(replay.json, r.json, "original settlement response preserved")
    again = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 7}]})
    ck.eq(again.status, 201, "operator permission preserved by import")
    act = feed(h, u.t("ada")).get("payments", [])
    ck.true(any(p.get("settlement_id") == (r.json or {}).get("settlement_id") for p in act),
            "settlement membership preserved in feed")


# ------------------------------------------------------------------ export / import

@test("r85_export_shape", "R85", needs=('export',))
def r85_export(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    r = h.request("GET", "/_test/export")  # unauthenticated
    ck.eq(r.status, 200, "export 200 unauthenticated")
    j = r.json or {}
    ck.eq(j.get("track"), "pocketful", "track pocketful")
    ck.eq(j.get("format_version"), 1, "format_version 1")
    ck.true(isinstance(j.get("state"), dict), "state is an object")


def _rich_state(h, ck):
    """Build a state exercising users, payments, requests, splits, settlements, idempotency."""
    f = std_fixture(operators=["u_ada"], balances={"ada": 10000, "bob": 2500, "cy": 500, "dave": 100})
    ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset")
    u = Users(h, ck)
    kp = unique_key()
    pay = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": kp},
                    body={"to_handle": "bob", "amount": 500, "note": "☕ private", "visibility": "private"})
    ck.eq(pay.status, 201, "payment")
    rq = h.request("POST", "/requests", token=u.t("cy"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 700})
    ck.eq(rq.status, 201, "request pending")
    rq2 = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                    body={"payer_handle": "dave", "amount": 60})
    ck.eq(h.request("POST", "/requests/%s/pay" % (rq2.json or {}).get("request_id"), token=u.t("dave"),
                    headers={"Idempotency-Key": unique_key()}, body={"visibility": "public"}).status, 201,
          "paid request")
    ck.eq(h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"amount": 300, "participant_handles": ["ada", "bob", "cy"], "note": "split"}).status,
          201, "split")
    ks = unique_key()
    st = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": ks},
                   body={"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 100},
                                        {"from_handle": "cy", "to_handle": "bob", "amount": 50}]})
    ck.eq(st.status, 201, "settlement")
    kf = unique_key()
    h.request("POST", "/payments", token=u.t("cy"), headers={"Idempotency-Key": kf},
              body={"to_handle": "ada", "amount": 100000})  # fails 409; key stays free
    tokens = dict(u.tokens)
    signup = h.request("POST", "/auth/signup", body={"email": "zed@example.com", "password": "correct horse",
                                                     "display_name": "Zed"})
    tokens["zed"] = (signup.json or {}).get("token")
    return tokens, {"kp": kp, "kp_body": {"to_handle": "bob", "amount": 500, "note": "☕ private",
                                          "visibility": "private"},
                    "kp_resp": pay.json, "ks": ks, "ks_resp": st.json,
                    "st_body": {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 100},
                                               {"from_handle": "cy", "to_handle": "bob", "amount": 50}]},
                    "kf": kf, "kf_body": {"to_handle": "ada", "amount": 100000}}


@test("r86_import_roundtrip_preserves_all", "R86", "R91", "R33", "R62", needs=('export', 'import', 'settlements', 'splits', 'payments', 'requests', 'pay'))
def r86_roundtrip(h, ck):
    tokens, keys = _rich_state(h, ck)
    balances = {hd: (h.request("GET", "/me", token=tk).json or {}).get("balance")
                for hd, tk in tokens.items()}
    feeds = {hd: feed(h, tk) for hd, tk in tokens.items()}
    reqs = {hd: h.request("GET", "/requests?limit=200", token=tk).json
            for hd, tk in tokens.items()}
    export = h.request("GET", "/_test/export")
    ck.eq(export.status, 200, "export taken")
    e_body = export.json
    # diverge heavily
    diverge = h.request("POST", "/payments", token=tokens["ada"], headers={"Idempotency-Key": unique_key()},
                        body={"to_handle": "cy", "amount": 999})
    ck.eq(diverge.status, 201, "divergent payment")
    sign = h.request("POST", "/auth/signup", body={"email": "diverge@example.com", "password": "correct horse",
                                                   "display_name": "D"})
    ck.eq(sign.status, 201, "divergent signup")
    imp = h.request("POST", "/_test/import", body=e_body)
    ck.eq(imp.status, 204, "import restores")
    # balances + tokens preserved (R91)
    balances_after = {hd: (h.request("GET", "/me", token=tk).json or {}).get("balance")
                      for hd, tk in tokens.items()}
    ck.eq(balances_after, balances, "balances and tokens valid after import")
    login = h.request("POST", "/auth/login", body={"email": "ada@example.com", "password": "correct horse"})
    ck.eq(login.status, 200, "hashed-password login works after import")
    # feeds identical (monetary records not regenerated)
    for hd in feeds:
        ck.eq(feed(h, tokens[hd]), feeds[hd], "%s activity identical after import" % hd)
    for hd in reqs:
        ck.eq(h.request("GET", "/requests?limit=200", token=tokens[hd]).json, reqs[hd],
              "%s requests identical after import" % hd)
    # idempotent replays still return original responses (R91)
    rp = h.request("POST", "/payments", token=tokens["ada"], headers={"Idempotency-Key": keys["kp"]},
                   body=keys["kp_body"])
    ck.eq(rp.status, 200, "payment replay after import -> 200")
    ck.eq(rp.json, keys["kp_resp"], "original payment response preserved")
    rs = h.request("POST", "/settlements", token=tokens["ada"], headers={"Idempotency-Key": keys["ks"]},
                   body=keys["st_body"])
    ck.eq(rs.status, 200, "settlement replay after import -> 200")
    ck.eq(rs.json, keys["ks_resp"], "original settlement response preserved")
    rf = h.request("POST", "/payments", token=tokens["cy"], headers={"Idempotency-Key": keys["kf"]},
                   body=keys["kf_body"])
    ck.is_err(rf, 409, "insufficient_funds", "failed key still reusable (re-executed, not idempotency_key_reuse)")
    assert_conservation(h, ck, sum(balances.values()), tokens)


@test("r87_import_replacement_repeats", "R87", "R92", needs=('export', 'import', 'payments', 'settlements'))
def r87_repeats(h, ck):
    tokens, _ = _rich_state(h, ck)
    export = h.request("GET", "/_test/export")
    e_body = export.json
    ck.eq(h.request("POST", "/_test/import", body=e_body).status, 204, "import 1")
    snap1 = h.request("GET", "/_test/export").text
    h.request("POST", "/payments", token=tokens["ada"], headers={"Idempotency-Key": unique_key()},
              body={"to_handle": "cy", "amount": 123})
    ck.eq(h.request("POST", "/_test/import", body=e_body).status, 204, "import 2 replaces, not merges")
    snap2 = h.request("GET", "/_test/export").text
    ck.eq(snap2, snap1, "repeated import restores identical state, no duplication")
    h.request("POST", "/_test/import", body=e_body)
    ck.eq(h.request("GET", "/_test/export").text, snap1, "third import still identical")


@test("r88_import_invalid_no_change", "R88", needs=('import',))
def r88_invalid(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset canary")
    u = Users(h, ck)
    bad_cases = [("raw notjson", None, b"notjson", 400),
                 ("empty object", {}, None, 422),
                 ("missing version", {"track": "pocketful"}, None, 422),
                 ("missing state", {"track": "pocketful", "format_version": 1}, None, 422),
                 ("state null", {"track": "pocketful", "format_version": 1, "state": None}, None, 422),
                 ("state string", {"track": "pocketful", "format_version": 1, "state": "junk"}, None, 422),
                 ("wrong track", {"track": "nope", "format_version": 1, "state": {}}, None, 422),
                 ("wrong version", {"track": "pocketful", "format_version": 2, "state": {}}, None, 422),
                 ("version string", {"track": "pocketful", "format_version": "1", "state": {}}, None, 422),
                 ("garbage state", {"track": "pocketful", "format_version": 1, "state": {"zz": True}}, None, 422)]
    for what, body, raw, status in bad_cases:
        r = h.request("POST", "/_test/import", body=body, raw=raw)
        ck.eq(r.status, status, "import %s -> %d" % (what, status))
        if status >= 400:
            ck.envelope(r, "import %s" % what)
        m = h.request("GET", "/me", token=u.t("ada"))
        ck.eq((m.json or {}).get("balance"), 10000, "destination unchanged after %s" % what)


@test("r90_export_snapshot_immutable", "R90", needs=('export', 'import', 'payments'))
def r90_snapshot(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    e1 = h.request("GET", "/_test/export").text
    h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
              body={"to_handle": "cy", "amount": 77})
    e2 = h.request("GET", "/_test/export").text
    ck.noteq(e2, e1, "later writes change the current export (snapshot was point-in-time)")
    ck.eq(h.request("POST", "/_test/import", body=json.loads(e1)).status, 204, "import old snapshot")
    m = h.request("GET", "/me", token=u.t("cy")).json or {}
    ck.eq(m.get("balance"), 0, "old snapshot restored (77-payment absent)")


@test("r92_import_removes_destination_credentials", "R92", needs=('export', 'import'))
def r92_replaces(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    zed = h.request("POST", "/auth/signup", body={"email": "zed@example.com", "password": "correct horse",
                                                  "display_name": "Zed"})
    zed_token = (zed.json or {}).get("token")
    e_zed = h.request("GET", "/_test/export").json
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset back to no-zed")
    u = Users(h, ck)
    ck.eq(h.request("GET", "/me", token=zed_token).status, 401, "after reset zed token invalid")
    ck.eq(h.request("POST", "/_test/import", body=e_zed).status, 204, "import zed state")
    ck.eq(h.request("GET", "/me", token=zed_token).status, 200, "zed token valid again after import")
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset destination")
    ck.eq(h.request("GET", "/me", token=zed_token).status, 401, "reset clears imported credentials")
    ada_login = h.request("POST", "/auth/login", body={"email": "ada@example.com", "password": "correct horse"})
    ck.eq(ada_login.status, 200, "destination user intact after import+reset")
    ck.eq((h.request("GET", "/me", token=(ada_login.json or {}).get("token")).json or {}).get("balance"),
          10000, "destination user data intact")


@test("r93_reset_clears_imported_state", "R93", "R11", needs=('export', 'import'))
def r93_reset_clears(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    e = h.request("GET", "/_test/export").json
    ck.eq(h.request("POST", "/_test/import", body=e).status, 204, "import")
    f2 = {"currency": "EUR", "minor_units": 2, "payments": [], "requests": [],
          "users": [fu("u_zoe", "zoe@example.com", "zoe", 77)]}
    ck.eq(h.request("POST", "/_test/reset", body=f2).status, 204, "reset over imported state")
    zoe = h.request("POST", "/auth/login", body={"email": "zoe@example.com", "password": "correct horse"})
    ck.eq(zoe.status, 200, "new fixture only")
    ck.is_err(h.request("POST", "/auth/login", body={"email": "ada@example.com", "password": "correct horse"}),
              401, "unauthenticated", "imported user cleared by reset")
    ck.eq(h.request("GET", "/me", token=u.t("ada")).status, 401, "imported token cleared by reset")