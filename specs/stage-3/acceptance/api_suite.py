"""Pocketful stage-3 acceptance tests — black-box, from the spec only.

Covers R193-R290 of specs/stage-3/requirements.md (we-are-devs/pocketful
spec stage-3.md) plus the carried stage-1 invariants (R1, R2, R3, R45,
stage-1 §9 rounding) under stage-3 conditions. Every test resets the service
with its own fixture first, so order does not matter.
"""

import json
from datetime import datetime, timedelta, timezone

import core
from core import (check_wallet_shape, eq, expect, fixture, fx_auth, fx_pay,
                  fx_user, iso, parallel, parse_ts, rfc3339_like,
                  statement_invariants, test)

S = lambda n: core.past(n)  # an instant n seconds before the suite loaded


def err_is_(r, status, code, what):
    eq(r.status, status, f"{what}: status")
    eq(r.err_code(), code, f"{what}: error code (body {r.raw[:200]!r})")


def hist_fixture():
    """ada 8600 / bob 6400 after history; openings 10000 / 5000 (R216)."""
    return fixture(
        [fx_user("u_ada", "ada", 8600), fx_user("u_bob", "bob", 6400),
         fx_user("u_cyd", "cyd", 0)],
        operators=["u_ada"],
        payments=[
            fx_pay("p1", "u_ada", "u_bob", 1500, created_at=S(5000)),
            fx_pay("p2", "u_bob", "u_ada", 300, created_at=S(4000)),
            fx_pay("p3", "u_ada", "u_bob", 200, created_at=S(3000)),
        ])


def hist_users(ctx):
    ctx.reset(hist_fixture())
    return [User_(ctx.api, h).login() for h in ("ada", "bob", "cyd")]


def User_(api, handle):
    return core.User(api, handle)


CORR_BODY = {"expected_revision": 1, "amount": 1200,
             "effective_at": S(5000), "reason": "corrected amount"}


# ------------------------------------------------------------- A. timestamps

@test("created_at_on_payment_responses", "R193 R194")
def created_at_on_payment_responses(ctx):
    ada, bob, cyd = hist_users(ctx)
    feed = ada.activity()
    eq([p["payment_id"] for p in feed], ["p3", "p2", "p1"],
       "activity newest-first by created_at (stage-1 rule retained)")
    for p in feed:
        rfc3339_like(p["created_at"], f"activity {p['payment_id']}")
        eq(p["created_at"], {"p1": S(5000), "p2": S(4000), "p3": S(3000)}[p["payment_id"]],
           f"seeded {p['payment_id']} created_at exact")
    st = ada.statement()
    for e in st["entries"]:
        rfc3339_like(e["payment"]["created_at"], "statement entry created_at")
    rev1 = ada.revisions("p1").json["revisions"][0]
    eq(rev1["effective_at"], S(5000), "rev1 effective_at == created_at")
    eq(rev1["recorded_at"], S(5000), "rev1 recorded_at == created_at")


@test("seeded_created_at_semantics", "R195 R215 R197")
def seeded_created_at_semantics(ctx):
    ctx.reset(fixture([fx_user("u_ada", "ada", 700), fx_user("u_bob", "bob", 300)],
                      payments=[fx_pay("p1", "u_ada", "u_bob", 300,
                                       created_at=S(7200)),
                                fx_pay("p2", "u_bob", "u_ada", 100)]))
    ada = User_(ctx.api, "ada").login()
    eq(ada.me()["total"], 700, "fixture balance is the after-seed balance (R197)")
    st = ada.statement()
    eq(st["entries"][0]["payment"]["created_at"], S(7200), "supplied created_at kept")
    ts_reset = parse_ts(st["entries"][1]["payment"]["created_at"])
    expect(abs(ts_reset - datetime.now(timezone.utc)) < 300,
           "omitted created_at uses reset time")
    after = ada.pay("bob", 50, key="post-seed")
    eq(after.status, 201, "API payment after seed")
    expect(parse_ts(after.json["created_at"]) > ts_reset,
           "seeded omitted-payment predates subsequent API payments (R195)")
    revs = ada.revisions("p2").json["revisions"]
    eq(revs[0]["effective_at"], st["entries"][1]["payment"]["created_at"],
       "rev1 effective == recorded == created_at for a reset-time seed (R215)")


@test("seeded_future_created_at_rejected_atomically", "R196 R218")
def seeded_future_created_at_rejected_atomically(ctx):
    ada, bob, cyd = hist_users(ctx)
    totals_before = (ada.me()["total"], bob.me()["total"])
    bad = fixture([fx_user("u_ada", "ada", 8600), fx_user("u_bob", "bob", 6400)],
                  payments=[fx_pay("p1", "u_ada", "u_bob", 500,
                                   created_at=S(-3600))])
    err_is_(ctx.api.post("/_test/reset", body=bad), 422, "validation_failed",
            "future seeded created_at")
    eq(ada.me()["total"], totals_before[0], "no state change (ada)")
    eq(bob.me()["total"], totals_before[1], "no state change (bob)")
    eq(ctx.api.post("/_test/reset", body=hist_fixture()).status, 204,
       "valid reset still accepted afterwards")
    bad2 = fixture([fx_user("u_ada", "ada", -1)])
    err_is_(ctx.api.post("/_test/reset", body=bad2), 422, "validation_failed",
            "negative seeded balance rejected atomically (R218)")


# ------------------------------------------------------------- B. GET /me as_of

@test("me_as_of_validation", "R198 R199 R252")
def me_as_of_validation(ctx):
    ada, bob, cyd = hist_users(ctx)
    for bad in ("2026-09-24%2013%3A20%3A00", "2026-09-24", "", "not-a-time"):
        err_is_(ctx.api.get(f"/me?as_of={bad}", token=ada.token), 422,
                "validation_failed", f"as_of={bad!r}")
    for bad in ("2026-09-24%2013%3A20%3A00", "2026-09-24", ""):
        err_is_(ctx.api.get(f"/statement?known_at={bad}", token=ada.token), 422,
                "validation_failed", f"statement known_at={bad!r}")


@test("me_as_of_semantics", "R201 R202 R203 R204 R216 R251")
def me_as_of_semantics(ctx):
    ada, bob, cyd = hist_users(ctx)
    probes = [
        (S(5500), 10000, "before earliest payment -> opening balance"),
        (S(5000), 8500, "payment at exactly as_of counts (inclusive)"),
        (S(4500), 8500, "between p1 and p2"),
        (S(4000), 8800, "p2 at exactly as_of counts too"),
        (S(60), 8600, "after latest -> current balance"),
        (S(-3600), 8600, "future as_of -> current balance (R251)"),
    ]
    for inst, want, what in probes:
        m = ada.me(f"as_of={inst}")
        eq(m["balance"], want, f"as_of: {what}")
        eq(m["total"], want, f"as_of: total matches balance")
        eq(m["available"], want, f"as_of: available (no holds)")
        eq(m["held"], 0, f"as_of: held 0")
    m = ctx.api.get("/me?as_of=2026-09-24T13%3A20%3A00%2B05%3A30", token=ada.token)
    eq(m.status, 200, "odd-offset as_of accepted")
    eq(m.json["as_of"], "2026-09-24T13:20:00+05:30",
       "as_of echoed exactly as given (R204)")


