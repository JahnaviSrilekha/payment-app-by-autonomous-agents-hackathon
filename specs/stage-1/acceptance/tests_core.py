"""Stage-1 acceptance tests: infra, errors, auth, idempotency, concurrency invariants."""
import json
import random

from harness import (Check, RFC3339, Resp, assert_conservation, check_all_responses,
                     fu, login, run_concurrently, std_fixture, test, unique_key, Users)


def amt_ok(r):
    return r.status == 201


# ------------------------------------------------------------------ infra

@test("r10_health_ok", "R10", "R12")
def r10_health_ok(h, ck):
    r = h.request("GET", "/health")
    ck.eq(r.status, 200, "/health status")
    ck.eq(r.json, {"status": "ok"}, "/health body")
    ct = r.lower_headers().get("content-type", "")
    ck.true(ct.startswith("application/json"), "/health content-type", ct)
    ck.true("charset=utf-8" in ct, "/health charset=utf-8", ct)


@test("r11_reset_seeds_state", "R11", "R32", "R33", "R34", "R64", "R20", needs=('activity', 'list_requests'))
def r11_reset_seeds_state(h, ck):
    seeded_pay = {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                  "amount": 500, "note": "coffee", "visibility": "public"}
    seeded_rq = {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
                 "amount": 1200, "note": "taxi", "status": "pending"}
    r = h.request("POST", "/_test/reset", body=std_fixture(payments=[seeded_pay], requests=[seeded_rq]))
    ck.eq(r.status, 204, "reset status")
    u = Users(h, ck)
    m = h.request("GET", "/me", token=u.t("ada"))
    ck.eq(m.status, 200, "/me ada status")
    j = m.json or {}
    ck.eq(j.get("user_id"), "u_ada", "/me user_id")
    ck.eq(j.get("handle"), "ada", "/me handle (R20)")
    ck.eq(j.get("balance"), 10000, "/me ada balance is post-seed value, not replayed (R34)")
    ck.eq(j.get("currency"), "EUR", "/me currency")
    ck.eq(j.get("minor_units"), 2, "/me minor_units")
    for f in ("display_name", "user_id", "handle", "balance", "currency", "minor_units"):
        ck.true(f in j, "/me has field %s" % f)
    mb = h.request("GET", "/me", token=u.t("bob"))
    ck.eq((mb.json or {}).get("balance"), 2500, "/me bob balance (R34)")
    act = h.request("GET", "/activity", token=u.t("ada"))
    ck.eq(act.status, 200, "activity status")
    ids = [p.get("payment_id") or p.get("id") for p in (act.json or {}).get("payments", [])]
    ck.true("p_1" in ids, "seeded payment p_1 visible in feed", act.text[:300])
    rqs = h.request("GET", "/requests", token=u.t("ada"))
    rids = [q.get("request_id") or q.get("id") for q in (rqs.json or {}).get("requests", [])]
    ck.true("rq_1" in rids, "seeded request rq_1 visible to payer", rqs.text[:300])


@test("r11_reset_replaces_and_repeats", "R11", "R93", "R51")
def r11_reset_replaces_and_repeats(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset F1")
    f2 = {"currency": "EUR", "minor_units": 2, "payments": [], "requests": [],
          "users": [fu("u_zoe", "zoe@example.com", "zoe", 77)]}
    ck.eq(h.request("POST", "/_test/reset", body=f2).status, 204, "reset F2")
    r_ada = h.request("POST", "/auth/login", body={"email": "ada@example.com", "password": "correct horse"})
    ck.is_err(r_ada, 401, "unauthenticated", "old fixture user cannot login after reset")
    rz = h.request("POST", "/auth/login", body={"email": "zoe@example.com", "password": "correct horse"})
    ck.eq(rz.status, 200, "new fixture user logs in")
    ck.eq((h.request("GET", "/me", token=rz.json["token"]).json or {}).get("balance"), 77,
          "zoe balance seeded")
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset F1 again")
    ck.eq(h.request("POST", "/auth/login", body={"email": "ada@example.com",
                                                 "password": "correct horse"}).status, 200, "ada back")
    ck.is_err(h.request("POST", "/auth/login", body={"email": "zoe@example.com", "password": "correct horse"}),
              401, "unauthenticated", "F2 user gone after re-reset")


@test("r12_json_charset_content_type", "R12", needs=('me',))
def r12_json_charset_content_type(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    for r, what in [(h.request("GET", "/health"), "health"),
                    (h.request("GET", "/me", token=u.t("ada")), "me"),
                    (h.request("GET", "/me", token="bogus"), "error 401")]:
        ct = r.lower_headers().get("content-type", "")
        ck.true(ct.startswith("application/json"), what + " content-type json", ct)
        ck.true("charset=utf-8" in ct, what + " content-type charset", ct)


@test("r13_timestamps_rfc3339_offset", "R13", needs=('payments', 'requests'))
def r13_timestamps_rfc3339_offset(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 10})
    ck.eq(p.status, 201, "payment created")
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 10})
    ck.eq(rq.status, 201, "request created")
    for what, ts in [("payment created_at", (p.json or {}).get("created_at")),
                     ("request created_at", (rq.json or {}).get("created_at"))]:
        ck.true(isinstance(ts, str) and RFC3339.match(ts), what + " RFC3339 with explicit offset", repr(ts))


@test("r14_unknown_body_fields_ignored", "R14", needs=('payments', 'requests', 'pay'))
def r14_unknown_body_fields_ignored(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "cy", "amount": 5, "zzz": {"deep": [1, 2]}, "extra": "x",
                        "note": "kept", "visibility": "private"})
    ck.eq(p.status, 201, "unknown fields ignored on payments")
    ck.eq((p.json or {}).get("note"), "kept", "known field still honoured")
    ck.eq((p.json or {}).get("visibility"), "private", "known field still honoured")
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 5, "visibility": "private", "payment_id": "x"})
    ck.eq(rq.status, 201, "unknown fields ignored on requests")
    ck.eq((rq.json or {}).get("note"), "", "request note default")
    pay = h.request("POST", "/requests/%s/pay" % (rq.json or {}).get("request_id"), token=u.t("ada"),
                    headers={"Idempotency-Key": unique_key()},
                    body={"visibility": "public", "amount": 999, "to_handle": "zzz"})
    ck.eq(pay.status, 201, "unknown fields ignored on pay (body carries visibility only)")


@test("r15_unknown_query_params_ignored", "R15", needs=('activity', 'list_requests'))
def r15_unknown_query_params_ignored(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    ck.eq(h.request("GET", "/activity?zzz=1&foo=bar&limit=10", token=u.t("ada")).status, 200,
          "unknown query params ignored on /activity")
    ck.eq(h.request("GET", "/requests?zzz=1&direction=incoming", token=u.t("ada")).status, 200,
          "unknown query params ignored on /requests")


@test("r16_ids_opaque_max64", "R16", needs=('payments', 'requests', 'splits'))
def r16_ids_opaque_max64(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture(operators=["u_ada"])).status, 204, "reset")
    s = h.request("POST", "/auth/signup", body={"email": "idcheck@example.com",
                                                "password": "correct horse", "display_name": "I"})
    ck.eq(s.status, 201, "signup")
    u = Users(h, ck)
    ids = [("user_id", (s.json or {}).get("user_id"))]
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "bob", "amount": 3})
    ids.append(("payment_id", (p.json or {}).get("payment_id")))
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 3})
    ids.append(("request_id", (rq.json or {}).get("request_id")))
    sp = h.request("POST", "/splits", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"amount": 9, "participant_handles": ["ada", "bob", "cy"]})
    ids.append(("split_id", (sp.json or {}).get("split_id")))
    st = h.request("POST", "/settlements", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 1}]})
    if st.status == 201:
        ids.append(("settlement_id", (st.json or {}).get("settlement_id")))
    for what, v in ids:
        ck.true(isinstance(v, str) and 1 <= len(v) <= 64, "%s opaque string 1..64 chars" % what, repr(v))


@test("r17_one_currency_everywhere", "R17", "R36", "R64", needs=('payments', 'requests'))
def r17_one_currency_everywhere(h, ck):
    for cur, mu, bal in [("EUR", 2, {"ada": 10000, "bob": 2500, "cy": 0, "dave": 0}),
                         ("JPY", 0, {"ada": 100000, "bob": 25000, "cy": 0, "dave": 0}),
                         ("BHD", 3, {"ada": 10000, "bob": 2500, "cy": 0, "dave": 0})]:
        f = std_fixture(currency=cur, minor_units=mu, balances=bal)
        ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset %s" % cur)
        u = Users(h, ck)
        m = h.request("GET", "/me", token=u.t("ada"))
        j = m.json or {}
        ck.eq(j.get("currency"), cur, "/me currency %s" % cur)
        ck.eq(j.get("minor_units"), mu, "/me minor_units %s" % cur)
        p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                      body={"to_handle": "bob", "amount": 1000})
        ck.eq(p.status, 201, "payment ok in %s" % cur)
        ck.eq((p.json or {}).get("currency"), cur, "payment currency %s" % cur)
        rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                       body={"payer_handle": "ada", "amount": 500})
        ck.eq((rq.json or {}).get("currency"), cur, "request currency %s" % cur)


# ------------------------------------------------------------------ errors