@test("me_without_temporal_params_current", "R200 R259")
def me_without_temporal_params_current(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = ada.correct("p3", {"expected_revision": 1, "amount": 300,
                           "effective_at": S(3000), "reason": "bump"}, key="cc")
    eq(r.status, 201, f"correction: {r}")
    eq(ada.me()["total"], 8500, "current view reflects the correction (8600+100)")
    eq(bob.me()["total"], 6500, "counterparty corrected too")
    check_wallet_shape(ada.me(), "no temporal params")


# ------------------------------------------------------------- C. statement

@test("statement_defaults_shape_window", "R205 R206 R207 R208 R209 R210 R211")
def statement_defaults_shape_window(ctx):
    ada, bob, cyd = hist_users(ctx)
    st = ada.statement()
    statement_invariants(st, ada.user_id, "ada default statement")
    eq(st["opening_balance"], 10000, "default from = wallet opening")
    eq(st["closing_balance"], 8600, "default to = now")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p1", "p2", "p3"],
       "oldest first")
    eq([e["delta"] for e in st["entries"]], [-1500, 300, -200], "delta signs")
    eq([e["balance_after"] for e in st["entries"]], [8500, 8800, 8600],
       "balance after each payment")
    eq(st["opening_balance"] + sum(e["delta"] for e in st["entries"]),
       st["closing_balance"], "opening + deltas == closing (R211)")
    eq(st["has_more"], False, "has_more false without limit")
    sb = bob.statement()
    statement_invariants(sb, bob.user_id, "bob default statement")
    eq([e["payment"]["payment_id"] for e in sb["entries"]], ["p1", "p2", "p3"],
       "received payments appear too")
    eq([e["delta"] for e in sb["entries"]], [1500, -300, 200], "receiver signs")
    eq(sb["opening_balance"], 5000, "bob opening")
    eq(sb["closing_balance"], 6400, "bob closing")
    err_is_(ctx.api.get("/statement?limit=0", token=ada.token), 422,
            "validation_failed", "limit bounds as in /requests (R206)")
    err_is_(ctx.api.get("/statement?offset=-1", token=ada.token), 422,
            "validation_failed", "offset bounds as in /requests (R206)")


@test("statement_window_half_open", "R205 R207 R210")
def statement_window_half_open(ctx):
    ada, bob, cyd = hist_users(ctx)
    st = ada.statement(f"from={S(4000)}&to={S(3000)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2"],
       "from inclusive, to exclusive")
    eq(st["opening_balance"], 8500, "balance immediately before from")
    eq(st["closing_balance"], 8800, "balance immediately before to")
    st2 = ada.statement(f"from={S(4000)}&to={S(2999)}")
    eq([e["payment"]["payment_id"] for e in st2["entries"]], ["p2", "p3"],
       "payment just before `to` included")
    eq(st2["closing_balance"], 8600, "closing moved with to")


@test("statement_ordering_ties_by_id", "R209")
def statement_ordering_ties_by_id(ctx):
    ctx.reset(fixture(
        [fx_user("u_ada", "ada", 1700), fx_user("u_bob", "bob", 300)],
        payments=[fx_pay("p_b", "u_ada", "u_bob", 100, created_at=S(2000)),
                  fx_pay("p_a", "u_ada", "u_bob", 200, created_at=S(2000))]))
    ada = User_(ctx.api, "ada").login()
    st = ada.statement()
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p_a", "p_b"],
       "same created_at -> payment id ascending")


@test("statement_pagination_full_window", "R206 R211 R212 R266")
def statement_pagination_full_window(ctx):
    ctx.reset(fixture(
        [fx_user("u_ada", "ada", 5300), fx_user("u_bob", "bob", 4700)],
        payments=[fx_pay(f"p{i}", "u_ada", "u_bob", 100 + i,
                         created_at=S(6000 - i * 500)) for i in range(1, 7)]))
    ada = User_(ctx.api, "ada").login()
    full = ada.statement()
    ids = [e["payment"]["payment_id"] for e in full["entries"]]
    eq(len(ids), 6, "six entries")
    by_id = {e["payment"]["payment_id"]: e for e in full["entries"]}
    seen, offset = [], 0
    while True:
        pg = ada.statement(f"limit=2&offset={offset}")
        eq(pg["opening_balance"], full["opening_balance"],
           "opening_balance independent of pagination")
        eq(pg["closing_balance"], full["closing_balance"],
           "closing_balance independent of pagination")
        for e in pg["entries"]:
            eq(e["balance_after"], by_id[e["payment"]["payment_id"]]["balance_after"],
               "balance_after independent of pagination")
            eq(e["delta"], by_id[e["payment"]["payment_id"]]["delta"], "delta stable")
        seen += [e["payment"]["payment_id"] for e in pg["entries"]]
        if not pg["has_more"]:
            break
        offset += len(pg["entries"])
    eq(seen, ids, "paged entries == full window, order kept")
    tail = ada.statement("limit=2&offset=99")
    eq(tail["entries"], [], "offset beyond end: empty page")
    eq(tail["has_more"], False, "beyond end has_more false")
    eq(tail["opening_balance"], full["opening_balance"], "beyond end opening kept")