@test("r38_error_envelope_shape", "R38")
def r38_error_envelope_shape(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    cases = [(h.request("GET", "/me"), "401 /me"),
             (h.request("POST", "/payments", raw=b"not json at all"), "400 unparseable"),
             (h.request("GET", "/activity", token="wrong-token"), "401 bad token")]
    for r, what in cases:
        ck.envelope(r, what)


@test("r39_status_code_matrix", "R39", "R43", needs=('payments', 'requests', 'pay'))
def r39_status_code_matrix(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 10})
    rid = (rq.json or {}).get("request_id")
    k1, k2 = unique_key(), unique_key()
    cases = [
        (h.request("POST", "/payments", token=u.t("ada"), raw=b"{ nope"), 400, "malformed_request", "unparseable body"),
        (h.request("POST", "/payments", token=u.t("ada"), body={"to_handle": "bob", "amount": 5}), 400,
         "missing_idempotency_key", "absent Idempotency-Key"),
        (h.request("POST", "/payments", body={"to_handle": "bob", "amount": 5},
                   headers={"Idempotency-Key": k2}), 401, "unauthenticated", "no token"),
        (h.request("POST", "/requests/%s/pay" % rid, token=u.t("cy"),
                   headers={"Idempotency-Key": unique_key()}, body={}), 403, "forbidden", "not the payer"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k1},
                   body={"to_handle": "nosuch", "amount": 5}), 404, "not_found", "unknown handle"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k2},
                   body={"to_handle": "bob", "amount": 5}), 201, None, "first use"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k2},
                   body={"to_handle": "bob", "amount": 6}), 409, "idempotency_key_reuse", "same key other body"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": "5"}), 422, "validation_failed", "amount as string"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k1},
                   body={"to_handle": "bob", "amount": 0}), 422, "validation_failed", "amount 0"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k1},
                   body={"to_handle": "ada", "amount": 5}), 422, "self_payment", "self payment"),
    ]
    for r, status, code, what in cases:
        ck.is_err(r, status, code, what)


@test("r40_wrong_type_400_vs_422", "R40", "R41", "R43", needs=('payments',))
def r40_wrong_type_400_vs_422(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    cases = [
        (h.request("POST", "/auth/signup", body={"email": 5, "password": "correct horse",
                                                 "display_name": "X"}), 400, "malformed_request", "email wrong JSON type"),
        (h.request("POST", "/auth/signup", body={"email": "wt@example.com", "password": "correct horse",
                                                 "display_name": {"a": 1}}), 400, "malformed_request",
         "display_name wrong JSON type"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": 5, "note": None}), 422, "validation_failed",
         "note null -> endpoint-specific 422 (R41)"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": 5, "note": 7}), 422, "validation_failed", "note number -> 422"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": 5, "visibility": 1}), 422, "validation_failed",
         "visibility number -> 422"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": "50"}), 422, "validation_failed", "amount string -> 422"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": "bob", "amount": True}), 422, "validation_failed", "amount boolean -> 422"),
        (h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"to_handle": 5, "amount": 5}), 400, "malformed_request", "to_handle wrong type -> 400"),
        (h.request("POST", "/auth/signup", raw=b"[1,2,3]"), 400, "malformed_request", "body not an object"),
    ]
    for r, status, code, what in cases:
        ck.is_err(r, status, code, what)


@test("r42_query_integer_format", "R42", "R44", needs=('list_requests', 'activity'))
def r42_query_integer_format(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    cases = [
        ("GET", "/requests?limit=1e9", 422), ("GET", "/requests?limit=4.0", 422),
        ("GET", "/requests?offset=%2B4", 422), ("GET", "/requests?limit=abc", 422),
        ("GET", "/requests?limit=201", 422), ("GET", "/requests?limit=0", 422),
        ("GET", "/requests?offset=-1", 422), ("GET", "/requests?limit=200", 200),
        ("GET", "/requests?limit=1&offset=0", 200), ("GET", "/requests", 200),
        ("GET", "/activity?limit=1e9", 422), ("GET", "/activity?limit=201", 422),
        ("GET", "/activity?offset=-2", 422), ("GET", "/activity?limit=200&offset=3", 200),
    ]
    for method, path, want in cases:
        r = h.request(method, path, token=u.t("ada"))
        ck.eq(r.status, want, "%s %s" % (method, path))
        if want >= 400:
            ck.envelope(r, path)


@test("r44_key_length_range", "R44", needs=('payments',))
def r44_key_length_range(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    r255 = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": "a" * 255},
                     body={"to_handle": "bob", "amount": 5})
    ck.eq(r255.status, 201, "255-char key accepted")
    r256 = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": "a" * 256},
                     body={"to_handle": "bob", "amount": 5})
    ck.is_err(r256, 422, "validation_failed", "256-char key rejected")
    r_empty = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": ""},
                        body={"to_handle": "bob", "amount": 5})
    ck.is_err(r_empty, 400, "missing_idempotency_key", "empty key")
    r256b = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": "b" * 256},
                      body={"to_handle": "bob", "amount": 5})
    ck.is_err(r256b, 422, "validation_failed", "oversized key never claims (retryable)")