@test("statement_visibility_own_only", "R213")
def statement_visibility_own_only(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = bob.pay("cyd", 250, visibility="public", key="pub-1")
    eq(r.status, 201, "public payment")
    sa = ada.statement()
    eq([e["payment"]["payment_id"] for e in sa["entries"]], ["p1", "p2", "p3"],
       "other users' public payment absent from ada's statement")
    sc = cyd.statement()
    eq([e["payment"]["payment_id"] for e in sc["entries"]], ["p4"],
       "cyd sees the payment sent to them")
    expect(any(p["payment_id"] == "p4" for p in ada.activity()),
           "but the activity feed still shows the public payment (rules differ)")


@test("statement_entry_revision_fields_uncorrected", "R214 R259")
def statement_entry_revision_fields_uncorrected(ctx):
    ada, bob, cyd = hist_users(ctx)
    st = ada.statement()
    amounts = {"p1": 1500, "p2": 300, "p3": 200}
    for e in st["entries"]:
        eq(e["revision"], 1, "revision 1 selected")
        eq(e["effective_at"], e["payment"]["created_at"],
           "rev1 effective_at == created_at")
        eq(e["recorded_at"], e["payment"]["created_at"],
           "rev1 recorded_at == created_at")
        eq(e["payment"]["amount"], amounts[e["payment"]["payment_id"]],
           "amount as originally paid")
    eq(bob.statement("known_at=2020-01-01T00:00:00%2B00:00")["entries"], [],
       "known_at before everything: no payment recorded yet -> contributes nothing")


# ------------------------------------------------------------- E. corrections

@test("corrections_auth_and_lookup_errors", "R219 R220 R221 R245 R59 R63")
def corrections_auth_and_lookup_errors(ctx):
    ada, bob, cyd = hist_users(ctx)
    err_is_(ada.correct("p1", CORR_BODY, key=None), 400,
            "missing_idempotency_key", "no idempotency key")
    err_is_(bob.correct("p1", CORR_BODY, key="k1"), 403, "forbidden",
           "authenticated receiver is not the sender")
    err_is_(cyd.correct("p1", CORR_BODY, key="k2"), 403, "forbidden", "third party")
    err_is_(ada.correct("p_nope", CORR_BODY, key="k3"), 404, "not_found",
            "unknown payment")
    eq(ctx.api.post("/payments/p1/corrections", body=CORR_BODY).status, 401,
       "no token -> 401")
    # A15: payment lookup precedes field validation and the sender check
    err_is_(ada.correct("p_nope", {"expected_revision": -5}, key="k4"),
            404, "not_found", "unknown payment beats an invalid body")
    err_is_(bob.correct("p_nope", {"expected_revision": -5}, key="k5"),
            404, "not_found", "unknown payment beats the sender check too")
    # R63: a successful key claims reuse before any resource checks
    eq(ada.correct("p1", CORR_BODY, key="k6").status, 201, "successful correction")
    err_is_(ada.correct("p_nope", {"expected_revision": 1}, key="k6"),
            409, "idempotency_key_reuse", "different body reusing a successful key")


@test("corrections_body_validation", "R222 R223 R224 R225 R226 R227")
def corrections_body_validation(ctx):
    ada, bob, cyd = hist_users(ctx)
    base = dict(CORR_BODY)
    cases = []
    for field in ("expected_revision", "amount", "effective_at", "reason"):
        b = dict(base)
        b.pop(field)
        cases.append((f"missing {field}", b))
    for v in (0, -1, "1", 1.5):
        cases.append((f"expected_revision={v!r}", dict(base, expected_revision=v)))
    for v in (-1, 1000000001, "5", 1.5):
        cases.append((f"amount={v!r}", dict(base, amount=v)))
    for v in ("", "x" * 201, 7):
        cases.append((f"reason={v!r}", dict(base, reason=v)))
    for v in (S(-3600), S(60), "2026-09-24", "", "2026-09-24T13:20:00"):
        cases.append((f"effective_at={v!r}", dict(base, effective_at=v)))
    for i, (what, body) in enumerate(cases):
        err_is_(ada.correct("p1", body, key=f"bad-{i}"), 422, "validation_failed",
                what)
    ok = ada.correct("p1", {"expected_revision": 1, "amount": 1400,
                            "effective_at": S(5000), "reason": "x" * 200,
                            "junk_field": 1}, key="ok-bounds")
    eq(ok.status, 201, f"200-char reason and amount 1400 accepted: {ok}")


@test("correction_success_shape_revisions", "R214 R229 R230 R243")
def correction_success_shape_revisions(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = ada.correct("p1", CORR_BODY, key="c1")
    eq(r.status, 201, f"first correction: {r}")
    b = r.json
    for f in ("payment_id", "revision", "amount", "effective_at",
              "recorded_at", "reason"):
        expect(f in b, f"201 body missing {f!r}: {sorted(b)}")
    eq(b["payment_id"], "p1", "payment_id")
    eq(b["revision"], 2, "revision 2")
    eq(b["amount"], 1200, "amount")
    eq(b["effective_at"], S(5000), "effective_at echoed")
    eq(b["reason"], "corrected amount", "reason echoed")
    rfc3339_like(b["recorded_at"], "server-assigned recorded_at")
    revs = ada.revisions("p1").json["revisions"]
    eq(len(revs), 2, "two revisions")
    eq(revs[0]["revision"], 1, "revision order")
    eq(revs[0]["amount"], 1500, "rev1 original amount")
    eq(revs[0]["reason"], "", "rev1 reason empty")
    eq(revs[1]["revision"], 2, "rev2")
    eq(revs[1]["amount"], 1200, "rev2 amount")
    expect(parse_ts(revs[1]["recorded_at"]) > parse_ts(revs[0]["recorded_at"]),
           "recorded times strictly increase (R230)")
    r2 = ada.correct("p1", {"expected_revision": 2, "amount": 1100,
                            "effective_at": S(5000), "reason": "again"}, key="c2")
    eq(r2.status, 201, "second correction")
    eq(r2.json["revision"], 3, "revision 3")
    revs2 = ada.revisions("p1").json["revisions"]
    expect(parse_ts(revs2[2]["recorded_at"]) > parse_ts(revs2[1]["recorded_at"]),
           "recorded keeps strictly increasing")


@test("correction_stale_revision", "R231 R232")
def correction_stale_revision(ctx):
    ada, bob, cyd = hist_users(ctx)
    err_is_(ada.correct("p1", dict(CORR_BODY, expected_revision=5), key="s1"),
            409, "stale_revision", "expected_revision ahead of history")
    eq(ada.correct("p1", CORR_BODY, key="s2").status, 201, "valid correction")
    err_is_(ada.correct("p1", dict(CORR_BODY, expected_revision=1), key="s3"),
            409, "stale_revision", "now-stale revision 1")
    rp = ada.correct("p1", CORR_BODY, key="s2")
    eq(rp.status, 200, "replay of the successful correction wins over staleness")
    eq(rp.json["revision"], 2, "replay returns the original revision")


@test("correction_replay_reuse_exactly_once", "R232 R233 R241 R3 R59")
def correction_replay_reuse_exactly_once(ctx):
    ada, bob, cyd = hist_users(ctx)
    first = ada.correct("p1", CORR_BODY, key="k")
    eq(first.status, 201, "first use")
    rp = ada.correct("p1", CORR_BODY, key="k")
    eq(rp.status, 200, "replay -> 200")
    eq(rp.json, first.json, "replay body identical (JSON value)")
    err_is_(ada.correct("p1", dict(CORR_BODY, amount=1300), key="k"),
            409, "idempotency_key_reuse", "different body, same key")
    eq(ada.correct("p1", dict(CORR_BODY, reason="again"), key="k2").status, 201,
       "second correction (rev 3)")
    rp2 = ada.correct("p1", CORR_BODY, key="k")
    eq(rp2.status, 200, "replay still 200 after newer revisions (R232)")
    eq(rp2.json["revision"], 2, "replay returns that original revision")
    eq(ada.revisions("p1").json["revisions"][-1]["revision"], 3,
       "replays appended no revision")
    eq(bob.me()["total"], 6000, "money moved exactly once (6400-300-100)")
    pay = ada.pay("bob", 700, key="orig-1")
    eq(pay.status, 201, "API payment")
    eq(ada.correct(pay.json["payment_id"],
                   {"expected_revision": 1, "amount": 600,
                    "effective_at": pay.json["created_at"], "reason": "r"},
                   key="corr-orig").status, 201, "correct the API payment")
    rp3 = ada.api.post("/payments", body={"to_handle": "bob", "amount": 700},
                       token=ada.token, key="orig-1")
    eq(rp3.status, 200, "original idempotent payment replay unchanged (R241)")
    eq(rp3.json["payment_id"], pay.json["payment_id"], "same payment")
    orig = [p for p in ada.activity() if p["payment_id"] == pay.json["payment_id"]]
    eq(orig[0]["amount"], 700, "activity keeps the original payment (R242)")


@test("correction_preserves_parties_visibility_activity", "R228 R242")
def correction_preserves_parties_visibility_activity(ctx):
    ada, bob, cyd = hist_users(ctx)
    before_feed = ada.activity()
    eq(ada.correct("p1", CORR_BODY, key="v1").status, 201, "correct public p1")
    eq(bob.correct("p2", {"expected_revision": 1, "amount": 100,
                          "effective_at": S(4000), "reason": "sender fixes"},
                   key="v2").status, 201, "bob corrects p2 (bob is p2's sender)")
    after_feed = ada.activity()
    eq([p["payment_id"] for p in after_feed], [p["payment_id"] for p in before_feed],
       "corrections add no feed payments")
    p1_feed = [p for p in after_feed if p["payment_id"] == "p1"][0]
    eq(p1_feed["amount"], 1500, "feed shows the original payment")
    eq((p1_feed["from_user_id"], p1_feed["to_user_id"]), ("u_ada", "u_bob"),
       "parties unchanged (R228)")
    expect(any(p["payment_id"] == "p1" for p in cyd.activity()),
           "public payment still public after correction")
    st = cyd.statement()
    expect(all(e["payment"]["payment_id"] != "p1" for e in st["entries"]),
           "and still absent from a stranger's statement")


@test("revisions_endpoint_access", "R243 R244 R245")
def revisions_endpoint_access(ctx):
    ada, bob, cyd = hist_users(ctx)
    eq(ada.correct("p1", CORR_BODY, key="a1").status, 201, "correction")
    for u, what in ((ada, "sender"), (bob, "receiver")):
        r = u.revisions("p1")
        eq(r.status, 200, f"{what} can read revisions")
        eq([x["revision"] for x in r.json["revisions"]], [1, 2], "in revision order")
        eq(r.json["revisions"][0]["reason"], "", "rev1 reason empty")
    err_is_(cyd.revisions("p1"), 404, "not_found",
            "third party, public payment -> 404")
    eq(ctx.api.get("/payments/p1/revisions").status, 401, "no token -> 401")


# ------------------------------------------------------------- F. money movement

@test("correction_moves_money_conserves", "R234 R235 R240 R216")
def correction_moves_money_conserves(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = ada.correct("p1", CORR_BODY, key="m1")  # 1500 -> 1200, decrease
    eq(r.status, 201, "decrease correction")
    eq(ada.me()["total"], 8900, "decrease debits the original receiver (ada +300)")
    eq(bob.me()["total"], 6100, "...and credits the original sender (bob -300)")
    r2 = bob.correct("p2", {"expected_revision": 1, "amount": 600,
                            "effective_at": S(4000), "reason": "up"}, key="m2")
    eq(r2.status, 201, "increase correction (bob is p2's sender)")
    eq(bob.me()["total"], 5800, "increase debits the original sender (bob -300)")
    eq(ada.me()["total"], 9200, "...and credits the original receiver (ada +300)")
    eq(ada.me()["total"] + bob.me()["total"] + cyd.me()["total"], 15000,
       "conservation now (R240)")
    for inst in (S(5500), S(5000), S(4500), S(4000), S(3500), S(3000), S(60)):
        tot = sum(u.me(f"as_of={inst}")["total"] for u in (ada, bob, cyd))
        eq(tot, 15000, f"conservation at as_of={inst} (R240)")
    eq(ada.me(f"as_of={S(5500)}")["balance"], 10000,
       "ada opening balance unchanged by corrections (R216)")
    eq(bob.me(f"as_of={S(5500)}")["balance"], 5000, "bob opening unchanged (R216)")


@test("correction_insufficient_funds_preserves", "R236 R239 R285 R59")
def correction_insufficient_funds_preserves(ctx):
    ada, bob, cyd = hist_users(ctx)
    err_is_(ada.correct("p1", {"expected_revision": 1, "amount": 90000000,
                               "effective_at": S(5000), "reason": "huge"}, key="f1"),
            409, "insufficient_funds", "sender cannot afford the increase")
    err_is_(bob.correct("p2", {"expected_revision": 1, "amount": 800000000,
                               "effective_at": S(4000), "reason": "huge"}, key="f2"),
            409, "insufficient_funds", "increase debits original sender bob")
    eq(ada.me()["total"], 8600, "ada unchanged after rejections")
    eq(bob.me()["total"], 6400, "bob unchanged")
    eq(len(ada.revisions("p1").json["revisions"]), 1, "no revision appended")
    eq([e["payment"]["amount"] for e in ada.statement()["entries"]],
       [1500, 300, 200], "statement unchanged")
    ok = ada.correct("p1", {"expected_revision": 1, "amount": 1400,
                            "effective_at": S(5000), "reason": "fine"}, key="f1")
    eq(ok.status, 201, "key from a failed attempt acts as first use (R59/R239)")


@test("correction_historical_overdraft", "R237 R238 R239")
def correction_historical_overdraft(ctx):
    ctx.reset(fixture(
        [fx_user("u_carol", "carol", 1100), fx_user("u_dave", "dave", 0),
         fx_user("u_erin", "erin", 400), fx_user("u_frank", "frank", 2000)],
        payments=[fx_pay("h1", "u_dave", "u_carol", 500, created_at=S(4000)),
                  fx_pay("h2", "u_carol", "u_erin", 400, created_at=S(3000)),
                  fx_pay("h3", "u_frank", "u_carol", 1000, created_at=S(2000))]))
    carol = User_(ctx.api, "carol").login()
    dave = User_(ctx.api, "dave").login()
    err_is_(dave.correct("h1", {"expected_revision": 1, "amount": 100,
                                "effective_at": S(4000), "reason": "reduce"},
                         key="ho1"),
            409, "historical_overdraft",
            "carol would go negative at h2's boundary (100-400)")
    eq(carol.me()["total"], 1100, "balances preserved")
    eq(len(carol.revisions("h1").json["revisions"]), 1, "revision history preserved")
    eq(carol.statement("known_at=2030-01-01T00:00:00%2B00:00")["closing_balance"],
       1100, "statements preserved")
    ok = dave.correct("h1", {"expected_revision": 1, "amount": 450,
                             "effective_at": S(4000), "reason": "small reduce"},
                      key="ho2")
    eq(ok.status, 201, "boundary stays >= 0 (450-400=50): accepted")
    eq(carol.me(f"as_of={S(3000)}")["balance"], 50, "boundary value exact")
    # R238: all movements landing on one instant are combined at that boundary
    ctx.reset(fixture(
        [fx_user("u_carol", "carol", 100), fx_user("u_dave", "dave", 0),
         fx_user("u_frank", "frank", 400)],
        payments=[fx_pay("h1", "u_dave", "u_carol", 500, created_at=S(4000)),
                  fx_pay("h2", "u_carol", "u_frank", 400, created_at=S(4000))]))
    carol2 = User_(ctx.api, "carol").login()
    both = carol2.correct("h2", {"expected_revision": 1, "amount": 500,
                                 "effective_at": S(4000), "reason": "same instant"},
                          key="ho3")
    eq(both.status, 201,
       "combined boundary (+500 in, -500 out) lands at exactly 0: accepted (R238)")
    eq(carol2.me(f"as_of={S(4000)}")["balance"], 0, "combined boundary value")


@test("correction_zero_amount_reversal", "R224 R257")
def correction_zero_amount_reversal(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = ada.correct("p1", {"expected_revision": 1, "amount": 0,
                           "effective_at": S(5000), "reason": "reversal"}, key="z1")
    eq(r.status, 201, "amount 0 reverses the entire payment")
    eq(r.json["amount"], 0, "revision amount 0")
    eq(ada.me()["total"], 10100, "ada refunded (10000 +300 -200)")
    eq(bob.me()["total"], 4900, "bob debited back")
    st = ada.statement()
    e1 = st["entries"][0]
    eq(e1["payment"]["payment_id"], "p1", "still an entry")
    eq(e1["delta"], 0, "zero delta (R257)")
    eq(e1["payment"]["amount"], 0, "selected amount 0")
    eq(e1["balance_after"], 10000, "walking balance skips the movement")
    eq(st["closing_balance"], 10100, "closing reflects the reversal")


@test("correction_noop_same_amount", "design-19")
def correction_noop_same_amount(ctx):
    "Design §19 step 6: restating the amount is valid and moves no money."
    ada, bob, cyd = hist_users(ctx)
    r = ada.correct("p1", {"expected_revision": 1, "amount": 1500,
                           "effective_at": S(5000), "reason": "restated"}, key="n1")
    eq(r.status, 201, f"same-amount correction accepted: {r}")
    eq(r.json["revision"], 2, "revision appended")
    eq(ada.me()["total"], 8600, "no money moved (ada)")
    eq(bob.me()["total"], 6400, "no money moved (bob)")


# ------------------------------------------------------------- H. known_at

@test("known_at_validation_and_echo", "R246 R252 R253")
def known_at_validation_and_echo(ctx):
    ada, bob, cyd = hist_users(ctx)
    for bad in ("2026-09-24%2013%3A20%3A00", "2026-09-24", "", "garbage"):
        err_is_(ctx.api.get(f"/me?known_at={bad}", token=ada.token), 422,
                "validation_failed", f"me known_at={bad!r}")
    m = ctx.api.get("/me?known_at=2026-09-24T13%3A20%3A00%2B05%3A30", token=ada.token)
    eq(m.status, 200, "odd-offset known_at accepted")
    eq(m.json["known_at"], "2026-09-24T13:20:00+05:30",
       "known_at echoed exactly (R253)")


@test("known_at_selection_semantics", "R247 R248 R249 R254 R256")
def known_at_selection_semantics(ctx):
    ada, bob, cyd = hist_users(ctx)
    m = ada.me(f"known_at={S(4500)}")  # p1 recorded S(5000) <= ? no: p1 recorded_at S(5000) > S(4500)
    eq(m["balance"], 10000, "known_at before p1's recording: p1 contributes nothing")
    m = ada.me(f"known_at={S(3500)}")  # p1 (S(5000)) and p2 (S(4000)) known
    eq(m["balance"], 8800, "only revisions recorded at or before known_at count")
    eq(ada.me()["balance"], 8600, "omission means everything known now (R248)")
    r = ada.correct("p1", {"expected_revision": 1, "amount": 1200,
                           "effective_at": S(5000), "reason": "late fix"}, key="kn1")
    eq(r.status, 201, "correction recorded now")
    eq(ada.me(f"known_at={S(4500)}")["balance"], 10000,
       "known_at before the correction's recording still selects rev1")
    eq(ada.me(f"known_at={S(-60)}")["balance"], 8300,
       "known_at after recording selects rev2 (8600-300)")
    eq(ada.me(f"known_at={S(-3600)}")["balance"], 8300,
       "future known_at allowed (R251), sees rev2")
    st = ada.statement(f"known_at={S(4500)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], [], "nothing selected yet")
    st = ada.statement(f"known_at={S(3500)}")
    eq([(e["payment"]["payment_id"], e["payment"]["amount"], e["revision"])
        for e in st["entries"]], [("p1", 1500, 1)], "rev1 selected at that knowledge")
    st = ada.statement(f"known_at={S(-60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p1", "p2", "p3"],
       "effective_at unchanged -> original order")
    eq(st["entries"][0]["payment"]["amount"], 1200, "selected amount (R256)")
    eq(st["entries"][0]["effective_at"], S(5000), "selected effective_at")
    rfc3339_like(st["entries"][0]["recorded_at"], "selected recorded_at")
    r = ada.correct("p1", {"expected_revision": 2, "amount": 1200,
                           "effective_at": S(3500), "reason": "moved"}, key="kn2")
    eq(r.status, 201, "correction moving effective_at")
    st = ada.statement(f"known_at={S(-60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2", "p1", "p3"],
       "ordered by selected effective_at, then id (R254)")
    eq([e["balance_after"] for e in st["entries"]], [10300, 9100, 8900],
       "balance walk follows the new order")


@test("statement_known_at_window_and_combo", "R250 R251 R255 R258")
def statement_known_at_window_and_combo(ctx):
    ada, bob, cyd = hist_users(ctx)
    eq(ada.correct("p1", CORR_BODY, key="w1").status, 201, "correction")
    st = ada.statement(f"from={S(4000)}&to={S(3000)}&known_at={S(-60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2"],
       "half-open window retained under known_at")
    eq(st["opening_balance"], 8800,
       "opening uses selected revisions, strict before from")
    for f in ("revision", "effective_at", "recorded_at"):
        expect(f in st["entries"][0], f"entry carries {f} (R255)")
    m = ada.me(f"as_of={S(5000)}&known_at={S(-60)}")
    eq(m["balance"], 8800, "as_of inclusive under known_at (R250)")
    m = ada.me(f"as_of={S(-3600)}&known_at={S(-3600)}")
    eq(m["balance"], 8300, "future as_of and known_at both allowed (R251)")
    st = ada.statement(f"known_at={S(-60)}")
    eq(sum(1 for e in st["entries"] if e["payment"]["payment_id"] == "p1"), 1,
       "exactly one entry for the corrected payment (R258)")


# ------------------------------------------------------------- I. snapshots

@test("snapshot_freeze_across_changes", "R260 R261 R262 R265 R290")
def snapshot_freeze_across_changes(ctx):
    ada, bob, cyd = hist_users(ctx)
    full = ada.statement()
    expect(isinstance(full.get("snapshot"), str),
           f"first statement response carries a snapshot token: {sorted(full)}")
    token = full["snapshot"]
    expect(isinstance(ada.statement("limit=2").get("snapshot"), str),
           "any non-snapshot call returns a fresh token (R260)")
    eq(ada.pay("bob", 333, key="np1").status, 201, "new payment after snapshot")
    eq(ada.correct("p1", {"expected_revision": 1, "amount": 1100,
                          "effective_at": S(5000), "reason": "post-snapshot"},
                   key="np2").status, 201, "correction after snapshot")
    eq(ada.me()["total"], 8533, "live view moved (8600-400+333)")
    frozen = ada.statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]],
       [e["payment"]["payment_id"] for e in full["entries"]], "same entries as frozen")
    eq(frozen["opening_balance"], full["opening_balance"], "frozen opening")
    eq(frozen["closing_balance"], full["closing_balance"], "frozen closing")
    for e in frozen["entries"]:
        match = [f for f in full["entries"]
                 if f["payment"]["payment_id"] == e["payment"]["payment_id"]][0]
        eq(e["balance_after"], match["balance_after"], "frozen balance_after")
        eq(e["payment"]["amount"], match["payment"]["amount"],
           "frozen selected amount")
    seen, offset = [], 0
    while True:
        pg = ada.statement(f"snapshot={token}&limit=2&offset={offset}")
        eq(pg["opening_balance"], full["opening_balance"], "paged frozen opening")
        seen += [e["payment"]["payment_id"] for e in pg["entries"]]
        if not pg["has_more"]:
            break
        offset += len(pg["entries"])
    eq(seen, [e["payment"]["payment_id"] for e in full["entries"]], "paged frozen entries")


@test("snapshot_param_rules", "R263 R264 R267")
def snapshot_param_rules(ctx):
    ada, bob, cyd = hist_users(ctx)
    token = ada.statement()["snapshot"]
    for q in (f"snapshot={token}&from={S(4000)}", f"snapshot={token}&to={S(3000)}",
              f"snapshot={token}&known_at={S(4000)}"):
        err_is_(ctx.api.get(f"/statement?{q}", token=ada.token), 422,
                "validation_failed", f"only limit/offset with snapshot: {q}")
    err_is_(ctx.api.get(f"/statement?snapshot=snap_nope&from={S(4000)}",
                        token=ada.token), 422, "validation_failed",
            "combination check precedes token resolution (A15)")
    err_is_(ctx.api.get("/statement?snapshot=snap_nope", token=ada.token),
            404, "not_found", "unknown token")
    err_is_(ctx.api.get(f"/statement?snapshot={token}", token=bob.token),
            404, "not_found", "another user's token")
    r = ctx.api.get(f"/statement?snapshot={token}&foo=bar", token=ada.token)
    eq(r.status, 200, "unrecognized query parameters ignored (R267)")
    ctx.reset(hist_fixture())
    err_is_(ctx.api.get(f"/statement?snapshot={token}", token=ada.token),
            404, "not_found", "token from before reset")