@test("r45_no_5xx_under_50_concurrent", "R45", "R7", "R1", "R2", needs=('payments', 'activity'))
def r45_no_5xx_under_50_concurrent(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    bad_bodies = [b"{{{", b"", b"[]", b'{"amount": "x"}']
    fns = []
    for i in range(50):
        if i % 5 == 0:
            fns.append(lambda i=i: h.request("POST", "/payments", token=u.t("ada"),
                                             headers={"Idempotency-Key": unique_key("nz")},
                                             body={"to_handle": "cy", "amount": 1 + i % 3}))
        elif i % 5 == 1:
            fns.append(lambda i=i: h.request("POST", "/payments", token=u.t("bob"), raw=bad_bodies[i % 4]))
        elif i % 5 == 2:
            fns.append(lambda: h.request("POST", "/payments", body={"to_handle": "cy", "amount": 1}))
        elif i % 5 == 3:
            fns.append(lambda: h.request("GET", "/activity", token=u.t("cy")))
        else:
            fns.append(lambda: h.request("POST", "/payments", token=u.t("ada"),
                                         headers={"Idempotency-Key": unique_key("nz")},
                                         body={"to_handle": "nosuchhandle", "amount": 2}))
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    check_all_responses(h, ck, results, "50-concurrent", seeded_total=12500, tokens=u.tokens)


@test("r7_r8_300_concurrent_requests_all_served", "R8", "R7", needs=('activity',))
def r7_r8_300_concurrent(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    fns = []
    for i in range(300):
        if i % 2:
            fns.append(lambda: h.request("GET", "/health"))
        else:
            fns.append(lambda: h.request("GET", "/activity", token=u.t("ada")))
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    served = [r for r in results if isinstance(r, Resp) and r.status]
    ck.eq(len(served), 300, "all 300 concurrent requests served (listen backlog >= queue)")
    ck.true(all(r.status < 500 for r in served), "none 5xx")


# ------------------------------------------------------------------ auth

@test("r46_signup_schema_new_user_zero_balance", "R46", "R22", needs=('payments', 'requests'))
def r46_signup_schema(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    s = h.request("POST", "/auth/signup", body={"email": "new.user1@example.com",
                                                "password": "correct horse", "display_name": "New User"})
    ck.eq(s.status, 201, "signup status")
    j = s.json or {}
    ck.eq(j.get("display_name"), "New User", "display_name echoed")
    ck.true(isinstance(j.get("user_id"), str) and j.get("user_id"), "user_id present")
    ck.true(isinstance(j.get("token"), str) and j.get("token"), "token present")
    m = h.request("GET", "/me", token=j.get("token"))
    ck.eq((m.json or {}).get("balance"), 0, "new user balance 0 (R22)")
    # can receive money and be asked for money immediately
    u = Users(h, ck, handles=("ada",))
    p = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                  body={"to_handle": "new_user1", "amount": 5})
    ck.eq(p.status, 201, "new user can receive immediately")
    rq = h.request("POST", "/requests", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "new_user1", "amount": 2})
    ck.eq(rq.status, 201, "new user can be asked immediately")


@test("r47_login_and_multiple_tokens", "R47", "R54", needs=('me',))
def r47_login_and_multiple_tokens(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    t1 = login(h, "ada@example.com", ck=ck, what="login 1")
    t2 = login(h, "ada@example.com", ck=ck, what="login 2")
    ck.true(t1 and t2, "two tokens minted")
    ck.true(t1 != t2, "distinct tokens per session")
    ck.eq(h.request("GET", "/me", token=t1).status, 200, "token 1 valid")
    ck.eq(h.request("GET", "/me", token=t2).status, 200, "token 2 valid concurrently")
    j = h.request("GET", "/me", token=t1).json or {}
    ck.eq(j.get("handle"), "ada", "/me via login token")


@test("r48_email_taken_409", "R48")
def r48_email_taken(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    r = h.request("POST", "/auth/signup", body={"email": "ada@example.com", "password": "correct horse",
                                                "display_name": "Fake Ada"})
    ck.is_err(r, 409, "email_taken", "duplicate email signup")


@test("r49_password_min_length", "R49")
def r49_password_min_length(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    short = h.request("POST", "/auth/signup", body={"email": "pw@example.com", "password": "1234567",
                                                    "display_name": "P"})
    ck.is_err(short, 422, "validation_failed", "7-char password")
    ok = h.request("POST", "/auth/signup", body={"email": "pw@example.com", "password": "12345678",
                                                 "display_name": "P"})
    ck.eq(ok.status, 201, "8-char password accepted")


@test("r50_email_shape", "R50")
def r50_email_shape(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    for email in ["nodomain", "a@b@c", "@b.com", "a@"]:
        r = h.request("POST", "/auth/signup", body={"email": email, "password": "correct horse",
                                                    "display_name": "E"})
        ck.is_err(r, 422, "validation_failed", "bad email %r" % email)
    # note: the spec defines no charset for local/domain; a space-containing local part
    # ("a b@example.com") is not asserted (recorded in coverage.md as ambiguous).
    ok = h.request("POST", "/auth/signup", body={"email": "a@b", "password": "correct horse",
                                                 "display_name": "E"})
    ck.eq(ok.status, 201, "minimal valid email local@domain")


@test("r51_login_bad_credentials", "R51")
def r51_login_bad_credentials(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    wrong = h.request("POST", "/auth/login", body={"email": "ada@example.com", "password": "wrong wrong"})
    ck.is_err(wrong, 401, "unauthenticated", "wrong password")
    unknown = h.request("POST", "/auth/login", body={"email": "nobody@example.com", "password": "correct horse"})
    ck.is_err(unknown, 401, "unauthenticated", "unknown email")


@test("r52_derived_handle_collides", "R21", "R52", needs=('me',))
def r52_derived_handle_collides(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    first = h.request("POST", "/auth/signup", body={"email": "ada_2@example.com", "password": "correct horse",
                                                    "display_name": "A2"})
    ck.eq(first.status, 201, "first ada_2 signup")
    clash = h.request("POST", "/auth/signup", body={"email": "Ada.2@example.com", "password": "correct horse",
                                                    "display_name": "Clash"})
    ck.is_err(clash, 409, "handle_taken", "derived handle collision")
    no_acct = h.request("POST", "/auth/login", body={"email": "Ada.2@example.com", "password": "correct horse"})
    ck.is_err(no_acct, 401, "unauthenticated", "no account created on handle_taken")


@test("r21_handle_derivation_rules", "R21", needs=('me',))
def r21_handle_derivation(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    s = h.request("POST", "/auth/signup", body={"email": "A.B+c-d@e.com", "password": "correct horse",
                                                "display_name": "D"})
    ck.eq(s.status, 201, "signup with mixed-case local part")
    m = h.request("GET", "/me", token=(s.json or {}).get("token"))
    ck.eq((m.json or {}).get("handle"), "a_b_c_d", "local part lowercased + outside chars -> _")
    long_local = "abcdefghijklmnopqrstuvwxyzabcd"
    s2 = h.request("POST", "/auth/signup", body={"email": long_local + "@e.com", "password": "correct horse",
                                                 "display_name": "T"})
    ck.eq(s2.status, 201, "signup with 30-char local part")
    m2 = h.request("GET", "/me", token=(s2.json or {}).get("token"))
    ck.eq((m2.json or {}).get("handle"), long_local[:20], "handle truncated to 20 chars")


@test("r53_endpoints_require_auth", "R53", needs=('requests',))
def r53_endpoints_require_auth(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck, handles=("ada",))
    r2 = h.request("POST", "/requests", token=u.t("ada"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "bob", "amount": 1})
    rid = (r2.json or {}).get("request_id") or "rq_x"
    cases = [("GET", "/me", None, None), ("GET", "/activity", None, None), ("GET", "/requests", None, None),
             ("POST", "/payments", {"to_handle": "bob", "amount": 1}, None),
             ("POST", "/requests", {"payer_handle": "bob", "amount": 1}, None),
             ("POST", "/splits", {"amount": 2, "participant_handles": ["bob"]}, None),
             ("POST", "/requests/%s/pay" % rid, {}, None),
             ("POST", "/requests/%s/decline" % rid, None, None),
             ("POST", "/requests/%s/cancel" % rid, None, None)]
    for method, path, body, _ in cases:
        r = h.request(method, path, body=body)
        ck.is_err(r, 401, "unauthenticated", "%s %s without token" % (method, path))


@test("r102_r103_no_out_of_scope_endpoints", "R102", "R103")
def r102_r103_absent(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck, handles=("ada",))
    for path in ["/users/search", "/directory", "/users", "/search",
                 "/auth/verify", "/auth/reset-password", "/auth/refresh", "/auth/roles"]:
        r = h.request("GET", path, token=u.t("ada"))
        ck.true(r.status in (404, 405), "no directory/search or auth-management endpoint at %s" % path,
                "got %s %.120s" % (r.status, r.text))
        p = h.request("POST", path, token=u.t("ada"), body={})
        ck.true(p.status in (404, 405), "no POST %s either" % path,
                "got %s %.120s" % (p.status, p.text))


# ------------------------------------------------------------------ idempotency

@test("r56_missing_key_on_five_paths", "R56", needs=('payments', 'requests', 'pay', 'splits', 'settlements'))
def r56_missing_key(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture(operators=["u_ada"])).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 2})
    rid = (rq.json or {}).get("request_id")
    cases = [("POST", "/payments", {"to_handle": "bob", "amount": 2}, u.t("ada")),
             ("POST", "/requests", {"payer_handle": "ada", "amount": 2}, u.t("bob")),
             ("POST", "/requests/%s/pay" % rid, {}, u.t("ada")),
             ("POST", "/splits", {"amount": 2, "participant_handles": ["bob"]}, u.t("ada")),
             ("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]},
              u.t("ada"))]
    for method, path, body, tok in cases:
        r = h.request(method, path, body=body, token=tok)
        ck.is_err(r, 400, "missing_idempotency_key", "%s %s without key" % (method, path))


@test("r59_idempotency_lifecycle", "R59", "R60", needs=('payments',))
def r59_idempotency_lifecycle(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    body = {"to_handle": "cy", "amount": 7, "note": "first"}
    first = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k}, body=body)
    ck.eq(first.status, 201, "first use -> 201")
    raw_replay = json.dumps({"note": "first", "amount": 7, "to_handle": "cy"},
                            ensure_ascii=False, indent=2)  # different key order + whitespace
    replay = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k}, raw=raw_replay)
    ck.eq(replay.status, 200, "replay same JSON value -> 200 (key order/whitespace irrelevant)")
    ck.eq(replay.json, first.json, "replay body identical as JSON value")
    other = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k},
                      body={"to_handle": "cy", "amount": 9})
    ck.is_err(other, 409, "idempotency_key_reuse", "same key different body")
    # key reused after a 4xx failure is treated as first use
    k2 = unique_key()
    fail = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k2},
                     body={"to_handle": "cy", "amount": 12000})
    ck.is_err(fail, 409, "insufficient_funds", "first attempt fails 4xx")
    bob_pay = h.request("POST", "/payments", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                        body={"to_handle": "ada", "amount": 2500})
    ck.eq(bob_pay.status, 201, "fund ada for retry")
    retry = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k2},
                      body={"to_handle": "cy", "amount": 12000})
    ck.eq(retry.status, 201, "same key after 4xx failure treated as first use")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r57_key_scoped_per_user", "R57", needs=('payments',))