@test("correction_moves_payment_across_window", "R268 R290")
def correction_moves_payment_across_window(ctx):
    ada, bob, cyd = hist_users(ctx)
    st = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p1", "p2", "p3"], "window")
    token = st["snapshot"]
    r = ada.correct("p3", {"expected_revision": 1, "amount": 100,
                           "effective_at": S(1000), "reason": "moved out"}, key="mw1")
    eq(r.status, 201, "move p3 out of the window (and shrink it)")
    st2 = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st2["entries"]], ["p1", "p2"],
       "p3 left the fresh window")
    eq(st2["closing_balance"], 8800, "closing stable: the movement left too")
    frozen = ada.statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]], ["p1", "p2", "p3"],
       "existing snapshot unchanged (R290)")
    r = ada.correct("p3", {"expected_revision": 2, "amount": 100,
                           "effective_at": S(3000), "reason": "moved back"},
                    key="mw2")
    eq(r.status, 201, "move p3 back into the window")
    st3 = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st3["entries"]], ["p1", "p2", "p3"],
       "p3 is back in the fresh window")
    frozen = ada.statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]], ["p1", "p2", "p3"],
       "snapshot still unchanged after competing corrections")


# ------------------------------------------------------------- concurrency

@test("concurrent_corrections_same_expected_revision", "R269 R231 R2")
def concurrent_corrections_same_expected_revision(ctx):
    ada, bob, cyd = hist_users(ctx)
    bodies = [{"expected_revision": 1, "amount": 1000 + i,
               "effective_at": S(5000), "reason": f"race {i}"} for i in range(12)]

    def hit(i):
        return ada.correct("p1", bodies[i], key=f"race-{i}")

    results, errs = parallel(12, hit, pool=12)
    expect(not errs, f"request errors: {errs[:2]}")
    codes = sorted(r.status for r in results)
    eq(codes.count(201), 1, "exactly one concurrent correction succeeds")
    eq(codes.count(409), 11, "the rest lose with 409")
    expect(all(r.err_code() == "stale_revision" for r in results if r.status == 409),
           "409 code is stale_revision")
    eq(len(ada.revisions("p1").json["revisions"]), 2, "one revision appended")
    eq(sum(u.me()["total"] for u in (ada, bob, cyd)), 15000,
       "conservation after the race (R240)")
    expect(all(u.me()["total"] >= 0 for u in (ada, bob, cyd)),
           "no negative balance (R2)")


@test("concurrent_same_key_correction_replay", "R3 R61 R232")
def concurrent_same_key_correction_replay(ctx):
    ada, bob, cyd = hist_users(ctx)
    body = dict(CORR_BODY)

    def hit(i):
        return ada.correct("p1", body, key="same-key")

    results, errs = parallel(20, hit, pool=20)
    expect(not errs, f"request errors: {errs[:2]}")
    created = [r for r in results if r.status == 201]
    replays = [r for r in results if r.status == 200]
    eq(len(created), 1, "exactly one 201")
    eq(len(replays), 19, "the rest are 200 replays")
    eq(len({json.dumps(r.json, sort_keys=True) for r in created + replays}), 1,
       "identical bodies everywhere")
    eq(bob.me()["total"], 6100, "money moved exactly once (6400-300)")
    rp = ada.correct("p1", body, key="same-key")
    eq(rp.status, 200, "later replay still 200")
    eq(rp.json["revision"], 2, "original revision returned")


# ------------------------------------------------------------- J. settlements

@test("settlement_member_correction_rejected", "R272 R271 R270 R99")
def settlement_member_correction_rejected(ctx):
    ada, bob, cyd = hist_users(ctx)
    r = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 400}],
                   key="set1")
    eq(r.status, 201, f"settlement: {r}")
    member = r.json["payments"][0]
    committed = r.json["committed_at"]
    eq(member["settlement_id"], r.json["settlement_id"], "membership link")
    err_is_(ada.correct(member["payment_id"],
                        {"expected_revision": 1, "amount": 100,
                         "effective_at": S(60), "reason": "nope"}, key="sc1"),
            422, "linked_payment_immutable", "settlement member immutable")
    revs = ada.revisions(member["payment_id"]).json["revisions"]
    eq(len(revs), 1, "single original revision")
    eq(revs[0]["effective_at"], committed, "rev1 effective_at == committed_at")
    eq(revs[0]["recorded_at"], committed, "rev1 recorded_at == committed_at")
    st = cyd.statement()
    got = [e for e in st["entries"] if e["payment"]["payment_id"] == member["payment_id"]]
    eq(len(got), 1, "member in the receiver's statement (ordinary payment, R270)")
    eq(got[0]["payment"]["settlement_id"], r.json["settlement_id"],
       "settlement_id carried on the statement entry")


# ------------------------------------------------------------- import/export

def downgrade(export, version, strip_auths=False):
    st = json.loads(json.dumps(export))
    st["format_version"] = version
    state = st.get("state", {})
    if strip_auths:
        state.pop("authorizations", None)
        state.pop("authorization_order", None)
        state.pop("authorization_ttl_seconds", None)
        for pay in state.get("payments", []) or []:
            pay.pop("authorization_id", None)
    for pay in state.get("payments", []) or []:
        pay.pop("revisions", None)
    for u in state.get("users", []) or []:
        u.pop("base_balance", None)
    return st


@test("capture_immutable_and_import_versions", "R273 R274 R275 A20")
def capture_immutable_and_import_versions(ctx):
    ada, bob, cyd = hist_users(ctx)
    auth = ada.authorize("bob", 500, key="au1")
    eq(auth.status, 201, "authorize")
    aid = auth.json["authorization_id"]
    cap = bob.capture(aid, body={"amount": 500, "final": True}, key="ca1")
    eq(cap.status, 201, f"final capture: {cap}")
    cap_pid = cap.json["payment_id"]
    err_is_(ada.correct(cap_pid,
                        {"expected_revision": 1, "amount": 100,
                         "effective_at": cap.json["created_at"], "reason": "no"},
                        key="cc1"),
            422, "linked_payment_immutable", "captures are immutable (R275)")
    ex = ctx.export()
    eq(ex["format_version"], 3, "stage-3 exports format_version 3 (A20)")
    st3 = ex["state"]
    expect(all("base_balance" in u for u in st3["users"].values()),
           "users carry base_balance")
    expect(all("revisions" in p for p in st3["payments"]),
           "payments carry revisions")
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    ctx.import_state(ex)
    ada2 = User_(ctx.api, "ada").login()
    eq(ada2.me()["total"], 8100, "correction history preserved through v3 import")
    eq(len(ada2.revisions(cap_pid).json["revisions"]), 1, "capture revisions kept")
    err_is_(ada2.correct(cap_pid,
                         {"expected_revision": 1, "amount": 100,
                          "effective_at": cap.json["created_at"], "reason": "no"},
                         key="cc2"),
            422, "linked_payment_immutable", "capture still immutable after import")
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    ctx.import_state(downgrade(ex, 1, strip_auths=True))
    ada1 = User_(ctx.api, "ada").login()
    eq(ada1.me()["total"], 8100, "v1-shaped export imports, balances re-derived")
    eq(ada1.list_auths(), [], "v1 carries no holds")
    eq(ada1.statement()["closing_balance"], 8100, "statement works after v1 import")
    eq(ada1.pay("bob", 50, key="post-v1").status, 201, "usable after v1 import")
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    ctx.import_state(downgrade(ex, 2))
    ada2b = User_(ctx.api, "ada").login()
    eq(ada2b.me()["total"], 8100, "v2-shaped export imports with holds accounted")
    eq(ada2b.me()["held"], 0, "final capture holds nothing")
    auths = ada2b.list_auths()
    eq([a["status"] for a in auths], ["captured"], "authorizations imported (R274)")
    cap_feed = [p for p in ada2b.activity() if p.get("authorization_id") == aid]
    eq([p["payment_id"] for p in cap_feed], [cap_pid], "capture payment keeps its link")
    err_is_(ada2b.correct(cap_pid,
                          {"expected_revision": 1, "amount": 100,
                           "effective_at": cap.json["created_at"], "reason": "no"},
                          key="cc3"),
            422, "linked_payment_immutable", "imported capture immutable (R275)")


@test("import_real_stage2_export", "R273 R274")
def import_real_stage2_export(ctx):
    if ctx.stage2 is None:
        raise core.Skip("no --stage2-url given")
    ex = ctx.export_from("stage2")
    eq(ex["format_version"], 2, "stage-2 service exports v2")
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    ctx.import_state(ex)
    state_users = {u["handle"]: u["balance"] for u in ex["state"]["users"]}
    ada = User_(ctx.api, "ada").login()
    eq(ada.me()["total"], state_users["ada"], "balances imported unchanged")
    statement_invariants(ada.statement("limit=50"), ada.user_id,
                         "statement after real stage-2 import")
    eq(ada.pay("bob", 50, key="post-s2").status, 201, "usable after import")


# ------------------------------------------------------------- K. historical holds