def r57_key_scoped_per_user(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    shared = "shared-key-xyz"
    a = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": shared},
                  body={"to_handle": "cy", "amount": 5})
    b = h.request("POST", "/payments", token=u.t("bob"), headers={"Idempotency-Key": shared},
                  body={"to_handle": "cy", "amount": 7})
    ck.eq(a.status, 201, "ada first use")
    ck.eq(b.status, 201, "bob same key string, independent scope -> 201")
    a2 = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": shared},
                   body={"to_handle": "cy", "amount": 5})
    ck.eq(a2.status, 200, "ada replay -> 200")
    ck.eq(a2.json, a.json, "replay returns ada's original")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r58_same_key_different_path_ok", "R58", needs=('requests', 'pay'))
def r58_same_key_different_path(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    body = {"payer_handle": "ada", "amount": 100, "note": "path-test"}
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": k}, body=body)
    ck.eq(rq.status, 201, "requests first use")
    rid = (rq.json or {}).get("request_id")
    pay = h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"), headers={"Idempotency-Key": k},
                    body=body)  # extra fields ignored; same key, different path -> NOT a replay
    ck.eq(pay.status, 201, "same key same body different path -> treated as first use (201)")
    again = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": k}, body=body)
    ck.eq(again.status, 200, "original path replay -> 200")
    ck.eq(again.json, rq.json, "original body returned")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r61_concurrent_same_key_exactly_one_201", "R61", "R3", "R1", needs=('payments',))