@test("me_historical_holds_lifecycle", "R276 R277 R278 R280 R281 R282 R279")
def me_historical_holds_lifecycle(ctx):
    ctx.reset(fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)]))
    ada = User_(ctx.api, "ada").login()
    bob = User_(ctx.api, "bob").login()
    auth = ada.authorize("bob", 500, key="h-a")
    eq(auth.status, 201, "authorize")
    aid = auth.json["authorization_id"]
    c_auth = auth.json["created_at"]
    eq(auth.json.get("closed_at", "missing"), None, "closed_at null while open (R282)")
    eq(ada.me(f"as_of={c_auth}")["held"], 500, "hold starts at creation (inclusive)")
    eq(ada.me(f"as_of={iso(parse_ts(c_auth) - timedelta(seconds=1))}")["held"], 0,
       "just before creation nothing is held (R277)")
    cap1 = bob.capture(aid, body={"amount": 200, "final": False}, key="h-c1")
    eq(cap1.status, 201, "nonfinal capture")
    c2 = cap1.json["created_at"]
    eq(ada.me(f"as_of={c2}")["held"], 300, "capture reduces the hold at capture time")
    eq(ada.me(f"as_of={iso(parse_ts(c2) - timedelta(seconds=1))}")["held"], 500,
       "just before the capture the full hold remains")
    eq(ada.me(f"as_of={c_auth}&known_at={iso(parse_ts(c2) - timedelta(seconds=1))}")["held"],
       500, "capture not yet known -> hold unreduced (R279)")
    eq(ada.me(f"as_of={c_auth}&known_at={c2}")["held"], 300,
       "known capture reduces the historical hold")
    eq(ada.me(f"as_of={iso(parse_ts(c_auth) - timedelta(seconds=1))}")["held"], 0,
       "before the authorization existed: nothing held")
    eq(ada.me(f"as_of={iso(parse_ts(c_auth) - timedelta(seconds=1))}"
              f"&known_at={c2}")["held"], 0,
       "authorization unknown at known_at -> contributes nothing (R279)")
    cap2 = bob.capture(aid, body={"amount": 300, "final": True}, key="h-c2")
    eq(cap2.status, 201, "final capture")
    c3 = cap2.json["created_at"]
    m = ada.me(f"as_of={c3}")
    eq(m["held"], 0, "final capture releases the remainder at the event time")
    eq(m["balance"], m["total"], "balance == total (R276)")
    eq(m["available"], m["total"] - m["held"], "available == total - held (R276)")
    a = [x for x in ada.list_auths() if x["authorization_id"] == aid][0]
    eq(a["closed_at"], c3, "closed_at == closing event time (R282)")
    auth2 = ada.authorize("bob", 100, key="h-b")
    aid2 = auth2.json["authorization_id"]
    eq(ada.void(aid2).status, 200, "void")
    v_time = [x for x in ada.list_auths()
              if x["authorization_id"] == aid2][0]["closed_at"]
    expect(v_time is not None, "voided auth reports closed_at")
    eq(ada.me(f"as_of={v_time}")["held"], 0, "void releases at the void time")
    # clock expiry at expires_at (R278)
    ctx.reset(fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)],
                      ttl=2))
    ada = User_(ctx.api, "ada").login()
    e1 = ada.authorize("bob", 150, key="h-e")
    eq(e1.status, 201, "short-ttl authorize")
    exp = e1.json["expires_at"]
    eq(ada.me(f"as_of={iso(parse_ts(exp) - timedelta(seconds=1))}")["held"], 150,
       "still held just before expiry")
    m = ada.me(f"as_of={exp}")
    eq(m["held"], 0, "expiry releases at expires_at exactly (R278)")
    eq(m["available"], m["total"], "available restored")
    a = ada.list_auths()[0]
    eq(a["closed_at"], exp, "expired auth closed at its deadline")
    # beyond now: an open hold expires at its deadline (R280); request start w/o as_of
    ctx.reset(fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)]))
    ada = User_(ctx.api, "ada").login()
    auth3 = ada.authorize("bob", 700, key="h-f")
    aid3 = auth3.json["authorization_id"]
    c3k = auth3.json["created_at"]
    plus3h = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    plus30s = iso(datetime.now(timezone.utc) + timedelta(seconds=30))
    eq(ada.me(f"as_of={plus3h}")["held"], 0,
       "future as_of past the deadline: expired (R280)")
    eq(ada.me(f"as_of={plus30s}")["held"], 700,
       "future as_of before the deadline: still open")
    eq(ada.me()["held"], 700, "no as_of: request-start instant (R281)")
    kn = iso(parse_ts(c3k) + timedelta(seconds=1))
    eq(ada.me(f"as_of={plus3h}&known_at={kn}")["held"], 0,
       "creation known -> deadline known: expired at as_of (R279)")
    eq(ada.me(f"as_of={plus30s}&known_at={kn}")["held"], 700,
       "known creation, as_of before deadline: open")
    eq(ada.me(f"as_of={plus30s}&known_at={iso(parse_ts(c3k) - timedelta(seconds=1))}")["held"],
       0, "creation unknown at known_at: contributes nothing")


@test("seeded_holds_assumed_creation", "R286 R287")
def seeded_holds_assumed_creation(ctx):
    pre = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    post = iso(datetime.now(timezone.utc) + timedelta(seconds=60))
    open_auth = {"id": "a_open", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 400, "note": "", "visibility": "public",
                 "status": "open",
                 "expires_at": iso(datetime.now(timezone.utc)
                                   + timedelta(hours=2))}
    ctx.reset(fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)],
                      authorizations=[open_auth]))
    ada = User_(ctx.api, "ada").login()
    eq(ada.me(f"as_of={pre}")["held"], 0,
       "no created_at -> assumed created at reset: nothing held before it (R286)")
    eq(ada.me(f"as_of={post}")["held"], 400, "from reset time on the hold exists")
    ctx.reset(fixture(
        [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0),
         fx_user("u_cyd", "cyd", 1000)],
        authorizations=[
            fx_auth("a_timed", "u_ada", "u_bob", 300, "open", 7200,
                    created_at=S(3600)),
            fx_auth("a_closed", "u_cyd", "u_bob", 250, "captured", 7200,
                    created_at=S(3600))]))
    ada = User_(ctx.api, "ada").login()
    eq(ada.me(f"as_of={S(3500)}")["held"], 300,
       "supplied created_at: the open hold starts then (R286)")
    m = ada.me()
    eq(m["held"], 300, "closed seeded hold not held at now (R287)")
    closed = [a for a in ada.list_auths() if a["authorization_id"] == "a_closed"]
    eq(closed, [], "someone else's seeded closed hold is not ada's")
    bob = User_(ctx.api, "bob").login()
    eq(bob.me()["held"], 0, "receiver of holds is not held")


@test("statement_only_money_movements", "R288 R289")
def statement_only_money_movements(ctx):
    ctx.reset(fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)]))
    ada = User_(ctx.api, "ada").login()
    bob = User_(ctx.api, "bob").login()
    n0 = len(ada.statement()["entries"])
    auth = ada.authorize("bob", 500, key="s-a")
    aid = auth.json["authorization_id"]
    eq(len(ada.statement()["entries"]), n0,
       "authorization alone is not a statement entry (R288)")
    cap = bob.capture(aid, body={"amount": 200, "final": False}, key="s-c")
    eq(cap.status, 201, "nonfinal capture")
    eq(ada.void(aid).status, 200, "void the remainder")
    eq(len(ada.statement()["entries"]), n0 + 1,
       "capture is the only new entry; release/void are not payments")
    entries = [e for e in ada.statement()["entries"]
               if e["payment"].get("authorization_id") == aid]
    eq(len(entries), 1, "capture appears exactly once (R289)")
    eq(entries[0]["delta"], 200, "capture delta")
    eq(bob.statement()["entries"][-1]["payment"]["authorization_id"], aid,
       "linked on the receiver's side too")
    eq(ada.me()["held"], 0, "void released the remaining hold")


@test("correction_vs_holds_overdraft_precedence", "R284 R285 R236")
def correction_vs_holds_overdraft_precedence(ctx):
    ctx.reset(fixture(
        [fx_user("u_payer", "payer", 1500), fx_user("u_rich", "rich", 1500),
         fx_user("u_mer", "merchant", 0)],
        payments=[fx_pay("g1", "u_rich", "u_payer", 600, created_at=S(5000)),
                  fx_pay("g2", "u_rich", "u_payer", 900, created_at=S(3000))],
        authorizations=[fx_auth("g_hold", "u_payer", "u_mer", 500, "open",
                                7200, created_at=S(4000))]))
    payer = User_(ctx.api, "payer").login()
    rich = User_(ctx.api, "rich").login()
    eq(payer.me()["held"], 500, "seeded hold active")
    eq(payer.me()["available"], 1000, "1500 - 500")
    err_is_(rich.correct("g1", {"expected_revision": 1, "amount": 200,
                              "effective_at": S(5000), "reason": "reduce"},
                       key="p1"),
            409, "historical_overdraft",
            "at the hold boundary available would be -300 (R284)")
    eq(payer.me()["available"], 1000, "state preserved")
    ctx.reset(fixture(
        [fx_user("u_payer", "payer", 600), fx_user("u_rich", "rich", 2400),
         fx_user("u_mer", "merchant", 0)],
        payments=[fx_pay("g1", "u_rich", "u_payer", 600, created_at=S(5000))],
        authorizations=[fx_auth("g_hold", "u_payer", "u_mer", 500, "open",
                                7200, created_at=S(4000))]))
    payer = User_(ctx.api, "payer").login()
    rich = User_(ctx.api, "rich").login()
    err_is_(rich.correct("g1", {"expected_revision": 1, "amount": 200,
                              "effective_at": S(5000), "reason": "reduce"},
                       key="p2"),
            409, "insufficient_funds",
            "current available (100) cannot cover the 400 debit (R285)")


@test("new_accounts_open_at_zero", "R217 R216")
def new_accounts_open_at_zero(ctx):
    hist_users(ctx)
    zed = core.User(ctx.api, f"zed{int(datetime.now().timestamp()) % 100000}").signup()
    eq(zed.me()["total"], 0, "new account opens at zero (R217)")
    st = zed.statement()
    eq(st["opening_balance"], 0, "statement opening 0")
    eq(st["closing_balance"], 0, "statement closing 0")
    eq(st["entries"], [], "no entries")
    eq(zed.me(f"as_of={S(-3600)}")["balance"], 0, "any as_of reads zero")
    r = zed.pay("ada", 25, key="z-first")
    eq(r.status, 201, "first payment from a zero account")
    eq(zed.statement()["opening_balance"], 0, "opening still zero after activity")


@test("historical_total_follows_revisions", "R283 R276")
def historical_total_follows_revisions(ctx):
    ctx.reset(fixture(
        [fx_user("u_payer", "payer", 1500), fx_user("u_rich", "rich", 1500),
         fx_user("u_mer", "merchant", 0)],
        payments=[fx_pay("g1", "u_rich", "u_payer", 600, created_at=S(5000)),
                  fx_pay("g2", "u_rich", "u_payer", 900, created_at=S(3000))],
        authorizations=[fx_auth("g_hold", "u_payer", "u_mer", 500, "open",
                                7200, created_at=S(4000))]))
    payer = User_(ctx.api, "payer").login()
    rich = User_(ctx.api, "rich").login()
    eq(payer.me(f"as_of={S(60)}")["total"], 1500, "pre-correction total")
    eq(payer.me(f"as_of={S(60)}")["held"], 500, "hold present")
    r = rich.correct("g1", {"expected_revision": 1, "amount": 700,
                            "effective_at": S(5000), "reason": "up"}, key="t1")
    eq(r.status, 201, f"correction: {r}")
    m = payer.me(f"as_of={S(60)}")
    eq(m["total"], 1600, "latest known revisions: 600->700 (+100)")
    eq(m["balance"], m["total"], "balance == total (R276)")
    eq(m["held"], 500, "hold unchanged by the correction")
    eq(m["available"], m["total"] - m["held"], "available == total - held (R276)")
    m = payer.me(f"as_of={S(60)}&known_at={S(3600)}")
    eq(m["total"], 1500, "known_at before the correction: total 1500 (R283)")
    eq(m["held"], 500, "historical held")
    m = payer.me(f"as_of={S(4500)}&known_at={S(-60)}")
    eq(m["total"], 700, "as_of between g1 and the hold: only the revised g1")
    eq(payer.me(f"as_of={S(4500)}")["held"], 500, "hold active at that as_of")


# ------------------------------------------------------------- carried invariants

@test("storm_stage3_invariants", "R1 R2 R3 R45 R239 R240 R269")
def storm_stage3_invariants(ctx):
    ctx.reset(fixture(
        [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 10000),
         fx_user("u_cyd", "cyd", 10000), fx_user("u_dee", "dee", 10000)],
        payments=[fx_pay(f"q{i}", "u_ada", "u_bob", 100 + i,
                         created_at=S(4000 + i)) for i in range(1, 5)],
        operators=["u_ada"]))
    users = [User_(ctx.api, h).login() for h in ("ada", "bob", "cyd", "dee")]
    seed_total = 40000
    token = users[0].statement()["snapshot"]
    full_before = users[0].statement()
    statuses, five_xx, corr_attempts = [], [], []

    def note(r):
        statuses.append(r.status)
        if r.status >= 500:
            five_xx.append(repr(r)[:200])
        return r

    def op(i):
        kind = i % 10
        u = users[i % 4]
        v = users[(i + 1) % 4]
        if kind in (0, 1, 2):
            return note(u.pay(v.handle, 10 + i, key=f"storm-pay-{i}"))
        if kind == 3:
            a = note(u.authorize(v.handle, 50, key=f"storm-auth-{i}"))
            if a.status == 201:
                note(v.capture(a.json["authorization_id"], body={"amount": 20},
                               key=f"storm-cap-{i}"))
            return a
        if kind == 4:
            return note(u.me(f"as_of={S(3900)}&known_at={S(60)}"))
        if kind == 5:
            return note(u.statement("limit=3"))
        if kind == 6:
            return note(u.statement(f"snapshot={token}&limit=2&offset={i % 4}"))
        if kind == 7:
            # same-revision correction races on the seeded payments; the sender
            # is always ada. Only one attempt per payment can win (R269).
            n = i // 10
            tgt = f"q{1 + n % 4}"
            body = {"expected_revision": 1, "amount": 100 + (n % 4) * 10,
                    "effective_at": S(4000 + 1 + n % 4), "reason": f"storm {n}"}
            r = note(users[0].correct(tgt, body, key=f"storm-corr-{n}"))
            corr_attempts.append((tgt, body, f"storm-corr-{n}", r.status))
            return r
        if kind == 8:
            return note(u.me())
        return note(u.settle([{"from_handle": "dee", "to_handle": "cyd",
                               "amount": 5}], key=f"storm-set-{i}"))

    results, errs = parallel(50, op, pool=50)
    expect(not errs, f"client-side errors: {errs[:2]}")
    eq(five_xx, [], "no 5xx under 50 concurrent requests (R45)")
    # conservation now and at historical instants (R240)
    for inst in (S(5000), S(4000), S(3000), S(60)):
        tot = sum(u.me(f"as_of={inst}")["total"] for u in users)
        eq(tot, seed_total, f"conservation at as_of={inst} (R240)")
    for u in users:
        m = u.me()
        check_wallet_shape(m, "post-storm")
        expect(m["total"] >= 0, f"negative balance for {u.handle}: {m}")
        m = u.me(f"as_of={S(60)}")
        expect(m["total"] >= 0 and m["available"] >= 0,
               f"negative historical view for {u.handle}: {m}")
    check_auth_invariants([a for u in users for a in u.list_auths()], "post-storm")
    frozen = users[0].statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]],
       [e["payment"]["payment_id"] for e in full_before["entries"]],
       "pre-storm snapshot entries unchanged (R262/R268/R290)")
    eq(frozen["closing_balance"], full_before["closing_balance"],
       "pre-storm snapshot closing unchanged")
    # exactly one winner per seeded payment; replay of each winner is exact
    winners = {}
    for tgt, body, key, status in corr_attempts:
        if status == 201:
            expect(tgt not in winners, f"two winners on {tgt} (R269)")
            winners[tgt] = (body, key)
    for i in range(1, 5):
        expect(f"q{i}" in winners, f"q{i} had no successful storm correction")
        revs = users[0].revisions(f"q{i}").json["revisions"]
        eq(len(revs), 2, f"q{i}: exactly one correction appended")
        rp = users[0].correct(f"q{i}", winners[f"q{i}"][0], key=winners[f"q{i}"][1])
        eq(rp.status, 200, f"q{i} winner replay is 200 (R3)")
        eq(rp.json["revision"], 2, f"q{i} replay returns the original revision")
    eq(sum(u.me()["total"] for u in users), seed_total, "final conservation")