def r61_concurrent_same_key(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    body = {"to_handle": "bob", "amount": 100, "note": "once"}
    fns = [lambda: h.request("POST", "/payments", token=u.t("ada"),
                             headers={"Idempotency-Key": k}, body=body) for _ in range(24)]
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    statuses = sorted(r.status for r in results if isinstance(r, Resp))
    ck.eq(statuses.count(201), 1, "exactly one 201")
    ck.eq(statuses.count(200), 23, "rest 200")
    ck.true(all(s in (200, 201) for s in statuses), "no other statuses", str(statuses))
    bodies = [json.dumps(r.json, sort_keys=True) for r in results if isinstance(r, Resp) and r.json]
    ck.eq(len(set(bodies)), 1, "all bodies identical as JSON value")
    m = h.request("GET", "/me", token=u.t("bob"))
    ck.eq((m.json or {}).get("balance"), 2600, "money moved exactly once")
    assert_conservation(h, ck, 12500, u.tokens)


@test("r61_concurrent_pay_same_key_once", "R61", "R3", "R74", needs=('requests', 'pay'))
def r61_concurrent_pay_same_key(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": unique_key()},
                   body={"payer_handle": "ada", "amount": 250})
    rid = (rq.json or {}).get("request_id")
    k = unique_key()
    fns = [lambda: h.request("POST", "/requests/%s/pay" % rid, token=u.t("ada"),
                             headers={"Idempotency-Key": k},
                             body={"visibility": "public"}) for _ in range(16)]
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    statuses = [r.status for r in results if isinstance(r, Resp)]
    ck.eq(statuses.count(201), 1, "exactly one 201")
    ck.eq(statuses.count(200), 15, "rest 200 replays")
    ck.true(409 not in statuses, "no request_not_pending among replays")
    bodies = {json.dumps(r.json, sort_keys=True) for r in results if isinstance(r, Resp) and r.json}
    ck.eq(len(bodies), 1, "identical payment body everywhere")
    assert_conservation(h, ck, 12500, u.tokens)
    rr = h.request("GET", "/requests?status=paid", token=u.t("ada"))
    paid = [q for q in (rr.json or {}).get("requests", []) if (q.get("request_id") or q.get("id")) == rid]
    ck.eq(len(paid), 1, "request paid exactly once")
    ck.true(paid and paid[0].get("payment_id"), "request carries payment_id")


@test("r62_replay_after_state_change", "R62", needs=('requests',))
def r62_replay_after_state_change(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    body = {"payer_handle": "ada", "amount": 60}
    rq = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": k}, body=body)
    ck.eq(rq.status, 201, "request created")
    dec = h.request("POST", "/requests/%s/decline" % (rq.json or {}).get("request_id"), token=u.t("ada"))
    ck.eq(dec.status, 200, "declined")
    before = h.request("GET", "/requests?direction=outgoing", token=u.t("bob")).json
    replay = h.request("POST", "/requests", token=u.t("bob"), headers={"Idempotency-Key": k}, body=body)
    ck.eq(replay.status, 200, "replay after decline -> 200 original")
    ck.eq(replay.json, rq.json, "original body (still pending status as at creation)")
    after = h.request("GET", "/requests?direction=outgoing", token=u.t("bob")).json
    ck.eq(after, before, "no new request created by replay")


@test("r63_claimed_key_before_field_validation", "R63", needs=('payments',))
def r63_claimed_key_wins(h, ck):
    ck.eq(h.request("POST", "/_test/reset", body=std_fixture()).status, 204, "reset")
    u = Users(h, ck)
    k = unique_key()
    ok = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k},
                   body={"to_handle": "bob", "amount": 10})
    ck.eq(ok.status, 201, "successful first use")
    cases = [("amount as string", {"to_handle": "bob", "amount": "50"}),
             ("unknown handle", {"to_handle": "nosuch", "amount": 5}),
             ("self payment", {"to_handle": "ada", "amount": 5}),
             ("bad visibility", {"to_handle": "bob", "amount": 5, "visibility": "friends"}),
             ("note null", {"to_handle": "bob", "amount": 5, "note": None})]
    for what, body in cases:
        r = h.request("POST", "/payments", token=u.t("ada"), headers={"Idempotency-Key": k}, body=body)
        ck.is_err(r, 409, "idempotency_key_reuse", "invalid body (%s) with claimed key -> 409" % what)


# ------------------------------------------------------------------ invariants: fuzz

def _mk_ops(h, u, rnd, pending_ids, round_no):
    handles = ["ada", "bob", "cy", "dave"]
    ops = []

    def add(method, path, token, body=None, raw=None, key=None):
        def op():
            return h.request(method, path, body=body, raw=raw, token=token,
                             headers=({"Idempotency-Key": key} if key else None))
        return op

    shared = unique_key("shared%d" % round_no)
    for _ in range(rnd.randint(10, 25)):
        kind = rnd.choice(["pay", "pay", "pay", "pay_bad", "rq", "rq", "pay_rq", "decline", "cancel",
                           "split", "settle", "malformed", "noauth", "list", "pay", "rq"])
        if kind == "pay":
            frm, to = rnd.choice(handles), rnd.choice(handles)
            ops.append(add("POST", "/payments", u.t(frm),
                           body={"to_handle": to, "amount": rnd.randint(1, 40),
                                 "note": rnd.choice(["", "n1", "☕"]), "visibility": rnd.choice(["public", "private"])},
                           key=rnd.choice([shared, unique_key()])))
        elif kind == "pay_bad":
            frm = rnd.choice(handles)
            body = rnd.choice([{"to_handle": frm, "amount": 5},
                               {"to_handle": "nosuch", "amount": 5},
                               {"to_handle": "cy", "amount": rnd.choice([0, "x", 9999999999])},
                               {"to_handle": "cy", "amount": 5, "visibility": "friends"}])
            ops.append(add("POST", "/payments", u.t(frm), body=body, key=unique_key()))
        elif kind == "rq":
            frm, to = rnd.choice(handles), rnd.choice(handles)
            ops.append(add("POST", "/requests", u.t(frm),
                           body={"payer_handle": to, "amount": rnd.randint(1, 40)},
                           key=rnd.choice([shared, unique_key()])))
        elif kind == "pay_rq" and pending_ids:
            rid = rnd.choice(list(pending_ids))
            payer = pending_ids[rid].get("payer")
            if payer:
                ops.append(add("POST", "/requests/%s/pay" % rid, u.t(payer),
                               body={"visibility": rnd.choice(["public", "private"])}, key=unique_key()))
        elif kind == "decline" and pending_ids:
            rid = rnd.choice(list(pending_ids))
            payer = pending_ids[rid].get("payer")
            if payer:
                ops.append(add("POST", "/requests/%s/decline" % rid, u.t(payer)))
        elif kind == "cancel" and pending_ids:
            rid = rnd.choice(list(pending_ids))
            requester = pending_ids[rid].get("requester")
            if requester:
                ops.append(add("POST", "/requests/%s/cancel" % rid, u.t(requester)))
        elif kind == "split":
            participants = rnd.sample(handles, rnd.randint(1, 4))
            ops.append(add("POST", "/splits", u.t(participants[0]),
                           body={"amount": rnd.randint(1, 60), "participant_handles": participants},
                           key=unique_key()))
        elif kind == "settle":
            legs = []
            for _ in range(rnd.randint(1, 3)):
                a, b = rnd.choice(handles), rnd.choice(handles)
                legs.append({"from_handle": a, "to_handle": b, "amount": rnd.randint(1, 30)})
            ops.append(add("POST", "/settlements", u.t("ada"), body={"transfers": legs}, key=unique_key()))
        elif kind == "malformed":
            ops.append(add("POST", "/payments", u.t("ada"), raw=rnd.choice([b"{{{", b"", b'"str"'])))
        elif kind == "noauth":
            ops.append(add("POST", "/payments", None, body={"to_handle": "bob", "amount": 5}))
        else:
            ops.append(add("GET", "/activity", u.t(rnd.choice(handles))))
    return ops


def _refresh_pending(h, u, pending):
    """pending: rid -> {"payer": hd, "requester": hd} for every pending request."""
    pending.clear()
    for hd in ("ada", "bob", "cy", "dave"):
        r = h.request("GET", "/requests?limit=200", token=u.t(hd))
        for q in (r.json or {}).get("requests", []):
            rid = q.get("request_id") or q.get("id")
            if q.get("status") == "pending" and rid:
                e = pending.setdefault(rid, {})
                if q.get("payer_handle") == hd:
                    e["payer"] = hd
                if q.get("requester_handle") == hd:
                    e["requester"] = hd


@test("r1_fuzz_mixed_operations_invariants", "R1", "R2", "R3", "R45", needs=('payments', 'requests', 'pay', 'splits', 'settlements', 'activity', 'list_requests'))
def r1_fuzz(h, ck):
    f = std_fixture(operators=["u_ada"], balances={"ada": 10000, "bob": 2500, "cy": 500, "dave": 300})
    ck.eq(h.request("POST", "/_test/reset", body=f).status, 204, "reset")
    u = Users(h, ck)
    rnd = random.Random(20261002)
    pending_ids = {}
    seeded_total = 13300  # 10000 + 2500 + 500 + 300 (+ frank 0)
    for round_no in range(10):
        _refresh_pending(h, u, pending_ids)
        ops = _mk_ops(h, u, rnd, pending_ids, round_no)
        results, errs = run_concurrently(ops)
        ck.true(not errs, "round %d: no thread errors" % round_no, str(errs)[:200])
        check_all_responses(h, ck, results, "round %d" % round_no, seeded_total=seeded_total, tokens=u.tokens)
        feeds_base = {hd: h.request("GET", "/activity?limit=200", token=u.t(hd)).json
                      for hd in ("ada", "bob", "cy", "dave")}
        reqs_base = {hd: h.request("GET", "/requests?limit=200", token=u.t(hd)).json
                     for hd in ("ada", "bob", "cy", "dave")}
        # replay burst: repeat identical requests (same method/path/key/body) -> no state change
        for op in ops:
            op()
        feeds_after = {hd: h.request("GET", "/activity?limit=200", token=u.t(hd)).json
                       for hd in ("ada", "bob", "cy", "dave")}
        for hd in feeds_base:
            ck.eq(feeds_after[hd], feeds_base[hd],
                  "round %d: %s activity unchanged after replay burst (replays move no money)" % (round_no, hd))
        for op in ops:
            op()
        reqs_after = {hd: h.request("GET", "/requests?limit=200", token=u.t(hd)).json
                      for hd in ("ada", "bob", "cy", "dave")}
        for hd in reqs_base:
            ck.eq(reqs_after[hd], reqs_base[hd],
                  "round %d: %s requests unchanged after second replay burst" % (round_no, hd))
        assert_conservation(h, ck, seeded_total, u.tokens)


@test("r2_concurrent_debits_never_negative", "R2", "R1", "R67", needs=('payments', 'me'))
def r2_concurrent_debits(h, ck):
    ck.eq(h.request("POST", "/_test/reset",
                    body=std_fixture(balances={"ada": 0, "bob": 2500, "cy": 0, "dave": 0})).status, 204,
          "reset: bob 2500 only funded account")
    u = Users(h, ck)
    fns = [lambda: h.request("POST", "/payments", token=u.t("bob"),
                             headers={"Idempotency-Key": unique_key("dr")},
                             body={"to_handle": "cy", "amount": 60}) for _ in range(50)]
    results, errs = run_concurrently(fns)
    ck.true(not errs, "no thread errors", str(errs)[:300])
    statuses = [r.status for r in results if isinstance(r, Resp)]
    ck.true(all(s in (201, 409) for s in statuses), "only 201 or 409", str(set(statuses)))
    ck.eq(statuses.count(201), 41, "exactly 41 payments succeed (2500 // 60)")
    ck.eq(statuses.count(409), 9, "rest 409 insufficient_funds")
    mb = h.request("GET", "/me", token=u.t("bob")).json or {}
    ck.eq(mb.get("balance"), 2500 - 41 * 60, "bob debited exactly the successful total")
    ck.true(mb.get("balance", -1) >= 0, "bob never negative")
    assert_conservation(h, ck, 2500, u.tokens)