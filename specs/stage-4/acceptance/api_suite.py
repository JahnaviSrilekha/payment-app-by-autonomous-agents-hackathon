"""Pocketful stage-4 acceptance tests — black-box, from the spec only.

Covers R291-R334 of specs/stage-4/requirements.md (we-are-devs/pocketful
spec stage-4.md: refunds, correction-batch extensions, POST /correction-batches,
format_version 4) plus the carried stage-1/2/3 suite (R193-R290 and the
carried stage-1 invariants R1, R2, R3, R45) re-run under stage-4 conditions.
Every test resets the service with its own fixture first, so order does not
matter.
"""

import json
from datetime import datetime, timedelta, timezone

import core
from core import (check_auth_invariants, check_wallet_shape, eq, expect,
                  fixture, fx_auth, fx_pay, fx_user, iso, parallel, parse_ts,
                  rfc3339_like, statement_invariants, test)
import time

S = lambda n: core.past(n)  # an instant n seconds before the suite loaded


def future(seconds=3600):
    """A fresh future instant computed at call time (SA-10): module-load-based
    'future' values go stale on long suite runs."""
    return iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


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
    expect(abs(ts_reset - datetime.now(timezone.utc)) < timedelta(seconds=300),
           "omitted created_at uses reset time")
    time.sleep(1.1)  # SA-9: the API payment must land in a later second
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
                                   created_at=future(3600))])
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
        (future(3600), 8600, "future as_of -> current balance (R251)"),
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
    p4_id = r.json["payment_id"]
    sa = ada.statement()
    eq([e["payment"]["payment_id"] for e in sa["entries"]], ["p1", "p2", "p3"],
       "other users' public payment absent from ada's statement")
    sc = cyd.statement()
    eq([e["payment"]["payment_id"] for e in sc["entries"]], [p4_id],
       "cyd sees the payment sent to them")
    expect(any(p["payment_id"] == p4_id for p in ada.activity()),
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
    err_is_(ada.correct("p1", {"expected_revision": 1}, key="k6"),
            409, "idempotency_key_reuse", "invalid body reusing a successful key "
            "on the same path (R63; key scope is per-path, R58)")


@test("corrections_body_validation", "R222 R223 R224 R225 R226 R227")
def corrections_body_validation(ctx):
    ada, bob, cyd = hist_users(ctx)
    base = dict(CORR_BODY)
    cases = []
    for field in ("expected_revision", "amount", "effective_at", "reason"):
        b = dict(base)
        b.pop(field)
        cases.append((f"missing {field}", b))
    for v in (0, -1):
        cases.append((f"expected_revision={v!r}", dict(base, expected_revision=v)))
    for v in (-1, 1000000001):
        cases.append((f"amount={v!r}", dict(base, amount=v)))
    for v in ("", "x" * 201):
        cases.append((f"reason={v!r}", dict(base, reason=v)))
    for v in (future(3600), "2026-09-24", "", "2026-09-24T13:20:00"):
        cases.append((f"effective_at={v!r}", dict(base, effective_at=v)))
    for i, (what, body) in enumerate(cases):
        err_is_(ada.correct("p1", body, key=f"bad-{i}"), 422, "validation_failed",
                what)
    # Wrong JSON *type*: adjudicated (stage-1 design §9 step 4) — every
    # correction field has an endpoint-specific rule (R223-226), so a
    # wrong-type value is always 422 validation_failed, never 400.
    for i, (what, body) in enumerate([
            ("expected_revision='1'", dict(base, expected_revision="1")),
            ("expected_revision=1.5", dict(base, expected_revision=1.5)),
            ("amount='5'", dict(base, amount="5")),
            ("amount=1.5", dict(base, amount=1.5)),
            ("reason=7", dict(base, reason=7))]):
        err_is_(ada.correct("p1", body, key=f"badtype-{i}"), 422,
                "validation_failed", what)
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
    eq(ada.correct("p1", dict(CORR_BODY, expected_revision=2, amount=1100,
                              reason="again"), key="k2").status, 201,
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
    time.sleep(1.1)  # SA-9
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
    time.sleep(1.1)  # SA-9
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
    time.sleep(1.1)  # SA-9
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
    m = ada.me(f"known_at={S(4500)}")  # S(5000) < S(4500): p1 recorded before known_at
    eq(m["balance"], 8500, "p1 known (rev1), p2/p3 not recorded yet")
    m = ada.me(f"known_at={S(3500)}")  # p1 (S(5000)) and p2 (S(4000)) known
    eq(m["balance"], 8800, "only revisions recorded at or before known_at count")
    eq(ada.me()["balance"], 8600, "omission means everything known now (R248)")
    r = ada.correct("p1", {"expected_revision": 1, "amount": 1200,
                           "effective_at": S(5000), "reason": "late fix"}, key="kn1")
    eq(r.status, 201, "correction recorded now")
    eq(ada.me(f"known_at={S(4500)}")["balance"], 8500,
       "known_at before the correction's recording still selects rev1")
    eq(ada.me(f"known_at={future(60)}")["balance"], 8900,
       "known_at after recording selects rev2 (10000-1200+300-200; decrease "
       "of a sent payment credits the sender)")
    eq(ada.me(f"known_at={future(3600)}")["balance"], 8900,
       "future known_at allowed (R251), sees rev2")
    st = ada.statement(f"known_at={S(4500)}")
    eq([(e["payment"]["payment_id"], e["payment"]["amount"], e["revision"])
        for e in st["entries"]], [("p1", 1500, 1)],
       "only payments recorded at or before known_at appear (rev1); p2's "
       "recording (S(4000)) is after known_at S(4500)")
    st = ada.statement(f"known_at={S(3500)}")
    eq([(e["payment"]["payment_id"], e["payment"]["amount"], e["revision"])
        for e in st["entries"]], [("p1", 1500, 1), ("p2", 300, 1)],
       "rev1 selected at that knowledge")
    st = ada.statement(f"known_at={future(60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p1", "p2", "p3"],
       "effective_at unchanged -> original order")
    eq(st["entries"][0]["payment"]["amount"], 1200, "selected amount (R256)")
    eq(st["entries"][0]["effective_at"], S(5000), "selected effective_at")
    rfc3339_like(st["entries"][0]["recorded_at"], "selected recorded_at")
    r = ada.correct("p1", {"expected_revision": 2, "amount": 1200,
                           "effective_at": S(3500), "reason": "moved"}, key="kn2")
    eq(r.status, 201, "correction moving effective_at")
    st = ada.statement(f"known_at={future(60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2", "p1", "p3"],
       "ordered by selected effective_at, then id (R254)")
    eq([e["balance_after"] for e in st["entries"]], [10300, 9100, 8900],
       "balance walk follows the new order")


@test("statement_known_at_window_and_combo", "R250 R251 R255 R258")
def statement_known_at_window_and_combo(ctx):
    ada, bob, cyd = hist_users(ctx)
    eq(ada.correct("p1", CORR_BODY, key="w1").status, 201, "correction")
    st = ada.statement(f"from={S(4000)}&to={S(3000)}&known_at={future(60)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2"],
       "half-open window retained under known_at")
    eq(st["opening_balance"], 8800,
       "opening uses selected revisions, strict before from")
    for f in ("revision", "effective_at", "recorded_at"):
        expect(f in st["entries"][0], f"entry carries {f} (R255)")
    m = ada.me(f"as_of={S(5000)}&known_at={future(60)}")
    eq(m["balance"], 8800, "as_of inclusive under known_at (R250)")
    m = ada.me(f"as_of={future(3600)}&known_at={future(3600)}")
    eq(m["balance"], 8900, "future as_of and known_at both allowed (R251); "
       "decrease of the sent p1 credits ada")
    st = ada.statement(f"known_at={future(60)}")
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
    eq(ada.me()["total"], 8667, "live view moved (8600+400-333)")
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
    eq(ctx.api.get(f"/statement?snapshot={token}", token=ada.token).status, 401,
       "the pre-reset bearer token is dead (reset clears credentials); auth "
       "precedes resource lookup (stage-1 §7)")
    ada2 = User_(ctx.api, "ada").login()
    err_is_(ctx.api.get(f"/statement?snapshot={token}", token=ada2.token),
            404, "not_found", "token from before reset, authenticated caller")


@test("correction_moves_payment_across_window", "R268 R290")
def correction_moves_payment_across_window(ctx):
    ada, bob, cyd = hist_users(ctx)
    st = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st["entries"]], ["p2", "p3"],
       "window: p1 (S(5000)) is before from=S(4500), excluded")
    token = st["snapshot"]
    r = ada.correct("p3", {"expected_revision": 1, "amount": 100,
                           "effective_at": S(1000), "reason": "moved out"}, key="mw1")
    eq(r.status, 201, "move p3 out of the window (and shrink it)")
    time.sleep(1.1)  # SA-9: same-second writes may lag selection reads
    st2 = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st2["entries"]], ["p2"],
       "p3 left the fresh window")
    eq(st2["closing_balance"], 8800, "closing: opening 8500 + p2's 300 only")
    frozen = ada.statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]], ["p2", "p3"],
       "existing snapshot unchanged (R290)")
    r = ada.correct("p3", {"expected_revision": 2, "amount": 100,
                           "effective_at": S(3000), "reason": "moved back"},
                    key="mw2")
    eq(r.status, 201, "move p3 back into the window")
    time.sleep(1.1)  # SA-9
    st3 = ada.statement(f"from={S(4500)}&to={S(2500)}")
    eq([e["payment"]["payment_id"] for e in st3["entries"]], ["p2", "p3"],
       "p3 is back in the fresh window")
    frozen = ada.statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]], ["p2", "p3"],
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
    """Downgrade an export to look like an older format_version (A20/A28):
    v1 also loses authorizations and revisions; v2 keeps holds; v3 keeps
    revisions. Stage-4's two new stored fields (refund_of, correction_batch_id)
    are stripped so v1-3 payloads exercise the import defaulting path."""
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
        pay.pop("refund_of", None)
    users = state.get("users") or {}
    for u in (users.values() if isinstance(users, dict) else users):
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
    eq(ex["format_version"], 4, "stage-4 exports format_version 4 (A28)")
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
    time.sleep(1.1)  # SA-9: keep capture and creation events in distinct seconds
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
    eq(ada.me(f"as_of={c_auth}&known_at={c2}")["held"], 500,
       "capture known but as_of still before the event: the reduction happens "
       "at capture time (R277); known_at only gates knowledge (R279)")
    eq(ada.me(f"as_of={c2}&known_at={c_auth}")["held"], 500,
       "regression probe: the capture already happened (as_of past the event) "
       "but is not yet known at known_at=c_auth -> must not count (R279)")
    eq(ada.me(f"as_of={iso(parse_ts(c_auth) - timedelta(seconds=1))}")["held"], 0,
       "before the authorization existed: nothing held")
    eq(ada.me(f"as_of={iso(parse_ts(c_auth) - timedelta(seconds=1))}"
              f"&known_at={c2}")["held"], 0,
       "authorization unknown at known_at -> contributes nothing (R279)")
    cap2 = bob.capture(aid, body={"amount": 300, "final": True}, key="h-c2")
    eq(cap2.status, 201, "final capture")
    c3 = cap2.json["created_at"]
    time.sleep(1.1)  # SA-9
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
    aid_e = e1.json["authorization_id"]
    live = [x for x in ada.list_auths() if x["authorization_id"] == aid_e][0]
    eq(live["status"], "open", "live /authorizations has no as_of: with real "
       "time still inside the ttl it reads open (R281 distinction)")
    eq(live["closed_at"], None, "closed_at null while open, live view")
    eq(ada.me(f"as_of={iso(parse_ts(exp) - timedelta(seconds=1))}")["held"], 150,
       "still held just before expiry (simulated as_of)")
    m = ada.me(f"as_of={exp}")
    eq(m["held"], 0, "expiry releases at expires_at exactly (R278)")
    eq(m["available"], m["total"], "available restored")
    time.sleep(2.2)  # SA-9 family: let real time pass the ttl before the live read
    a = [x for x in ada.list_auths() if x["authorization_id"] == aid_e][0]
    eq(a["status"], "expired", "lazy expiry shows on the live endpoint once "
       "real time passes the deadline")
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
    time.sleep(1.1)  # SA-9
    eq(len(ada.statement()["entries"]), n0 + 1,
       "capture is the only new entry; release/void are not payments")
    entries = [e for e in ada.statement()["entries"]
               if e["payment"].get("authorization_id") == aid]
    eq(len(entries), 1, "capture appears exactly once (R289)")
    eq(entries[0]["delta"], -200, "capture delta (ada paid)")
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
    eq(zed.me(f"as_of={future(3600)}")["balance"], 0, "any as_of reads zero")
    ada = User_(ctx.api, "ada").login()
    r = ada.pay(zed.handle, 25, key="z-first")
    eq(r.status, 201, "first payment into the zero account")
    time.sleep(1.1)  # SA-9
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
    time.sleep(1.1)  # SA-9
    m = payer.me(f"as_of={S(60)}")
    eq(m["total"], 1600, "latest known revisions: 600->700 (+100)")
    eq(m["balance"], m["total"], "balance == total (R276)")
    eq(m["held"], 500, "hold unchanged by the correction")
    eq(m["available"], m["total"] - m["held"], "available == total - held (R276)")
    m = payer.me(f"as_of={S(60)}&known_at={S(1500)}")
    eq(m["total"], 1500, "known_at after both original recordings but before the "
       "correction's: rev1 total 1500 (R283)")
    eq(m["held"], 500, "historical held")
    m = payer.me(f"as_of={S(4500)}&known_at={future(60)}")
    eq(m["total"], 700, "as_of between g1 and the hold: only the revised g1")
    eq(payer.me(f"as_of={S(3900)}")["held"], 500, "hold active just after creation")


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
        status = r.status if hasattr(r, "status") else 200
        statuses.append(status)
        if status >= 500:
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
            return note(u.api.get(f"/me?as_of={S(3900)}&known_at={S(60)}",
                                  token=u.token))
        if kind == 5:
            return note(u.api.get("/statement?limit=3", token=u.token))
        if kind == 6:
            return note(u.api.get(f"/statement?snapshot={token}&limit=2&offset={i % 4}",
                                  token=u.token))
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
            return note(u.api.get("/me", token=u.token))
        return note(u.settle([{"from_handle": "dee", "to_handle": "cyd",
                               "amount": 5}], key=f"storm-set-{i}"))

    results, errs = parallel(50, op, pool=50)
    expect(not errs, f"client-side errors: {errs[:2]}")
    eq(five_xx, [], "no 5xx under 50 concurrent requests (R45)")
    time.sleep(1.2)  # SA-9
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


# ================================================================ stage 4
# Refunds (R291-R305), corrections extensions (R306-R309), correction
# batches (R310-R331), closing rules (R332-R334). Derived only from
# we-are-devs/pocketful spec stage-4.md and specs/stage-4/requirements.md.

def s4_fixture():
    """ada 8300 / bob 3500 / cyd 700 after history; openings 10000/2000/500
    (conservation total 12500)."""
    return fixture(
        [fx_user("u_ada", "ada", 8300), fx_user("u_bob", "bob", 3500),
         fx_user("u_cyd", "cyd", 700)],
        operators=["u_ada"],
        payments=[
            fx_pay("p1", "u_ada", "u_bob", 2000, created_at=S(4000),
                   note="lunch", visibility="public"),
            fx_pay("p2", "u_bob", "u_cyd", 500, created_at=S(3000),
                   note="cinema", visibility="private"),
            fx_pay("p3", "u_cyd", "u_ada", 300, created_at=S(2000)),
        ])


def s4_users(ctx):
    ctx.reset(s4_fixture())
    return [User_(ctx.api, h).login() for h in ("ada", "bob", "cyd")]


def stored_payment(ctx, pid):
    ex = ctx.export()
    for p in ex["state"]["payments"]:
        if p["id"] == pid or p.get("payment_id") == pid:
            return p
    return None


# ------------------------------------------------------------- refunds

@test("refund_requires_idempotency_key", "R294 R293")
def refund_requires_idempotency_key(ctx):
    ada, bob, cyd = s4_users(ctx)
    r = bob.api.post("/payments/p1/refunds", body={"amount": 100}, token=bob.token)
    err_is_(r, 400, "missing_idempotency_key", "no key at all")
    r = bob.api.post("/payments/p1/refunds", body={"amount": 100},
                     token=bob.token, key="")
    err_is_(r, 400, "missing_idempotency_key", "empty key")
    r = bob.api.post("/payments/p1/refunds", body={"amount": -1}, token=bob.token)
    err_is_(r, 400, "missing_idempotency_key", "key check precedes body validation")
    eq(bob.me()["total"], 3500, "nothing moved")
    eq(len(ctx.export()["state"]["payments"]), 3, "no payment appended")


@test("refund_auth_and_lookup_errors", "R295 R296 A21")
def refund_auth_and_lookup_errors(ctx):
    ada, bob, cyd = s4_users(ctx)
    err_is_(bob.refund("p_ghost", {"amount": 100}, key="k1"), 404, "not_found",
            "unknown payment")
    err_is_(ada.refund("p1", {"amount": 100}, key="k2"), 403, "forbidden",
            "payer is not the receiver (R295)")
    err_is_(cyd.refund("p1", {"amount": 100}, key="k3"), 403, "forbidden",
            "third party is not the receiver")
    # A21: lookup precedes ownership, ownership precedes field shape
    err_is_(ada.refund("p_ghost", {"amount": 100}, key="k4"), 404, "not_found",
            "unknown payment beats the ownership check")
    err_is_(ada.refund("p1", {"amount": 0}, key="k5"), 403, "forbidden",
            "ownership check precedes field validation")
    eq(ctx.api.post("/payments/p1/refunds", body={"amount": 100}, key="k6").status,
       401, "no token -> 401")


@test("refund_target_kinds", "R297 R298 A23")
def refund_target_kinds(ctx):
    ada, bob, cyd = s4_users(ctx)
    # direct payment (p1): receiver bob refunds
    eq(bob.refund("p1", {"amount": 100}, key="t1").status, 201, "direct payment")
    # request payment: bob requests from ada, ada pays
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 150},
                      token=bob.token, key="rq1")
    eq(rq.status, 201, f"request created: {rq}")
    rid = rq.json["request_id"]
    pay = ada.api.post(f"/requests/{rid}/pay", body={}, token=ada.token, key="pay1")
    eq(pay.status, 201, f"request paid: {pay}")
    rp = pay.json
    if "payment_id" not in rp and isinstance(rp.get("payment"), dict):
        rp = rp["payment"]
    pid = rp["payment_id"]
    eq(rp.get("request_id"), rid, "request link on the payment")
    eq(bob.refund(pid, {"amount": 150}, key="t2").status, 201, "request payment")
    # capture: ada authorizes to bob, bob captures
    auth = ada.authorize("bob", 400, key="au1")
    eq(auth.status, 201, "authorize")
    aid = auth.json["authorization_id"]
    cap = bob.capture(aid, body={"amount": 400, "final": True}, key="cap1")
    eq(cap.status, 201, f"capture: {cap}")
    cpid = cap.json["payment_id"]
    eq(bob.refund(cpid, {"amount": 400}, key="t3").status, 201, "capture")
    # refund of a refund: only the refund's receiver (ada) may even try
    r1 = bob.refund("p1", {"amount": 50}, key="t4")
    eq(r1.status, 201, "seed a refund to re-refund")
    r1id = r1.json["payment_id"]
    err_is_(bob.refund(r1id, {"amount": 10}, key="t5"), 403, "forbidden",
            "A21: ownership precedes the refund-of-refund check")
    err_is_(ada.refund(r1id, {"amount": 10}, key="t6"), 422,
            "invalid_refund_target", "refunds of refunds rejected (R298)")
    ex = ctx.export()
    eq(len([p for p in ex["state"]["payments"] if p.get("refund_of") == r1id]), 0,
       "no state change: the rejected refund created nothing")


@test("refund_amount_validation", "R299")
def refund_amount_validation(ctx):
    ada, bob, cyd = s4_users(ctx)
    cases = [("missing amount", {}), ("zero", {"amount": 0}),
             ("negative", {"amount": -1}), ("float", {"amount": 1.5}),
             ("string", {"amount": "200"}), ("null", {"amount": None}),
             ("above range", {"amount": 1000000001})]
    for i, (what, body) in enumerate(cases):
        err_is_(bob.refund("p1", body, key=f"bad-{i}"), 422, "validation_failed",
                f"{what} -> validation_failed")
    # 1_000_000_000 passes the range rule, so the error is the next check (A21)
    err_is_(bob.refund("p1", {"amount": 1000000000}, key="bound"),
            422, "refund_exceeds_payment",
            "amount 1e9 passes the shape check (R299 boundary)")


@test("refund_cumulative_limit", "R300 A22")
def refund_cumulative_limit(ctx):
    ada, bob, cyd = s4_users(ctx)
    eq(bob.refund("p1", {"amount": 800}, key="r1").status, 201, "partial 1")
    eq(bob.refund("p1", {"amount": 700}, key="r2").status, 201, "partial 2")
    err_is_(bob.refund("p1", {"amount": 501}, key="r3"), 422,
            "refund_exceeds_payment", "800+700+501 > 2000 (R300)")
    eq(bob.me()["total"], 2000, "failed refund moved nothing")
    eq(bob.refund("p1", {"amount": 500}, key="r4").status, 201, "exactly the rest")
    err_is_(bob.refund("p1", {"amount": 1}, key="r5"), 422,
            "refund_exceeds_payment", "cumulative cap reached")
    # correcting the target upward re-opens headroom (A22: current = last rev)
    up = ada.correct("p1", {"expected_revision": 1, "amount": 3000,
                            "effective_at": S(4000), "reason": "tip"}, key="c1")
    eq(up.status, 201, f"correction up: {up}")
    eq(bob.refund("p1", {"amount": 1000}, key="r6").status, 201,
       "headroom after upward correction")
    err_is_(bob.refund("p1", {"amount": 1}, key="r7"), 422,
            "refund_exceeds_payment", "new cap reached")
    eq(bob.me()["total"], 1500, "bob debited exactly the refund total (3000)")
    eq(ada.me()["total"], 10300, "ada credited exactly the refund total")
    eq(sum(u.me()["total"] for u in (ada, bob, cyd)), 12500, "conservation")


@test("refund_creates_opposite_payment", "R301 R305 R302")
def refund_creates_opposite_payment(ctx):
    ada, bob, cyd = s4_users(ctx)
    r = bob.refund("p1", {"amount": 500}, key="r1")
    eq(r.status, 201, f"refund: {r}")
    b = r.json
    eq(b["from_user_id"], bob.user_id, "from = original receiver")
    eq(b["to_user_id"], ada.user_id, "to = original payer (opposite direction)")
    eq(b["amount"], 500, "amount")
    eq(b["refund_of"], "p1", "refund_of names the target")
    eq(b["request_id"], None, "request_id null (R301)")
    eq(b["authorization_id"], None, "authorization_id null (R301)")
    eq(b["note"], "lunch", "note copied from the target")
    eq(b["visibility"], "public", "visibility copied from the target")
    rfc3339_like(b["created_at"], "created_at RFC 3339")
    replay = bob.refund("p1", {"amount": 500}, key="r1")
    eq(replay.status, 200, "replay is 200 (R302)")
    eq(replay.json, b, "replay returns the original body (R302)")
    # every non-refund payment exposes refund_of null (R305)
    ex = ctx.export()
    for p in ex["state"]["payments"]:
        if p["id"] in ("p1", "p2", "p3"):
            eq(p.get("refund_of"), None, f"{p['id']}: refund_of null (R305)")
    eq(stored_payment(ctx, b["payment_id"]).get("refund_of"), "p1",
       "stored refund payment keeps refund_of")


@test("refund_replay_reuse_exactly_once", "R302 R3 R293")
def refund_replay_reuse_exactly_once(ctx):
    ada, bob, cyd = s4_users(ctx)
    first = bob.refund("p1", {"amount": 500}, key="k")
    eq(first.status, 201, "first use")
    rp = bob.refund("p1", {"amount": 500}, key="k")
    eq(rp.status, 200, "replay 200")
    eq(rp.json, first.json, "replay body identical")
    err_is_(bob.refund("p1", {"amount": 600}, key="k"), 409,
            "idempotency_key_reuse", "different body, same key (R3/R61)")
    eq(bob.me()["total"], 3000, "money moved exactly once")


@test("refund_insufficient_funds_available", "R303 A21 R293")
def refund_insufficient_funds_available(ctx):
    ada, bob, cyd = s4_users(ctx)
    # bob (receiver of p1, total 3500) holds 3300: available 200
    auth = bob.authorize("cyd", 3300, key="h1")
    eq(auth.status, 201, f"hold: {auth}")
    eq(bob.me()["available"], 200, "hold reduces available (R303: not balance)")
    before = (ada.me()["total"], bob.me()["total"], bob.me()["held"])
    err_is_(bob.refund("p1", {"amount": 250}, key="r1"), 409,
            "insufficient_funds", "250 > available 200 (even though balance 3500)")
    eq((ada.me()["total"], bob.me()["total"], bob.me()["held"]), before,
       "failing refund is atomic: balances and holds unchanged (R303)")
    eq(len(ctx.export()["state"]["payments"]), 3, "no payment appended")
    # no idempotency record was stored by the failure (A21: nothing recorded)
    eq(bob.refund("p1", {"amount": 150}, key="r1").status, 201,
       "same key retries fresh after a failure")
    eq(bob.me()["total"], 3350, "150 moved")
    eq(bob.me()["available"], 50, "available tracked the refund")
    # boundary: refund exactly the available amount once the hold is released
    eq(bob.void(auth.json["authorization_id"]).status in (200, 201), True,
       "hold released")
    eq(bob.me()["available"], 3350, "hold released")
    eq(bob.refund("p1", {"amount": 1600}, key="r2").status, 201,
       "refund up to the cumulative cap (400+1600=2000)")
    eq(bob.me()["total"], 1750, "bob debited 1600")


@test("refund_never_reopens_request_or_authorization", "R304 A23")
def refund_never_reopens_request_or_authorization(ctx):
    ada, bob, cyd = s4_users(ctx)
    # paid request stays paid (R304)
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 150},
                      token=bob.token, key="rq1")
    rid = rq.json["request_id"]
    pay = ada.api.post(f"/requests/{rid}/pay", body={}, token=ada.token, key="pay1")
    eq(pay.status, 201, "request paid")
    rpid = pay.json["payment_id"]
    held_before = bob.me()["held"]
    eq(bob.refund(rpid, {"amount": 150}, key="r1").status, 201, "refund the request-payment")
    lst = bob.api.get("/requests?status=paid", token=bob.token).json.get("requests", [])
    eq(any(r["request_id"] == rid for r in lst), True,
       "request still listed as paid (R304: never reopened)")
    eq(ada.api.post(f"/requests/{rid}/pay", body={}, token=ada.token,
                    key="pay1").status, 200, "pay replay still returns the original")
    # captured authorization keeps its numbers; released holds stay released (R304)
    auth = ada.authorize("bob", 400, key="au1")
    aid = auth.json["authorization_id"]
    cap = bob.capture(aid, body={"amount": 400, "final": True}, key="cap1")
    cpid = cap.json["payment_id"]
    eq(bob.me()["held"], 0, "final capture holds nothing")
    eq(bob.refund(cpid, {"amount": 400}, key="r2").status, 201, "refund the capture")
    a = [x for x in bob.list_auths() if x.get("authorization_id", x.get("id")) == aid][0]
    eq(a["status"], "captured", "authorization still captured (R304)")
    eq(a["captured_amount"], 400, "captured_amount untouched (R304)")
    eq(bob.me()["held"], 0, "no hold restored by the refund (R304)")
    # partial capture: open authorization keeps remaining_amount, hold unchanged
    auth2 = ada.authorize("bob", 500, key="au2")
    aid2 = auth2.json["authorization_id"]
    cap2 = bob.capture(aid2, body={"amount": 150, "final": False}, key="cap2")
    eq(cap2.status, 201, "partial capture")
    held2 = bob.me()["held"]
    eq(bob.refund(cap2.json["payment_id"], {"amount": 150}, key="r3").status, 201,
       "refund the partial capture")
    a2 = [x for x in bob.list_auths() if x.get("authorization_id", x.get("id")) == aid2][0]
    eq(a2["status"], "open", "authorization still open (R304)")
    eq(a2["captured_amount"], 150, "captured_amount untouched")
    eq(bob.me()["held"], held2, "hold amount unchanged by the refund (R304)")


@test("refund_of_settlement_member", "R332 R307")
def refund_of_settlement_member(ctx):
    ada, bob, cyd = s4_users(ctx)
    s = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 400}],
                   key="set1")
    eq(s.status, 201, f"settlement: {s}")
    sid = s.json["settlement_id"]
    sp = s.json["payments"][0]["payment_id"]
    r = cyd.refund(sp, {"amount": 150}, key="r1")
    eq(r.status, 201, f"settlement member refunded under ordinary rules (R332): {r}")
    eq(r.json["refund_of"], sp, "refund_of set")
    eq(r.json.get("settlement_id"), None,
       "refund payment is not a settlement member (R332)")
    eq(stored_payment(ctx, sp).get("settlement_id"), sid,
       "original member keeps its settlement_id (R332)")
    err_is_(cyd.correct(r.json["payment_id"],
                        {"expected_revision": 1, "amount": 100,
                         "effective_at": S(60), "reason": "no"}, key="c1"),
            422, "linked_payment_immutable",
            "the refund payment is immutable (R307; cyd is its sender)")
    eq(cyd.refund(sp, {"amount": 150}, key="r2").status, 201, "second partial ok")
    err_is_(cyd.refund(sp, {"amount": 101}, key="r3"), 422,
            "refund_exceeds_payment", "ordinary refund_exceeds_payment applies (R332)")


# ------------------------------------------------------------- corrections x refunds

@test("correction_refund_linked_immutable", "R306 R307 A24")
def correction_refund_linked_immutable(ctx):
    ada, bob, cyd = s4_users(ctx)
    r1 = bob.refund("p1", {"amount": 500}, key="r1")
    eq(r1.status, 201, "refund exists")
    rpid = r1.json["payment_id"]
    err_is_(bob.correct(rpid, {"expected_revision": 1, "amount": 100,
                               "effective_at": S(4000), "reason": "no"}, key="c1"),
            422, "linked_payment_immutable",
            "refund payments cannot be corrected (bob is the refund's sender)")
    eq(len(ada.revisions(rpid).json["revisions"]), 1, "no revision appended")
    auth = ada.authorize("bob", 400, key="au1")
    cap = bob.capture(auth.json["authorization_id"],
                      body={"amount": 400, "final": True}, key="cap1")
    err_is_(ada.correct(cap.json["payment_id"],
                        {"expected_revision": 1, "amount": 100,
                         "effective_at": cap.json["created_at"], "reason": "no"},
                        key="c2"),
            422, "linked_payment_immutable", "captures still cannot be corrected")
    # ordinary payments remain correctable (R306)
    eq(ada.correct("p1", {"expected_revision": 1, "amount": 1500,
                          "effective_at": S(4000), "reason": "ok"}, key="c2").status,
       201, "direct payment still correctable (R306)")


@test("correction_below_refunded_rejected", "R308 A25 R300")
def correction_below_refunded_rejected(ctx):
    ada, bob, cyd = s4_users(ctx)
    eq(bob.refund("p1", {"amount": 800}, key="r1").status, 201, "refund 800")
    before = (ada.me()["total"], bob.me()["total"])
    err_is_(ada.correct("p1", {"expected_revision": 1, "amount": 799,
                               "effective_at": S(4000), "reason": "cut"}, key="c1"),
            422, "refund_exceeds_payment", "799 < refunded 800 (R308)")
    eq((ada.me()["total"], bob.me()["total"]), before, "nothing moved")
    eq(len(ada.revisions("p1").json["revisions"]), 1, "no revision appended")
    eq(ada.correct("p1", {"expected_revision": 1, "amount": 800,
                          "effective_at": S(4000), "reason": "floor"},
                    key="c2").status, 201, "amount == refunded total is allowed")
    err_is_(ada.correct("p1", {"expected_revision": 99, "amount": 100,
                               "effective_at": S(4000), "reason": "both wrong"},
                    key="c3"),
            422, "refund_exceeds_payment",
            "A25: refund_exceeds_payment precedes stale_revision")


@test("correction_debits_available_funds", "R309 R236 R285")
def correction_debits_available_funds(ctx):
    ada, bob, cyd = s4_users(ctx)
    # correcting p2 (bob->cyd 500) down to 100 makes cyd (the receiver who must
    # give money back) the debtor for 400 — checked against available (R309)
    auth = cyd.authorize("ada", 400, key="h1")
    eq(auth.status, 201, f"cyd holds 400: {auth}")
    eq(cyd.me()["available"], 300, "available 300")
    err_is_(bob.correct("p2", {"expected_revision": 1, "amount": 100,
                               "effective_at": S(3000), "reason": "refund part"},
                        key="c1"),
            409, "insufficient_funds", "debit 400 > available 300 (R309)")
    eq(cyd.me()["total"], 700, "balance untouched")
    eq(cyd.void(auth.json["authorization_id"]).status in (200, 201), True,
       "hold released")
    eq(bob.correct("p2", {"expected_revision": 1, "amount": 100,
                         "effective_at": S(3000), "reason": "refund part"},
                    key="c2").status, 201, "now affordable")
    eq(cyd.me()["total"], 300, "cyd debited 400")
    eq(bob.me()["total"], 3900, "bob credited 400")


# ------------------------------------------------------------- batches

@test("batch_auth_and_idempotency", "R310 R293 A26")
def batch_auth_and_idempotency(ctx):
    ada, bob, cyd = s4_users(ctx)
    item = {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
            "effective_at": S(4000), "reason": "fix"}
    err_is_(ada.cbatch([item]), 400, "missing_idempotency_key", "no key")
    eq(ctx.api.post("/correction-batches", body={"corrections": [item]},
                    key="k1").status, 401, "no token -> 401 (R310)")
    err_is_(bob.cbatch([item], key="k2"), 403, "forbidden",
            "caller is not a settlement operator (R310)")
    err_is_(bob.cbatch([{"payment_id": "p_ghost", "expected_revision": 1,
                         "amount": 10, "effective_at": S(60), "reason": "x"}],
                       key="k3"),
            404, "not_found",
            "A26: per-item validation precedes the operator check")
    settle = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100}],
                        key="set1")
    member = settle_member_id(settle)
    err_is_(bob.cbatch([{"payment_id": member, "expected_revision": 1,
                         "amount": 50, "effective_at": S(60), "reason": "x"}],
                       key="k4"),
            403, "forbidden",
            "operator check precedes settlement completeness (A26 phase 3)")
    first = ada.cbatch([item], key="k4")
    eq(first.status, 201, f"operator batch succeeds: {first}")
    err_is_(ada.cbatch([dict(item, amount=1799)], key="k4"), 409,
            "idempotency_key_reuse", "same key, different body (R3/R61)")


def settle_member_id(settle_resp):
    return settle_resp.json["payments"][0]["payment_id"]


@test("batch_shape_validation", "R312 R311 R319")
def batch_shape_validation(ctx):
    ada, bob, cyd = s4_users(ctx)
    item = {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
            "effective_at": S(4000), "reason": "fix"}
    err_is_(ctx.api.post("/correction-batches", body={}, token=ada.token,
                         key="s0"), 422, "validation_failed", "corrections absent")
    for i, (what, corrections) in enumerate([
            ("corrections is a string", "x"),
            ("corrections is an object", {}),
            ("corrections empty", []),
            ("item not an object", [42]),
            ("duplicate payment_ids", [item, dict(item)]),
            ("33 items", [dict(item, payment_id=f"q{n}") for n in range(33)]),
            ("item missing reason", [dict(item, reason=None)]),
    ]):
        err_is_(ctx.api.post("/correction-batches", body={"corrections": corrections},
                             token=ada.token, key=f"s{i}"),
                422, "validation_failed", what)
    # unknown fields ignored (R319), on the item and the body
    noisy = ctx.api.post("/correction-batches",
                         body={"corrections": [dict(item, junk=1, bogus="x")],
                               "why": "because"},
                         token=ada.token, key="s-ok")
    eq(noisy.status, 201, f"unknown fields ignored (R319): {noisy}")


@test("batch_boundary_one_and_thirty_two", "R312")
def batch_boundary_one_and_thirty_two(ctx):
    users = [fx_user("u_ada", "ada", 6800), fx_user("u_bob", "bob", 5200)]
    payments = [fx_pay(f"q{i}", "u_ada", "u_bob", 100, created_at=S(4000 + i))
                for i in range(32)]
    ctx.reset(fixture(users, operators=["u_ada"], payments=payments))
    ada = User_(ctx.api, "ada").login()
    bob = User_(ctx.api, "bob").login()
    eq(sum(u.me()["total"] for u in (ada, bob)), 12000, "seed sum")
    one = [{"payment_id": "q0", "expected_revision": 1, "amount": 100,
            "effective_at": S(3500), "reason": "noop-first"}]
    r1 = ada.cbatch(one, key="b1")
    eq(r1.status, 201, f"1-item batch (R312 lower bound): {r1}")
    full = [{"payment_id": f"q{i}", "expected_revision": 2 if i == 0 else 1,
             "amount": 100, "effective_at": S(3500), "reason": "noop"}
            for i in range(32)]
    r2 = ada.cbatch(full, key="b2")
    eq(r2.status, 201, f"32-item batch (R312 upper bound): {r2}")
    eq(len(r2.json["revisions"]), 32, "32 revisions in the response")
    over = full + [{"payment_id": "q0", "expected_revision": 2, "amount": 1,
                    "effective_at": S(3500), "reason": "33rd"}]
    err_is_(ada.cbatch(over, key="b3"), 422, "validation_failed",
            "33 items rejected even before the item's own 404/409 (R312)")


@test("batch_per_item_errors_input_order", "R313 R314 R315 R326 A26")
def batch_per_item_errors_input_order(ctx):
    ada, bob, cyd = s4_users(ctx)
    auth = ada.authorize("bob", 100, key="au1")
    cap = bob.capture(auth.json["authorization_id"],
                      body={"amount": 100, "final": True}, key="cap1")
    cp = cap.json["payment_id"]
    r1 = bob.refund("p1", {"amount": 50}, key="r1")
    expect(r1.status == 201, f"seed refund: {r1}")
    rp = r1.json["payment_id"]

    def item(pid="p2", **kw):
        base = {"payment_id": pid, "expected_revision": 1, "amount": 450,
                "effective_at": S(3000), "reason": "fix"}
        return dict(base, **kw)

    ok_p1 = {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
             "effective_at": S(4000), "reason": "fix"}
    ghost = item("p_ghost")
    stale = {"payment_id": "p1", "expected_revision": 5, "amount": 100,
             "effective_at": S(4000), "reason": "x"}
    cases = [
        ("item2 unknown -> 404 (R314)", [ok_p1, ghost], 404, "not_found"),
        ("input order: stale then unknown -> 409", [stale, ghost], 409,
         "stale_revision"),
        ("input order: unknown then stale -> 404", [ghost, stale], 404,
         "not_found"),
        ("amount -1 in item2 (R313)", [ok_p1, item(amount=-1)], 422,
         "validation_failed"),
        ("missing reason in item2 (R313)", [ok_p1, item(reason=None)], 422,
         "validation_failed"),
        ("expected_revision wrong type (R313)", [ok_p1, item(expected_revision="1")],
         422, "validation_failed"),
        ("garbage effective_at (R313)", [ok_p1, item(effective_at="not-a-time")],
         422, "validation_failed"),
        ("future effective_at rejected (R326)", [ok_p1, item(effective_at=future(3600))],
         422, "validation_failed"),
        ("capture immutable in a batch (R315)", [ok_p1, item(cp)], 422,
         "linked_payment_immutable"),
        ("refund immutable in a batch (R315)", [ok_p1, item(rp)], 422,
         "linked_payment_immutable"),
    ]
    for i, (what, items, status, code) in enumerate(cases):
        err_is_(ada.cbatch(items, key=f"pe-{i}"), status, code, what)
    # nothing was applied by any rejected batch
    eq(len(ada.revisions("p1").json["revisions"]), 1, "p1 untouched (R323)")
    eq(len(ada.revisions("p2").json["revisions"]), 1, "p2 untouched (R323)")
    # a batch item may correct a settlement member (R315), unlike the single path
    s = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100}],
                   key="set1")
    member = s.json["payments"][0]["payment_id"]
    eq(ada.cbatch([item(member, amount=80, effective_at=S(60), reason="member")],
                  key="pe-ok").status, 201,
       "settlement member correctable in a batch (R315)")
    # stale_revision is judged against the payment's current revision count
    eq(ada.correct("p1", {"expected_revision": 1, "amount": 1900,
                          "effective_at": S(4000), "reason": "solo"}, key="solo1")
       .status, 201, "p1 now at revision 2")
    err_is_(ada.cbatch([item("p1", expected_revision=1)], key="pe-stale"),
            409, "stale_revision", "revision 1 is now stale")
    eq(ada.cbatch([item("p1", expected_revision=2, amount=1850)], key="pe-fresh")
       .status, 201, "current revision accepted")


@test("batch_settlement_completeness_and_instants", "R316 R317 R315 R318")
def batch_settlement_completeness_and_instants(ctx):
    ada, bob, cyd = s4_users(ctx)
    s = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100},
                    {"from_handle": "bob", "to_handle": "cyd", "amount": 50}],
                   key="set1")
    eq(s.status, 201, f"settlement: {s}")
    m1 = s.json["payments"][0]["payment_id"]
    m2 = s.json["payments"][1]["payment_id"]
    both = [{"payment_id": m1, "expected_revision": 1, "amount": 80,
             "effective_at": S(100), "reason": "reversal"},
            {"payment_id": m2, "expected_revision": 1, "amount": 40,
             "effective_at": S(100), "reason": "reversal"}]
    err_is_(ada.cbatch([both[0]], key="inc1"), 422, "incomplete_settlement",
            "one member alone rejected (R316)")
    eq(ada.cbatch(both, key="inc2").status, 201, "both members accepted")
    # fresh settlement for the instant checks
    s2 = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 60},
                     {"from_handle": "bob", "to_handle": "cyd", "amount": 30}],
                    key="set2")
    n1 = s2.json["payments"][0]["payment_id"]
    n2 = s2.json["payments"][1]["payment_id"]
    differing = [
        {"payment_id": n1, "expected_revision": 1, "amount": 50,
         "effective_at": S(100), "reason": "x"},
        {"payment_id": n2, "expected_revision": 1, "amount": 20,
         "effective_at": S(200), "reason": "x"},
    ]
    err_is_(ada.cbatch(differing, key="inc3"), 422, "validation_failed",
            "members must share one effective instant (R317)")
    # offset spellings may differ; the instants must be identical (R317 AC)
    off = [
        {"payment_id": n1, "expected_revision": 1, "amount": 50,
         "effective_at": "2026-09-20T12:00:00Z", "reason": "z"},
        {"payment_id": n2, "expected_revision": 1, "amount": 20,
         "effective_at": "2026-09-20T14:00:00+02:00", "reason": "offset"},
    ]
    r = ada.cbatch(off, key="inc4")
    eq(r.status, 201, f"Z and +02:00 spellings of one instant accepted (R317): {r}")
    eq(parse_ts(r.json["revisions"][0]["effective_at"]),
       parse_ts(r.json["revisions"][1]["effective_at"]),
       "both revisions share the instant")
    # completeness is per settlement, detected regardless of item order (R316)
    err_is_(ada.cbatch([dict(off[0]), dict(off[1], payment_id=n1)], key="inc5"),
            422, "validation_failed", "duplicate payment ids are a shape error")
    s3 = ada.settle([{"from_handle": "cyd", "to_handle": "ada", "amount": 20}],
                    key="set3")
    m3 = s3.json["payments"][0]["payment_id"]
    mixed = [
        {"payment_id": n1, "expected_revision": 2, "amount": 45,
         "effective_at": S(100), "reason": "x"},
        {"payment_id": m3, "expected_revision": 1, "amount": 15,
         "effective_at": S(100), "reason": "x"},
    ]
    err_is_(ada.cbatch(mixed, key="inc6"), 422, "incomplete_settlement",
            "set2 incomplete even though set3 is complete (R316)")
    err_is_(ada.cbatch(list(reversed(mixed)), key="inc7"), 422,
            "incomplete_settlement", "detected regardless of input order")
    # nonmembers stay correctable both ways (R318)
    eq(ada.cbatch([{"payment_id": "p1", "expected_revision": 1, "amount": 1900,
                    "effective_at": S(4000), "reason": "nonmember"}],
                  key="inc8").status, 201, "nonmember batch correction (R318)")
    eq(ada.correct("p2", {"expected_revision": 1, "amount": 450,
                          "effective_at": S(3000), "reason": "nonmember"},
                    key="inc9").status, 201, "ordinary single correction (R318)")


@test("batch_precedence_chain", "R320 R310 A26 R316 R321")
def batch_precedence_chain(ctx):
    ada, bob, cyd = s4_users(ctx)
    item = {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
            "effective_at": S(4000), "reason": "fix"}
    # phase 1 beats phase 2: duplicates win over an unknown payment
    dup = [item, dict(item), {"payment_id": "p_ghost", "expected_revision": 1,
                              "amount": 1, "effective_at": S(60), "reason": "x"}]
    err_is_(ada.cbatch(dup, key="pc1"), 422, "validation_failed",
            "shape error precedes the unknown-payment 404")
    # phase 2 beats phase 3
    err_is_(bob.cbatch([{"payment_id": "p_ghost", "expected_revision": 1,
                         "amount": 1, "effective_at": S(60), "reason": "x"}],
                       key="pc2"), 404, "not_found",
            "item error precedes the operator 403 (A26)")
    # phase 3 beats phase 4
    s = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100}],
                   key="set1")
    member = s.json["payments"][0]["payment_id"]
    err_is_(bob.cbatch([{"payment_id": member, "expected_revision": 1,
                         "amount": 50, "effective_at": S(60), "reason": "x"}],
                       key="pc3"), 403, "forbidden",
            "operator 403 precedes incomplete_settlement (A26)")
    # phase 4 beats phase 5: incomplete AND unaffordable -> incomplete_settlement
    cyd_holds = cyd.authorize("ada", 700, key="hold")
    eq(cyd_holds.status, 201, "cyd available drops to 0")
    err_is_(ada.cbatch([{"payment_id": member, "expected_revision": 1,
                         "amount": 50, "effective_at": S(60), "reason": "x"}],
                       key="pc4"), 422, "incomplete_settlement",
            "completeness precedes affordability (R320)")
    eq(cyd.void(cyd_holds.json["authorization_id"]).status in (200, 201), True,
       "hold released")


def combo_fixture():
    return fixture(
        [fx_user("u_ada", "ada", 9650), fx_user("u_cyd", "cyd", 350)],
        operators=["u_ada"],
        payments=[fx_pay("p3", "u_cyd", "u_ada", 300, created_at=S(2000)),
                  fx_pay("p4", "u_cyd", "u_ada", 50, created_at=S(1500))])


@test("batch_combined_affordability", "R322 R303 A26")
def batch_combined_affordability(ctx):
    # cyd has 350 with a 250 hold: available 100 (R303). Each leg alone is
    # affordable, the combined net is not (R322), and vice versa via netting.
    legs = [
        {"payment_id": "p3", "expected_revision": 1, "amount": 380,
         "effective_at": S(2000), "reason": "-80 each"},
        {"payment_id": "p4", "expected_revision": 1, "amount": 130,
         "effective_at": S(1500), "reason": "-80 each"},
    ]
    # reset A: prove each leg alone is affordable
    ctx.reset(combo_fixture())
    ada = User_(ctx.api, "ada").login()
    cyd = User_(ctx.api, "cyd").login()
    eq(cyd.authorize("ada", 250, key="h").status, 201, "hold set: available 100")
    for i, leg in enumerate(legs):
        eq(ada.cbatch([leg], key=f"single-{i}").status, 201,
           f"leg {i} alone is affordable (R322)")
    # reset B: the two legs together overdraft cyd's available
    ctx.reset(combo_fixture())
    ada = User_(ctx.api, "ada").login()
    cyd = User_(ctx.api, "cyd").login()
    eq(cyd.authorize("ada", 250, key="h").status, 201, "hold set: available 100")
    err_is_(ada.cbatch(legs, key="both"), 409, "insufficient_funds",
            "combined net -160 > available 100, judged together (R322)")
    eq(cyd.me()["total"], 350, "rejected batch moved nothing (R323)")
    eq(cyd.me()["held"], 250, "hold untouched (R323)")
    # reset C: netting across legs — -120 and +20 land exactly at 0
    ctx.reset(combo_fixture())
    ada = User_(ctx.api, "ada").login()
    cyd = User_(ctx.api, "cyd").login()
    eq(cyd.authorize("ada", 250, key="h").status, 201, "hold set: available 100")
    netting = [
        {"payment_id": "p3", "expected_revision": 1, "amount": 420,
         "effective_at": S(2000), "reason": "-120 alone"},
        {"payment_id": "p4", "expected_revision": 1, "amount": 30,
         "effective_at": S(1500), "reason": "+20 back"},
    ]
    r = ada.cbatch(netting, key="net")
    eq(r.status, 201, f"combined net -100 lands exactly at available: {r}")
    eq(cyd.me()["total"], 250, "net debit applied once")
    eq(sum(u.me()["total"] for u in (ada, cyd)), 10000, "conservation")


def ho_fixture():
    return fixture(
        [fx_user("u_ada", "ada", 100), fx_user("u_carol", "carol", 200),
         fx_user("u_dave", "dave", 500), fx_user("u_erin", "erin", 800),
         fx_user("u_frank", "frank", 500)],
        operators=["u_ada"],
        payments=[fx_pay("h1", "u_dave", "u_carol", 500, created_at=S(4000)),
                  fx_pay("h2", "u_frank", "u_carol", 500, created_at=S(3900)),
                  fx_pay("h3", "u_carol", "u_erin", 800, created_at=S(3000))])


@test("batch_historical_overdraft_all_or_nothing", "R320 R321 R323 R237")
def batch_historical_overdraft_all_or_nothing(ctx):
    ctx.reset(ho_fixture())
    ada = User_(ctx.api, "ada").login()  # operator
    carol = User_(ctx.api, "carol").login()
    dave = User_(ctx.api, "dave").login()
    r1 = {"payment_id": "h1", "expected_revision": 1, "amount": 450,
          "effective_at": S(4000), "reason": "reduce a little"}
    r2 = {"payment_id": "h2", "expected_revision": 1, "amount": 350,
          "effective_at": S(3900), "reason": "reduce a little"}
    # each alone is fine; combined carol goes to -50 at h3's boundary (R320/R322)
    eq(ada.cbatch([r1], key="ho-a").status, 201,
       "item 1 alone: boundary 450+500-800 = 150")
    time.sleep(1.1)  # SA-9
    eq(ada.cbatch([r2], key="ho-b").status, 201,
       "item 2 alone: boundary 500+350-800 = 50")
    time.sleep(1.1)
    # fresh history for the combined rejection
    ctx.reset(ho_fixture())
    ada = User_(ctx.api, "ada").login()
    carol = User_(ctx.api, "carol").login()
    dave = User_(ctx.api, "dave").login()
    before = [carol.me()["total"], dave.me()["total"]]
    err_is_(ada.cbatch([dict(r1), dict(r2)], key="ho-both"), 409,
            "historical_overdraft",
            "combined boundary 450+350-800 = -50 (R320 final phase)")
    eq([carol.me()["total"], dave.me()["total"]], before,
       "all-or-nothing: balances unchanged (R323)")
    ex = ctx.export()
    eq([r_.get("correction_batch_id") for p in ex["state"]["payments"]
        for r_ in p.get("revisions", [])],
       [], "no revision carries correction_batch_id (R323)")
    eq(len(carol.revisions("h1").json["revisions"]), 1, "no revision appended (R323)")
    # the rejected key left no idempotency record: it can start fresh (R323)
    ok = ada.cbatch([dict(r1, amount=500, reason="noop")], key="ho-both")
    eq(ok.status, 201, "same key reusable after a rejected batch (R323)")
    # insufficient_funds beats historical_overdraft (R320 phase 5 vs 6)
    ctx.reset(ho_fixture())
    ada = User_(ctx.api, "ada").login()
    carol = User_(ctx.api, "carol").login()
    triple = [dict(r1), dict(r2),
              {"payment_id": "h3", "expected_revision": 1, "amount": 1700,
               "effective_at": S(3000), "reason": "fee up"}]
    err_is_(ada.cbatch(triple, key="ho-ih"), 409, "insufficient_funds",
            "combined available shortfall (-1100) precedes the historical sweep (R320)")
    eq(carol.me()["total"], 200, "nothing applied (R323)")


@test("batch_success_shape_shared_recorded_at", "R324 R325 R326 A27")
def batch_success_shape_shared_recorded_at(ctx):
    ada, bob, cyd = s4_users(ctx)
    prior = {pid: ada.revisions(pid).json["revisions"][-1]["recorded_at"]
             for pid in ("p1", "p3")}
    items = [
        {"payment_id": "p3", "expected_revision": 1, "amount": 250,
         "effective_at": S(100), "reason": "b"},
        {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
         "effective_at": S(4000), "reason": "a"},
    ]
    r = ada.cbatch(items, key="b1")
    eq(r.status, 201, f"batch: {r}")
    body = r.json
    for f in ("correction_batch_id", "recorded_at", "revisions"):
        expect(f in body, f"201 body missing {f!r}: {sorted(body)}")
    expect(isinstance(body["correction_batch_id"], str) and
           body["correction_batch_id"], "correction_batch_id is a string (R324)")
    rfc3339_like(body["recorded_at"], "shared recorded_at")
    eq(len(body["revisions"]), 2, "two revisions")
    eq([rev["payment_id"] for rev in body["revisions"]], ["p3", "p1"],
       "revisions in input order (R324)")
    for rev in body["revisions"]:
        eq(rev["recorded_at"], body["recorded_at"],
           "every revision shares one recorded_at (R325)")
        eq(rev["correction_batch_id"], body["correction_batch_id"],
           "revision exposes correction_batch_id (R325)")
        eq(rev["revision"], 2, "revision number")
        eq(rev["amount"], {"p3": 250, "p1": 1800}[rev["payment_id"]], "amount")
    expect(parse_ts(body["recorded_at"]) > parse_ts(max(prior.values(),
           key=parse_ts)), "strictly later than every member's prior (R325)")
    # visible on GET /payments/{id}/revisions too (R243/R325)
    for pid in ("p1", "p3"):
        revs = ada.revisions(pid).json["revisions"]
        eq(revs[-1]["correction_batch_id"], body["correction_batch_id"],
           f"{pid}: batch revision tagged")
        eq(revs[-1]["recorded_at"], body["recorded_at"], f"{pid}: shared recorded_at")
        eq(revs[0]["correction_batch_id"], None, "revision 1 untagged")
    # a single correction exposes correction_batch_id null (R325 AC)
    solo = ada.correct("p2", {"expected_revision": 1, "amount": 450,
                              "effective_at": S(3000), "reason": "solo"}, key="s1")
    eq(solo.status, 201, "single correction")
    eq(solo.json.get("correction_batch_id"), None,
       "single-correction revision has correction_batch_id null (R325 AC)")
    # A27: a batch immediately after is still strictly later (same-second bump)
    b2 = ada.cbatch([{"payment_id": "p2", "expected_revision": 2, "amount": 440,
                      "effective_at": S(3000), "reason": "soon"}], key="b2")
    eq(b2.status, 201, "batch right after the single correction")
    expect(parse_ts(b2.json["recorded_at"]) > parse_ts(solo.json["recorded_at"]),
           "shared recorded_at strictly later than the member's fresh revision (A27/R325)")


@test("batch_replay_and_rejection_idempotency", "R330 R3 R323")
def batch_replay_and_rejection_idempotency(ctx):
    ada, bob, cyd = s4_users(ctx)
    items = [{"payment_id": "p2", "expected_revision": 1, "amount": 450,
              "effective_at": S(3000), "reason": "a"},
             {"payment_id": "p1", "expected_revision": 1, "amount": 1800,
              "effective_at": S(4000), "reason": "b"}]
    first = ada.cbatch(items, key="k")
    eq(first.status, 201, "first use")
    rp = ada.cbatch(items, key="k")
    eq(rp.status, 200, "replay is 200 (R330)")
    eq(rp.json, first.json, "replay returns the original batch response (R330)")
    time.sleep(1.1)  # SA-9
    eq(ada.correct("p2", {"expected_revision": 2, "amount": 400,
                          "effective_at": S(3000), "reason": "later"}, key="solo")
       .status, 201, "a later correction happens")
    rp2 = ada.cbatch(items, key="k")
    eq(rp2.status, 200, "replay after later mutations still 200 (R330)")
    eq(rp2.json, first.json, "original body, not the new state (R330)")
    err_is_(ada.cbatch([dict(items[0], amount=1)], key="k"), 409,
            "idempotency_key_reuse", "same key, different body")
    # a rejected batch leaves no idempotency record (R323)
    bad = [dict(items[0], payment_id="p_ghost")]
    err_is_(ada.cbatch(bad, key="k2"), 404, "not_found", "rejected batch")
    err_is_(ada.cbatch(bad, key="k2"), 404, "not_found",
            "same rejected body replays nothing: same error again")
    good = [dict(items[0], amount=410)]
    eq(ada.cbatch(good, key="k2").status, 201,
       "key free after the rejection (R323)")


@test("originals_unchanged_after_refund_and_batch", "R327 R328 R292 R329")
def originals_unchanged_after_refund_and_batch(ctx):
    ada, bob, cyd = s4_users(ctx)
    # a settlement and an API payment created before the mutations
    settle = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100}],
                        key="setA")
    eq(settle.status, 201, "settlement exists")
    settle_body = settle.json
    pay = bob.pay("cyd", 50, note="pre", key="payA")
    eq(pay.status, 201, "api payment exists")
    pay_body = pay.json
    stmt_before = ada.statement()
    token = stmt_before["snapshot"]
    feed_before = {p["payment_id"]: p for p in ada.activity()}
    rev1_before = ada.revisions("p1").json["revisions"][0]
    # mutate: refund + batch
    eq(bob.refund("p1", {"amount": 300}, key="r1").status, 201, "refund")
    eq(ada.cbatch([
        {"payment_id": "p2", "expected_revision": 1, "amount": 450,
         "effective_at": S(3000), "reason": "x"},
        {"payment_id": "p3", "expected_revision": 1, "amount": 250,
         "effective_at": S(2000), "reason": "y"}], key="b1").status, 201, "batch")
    # original payment and settlement retries return their original bodies (R328)
    rp = bob.pay("cyd", 50, note="pre", key="payA")
    eq(rp.status, 200, "payment retry is 200")
    eq(rp.json, pay_body, "original payment body unchanged (R328)")
    rs = ada.settle([{"from_handle": "ada", "to_handle": "cyd", "amount": 100}],
                    key="setA")
    eq(rs.status, 200, "settlement retry is 200")
    eq(rs.json, settle_body, "settlement retry body unchanged (R328)")
    # activity entries for the original payments byte-identical (R292/R327)
    feed_after = {p["payment_id"]: p for p in ada.activity()}
    for pid in ("p1", "p2", "p3", pay_body["payment_id"],
                settle_body["payments"][0]["payment_id"]):
        eq(feed_after.get(pid), feed_before.get(pid),
           f"activity entry for {pid} unchanged (R292/R327)")
    # revision 1 of p1 unchanged (R327)
    eq(ada.revisions("p1").json["revisions"][0], rev1_before,
       "revision 1 untouched by refund and batch (R327)")
    # earlier snapshot token still pages its frozen entries (R292/R329)
    frozen = ada.statement(f"snapshot={token}")
    eq(frozen["entries"], stmt_before["entries"],
       "pre-mutation snapshot entries unchanged (R292/R329)")
    eq(frozen["closing_balance"], stmt_before["closing_balance"],
       "pre-mutation snapshot closing unchanged (R292)")
    # new statements reflect the new revisions (R329)
    st = ada.statement()
    by_pid = {e["payment"]["payment_id"]: e for e in st["entries"]}
    eq(by_pid["p2"]["payment"]["amount"], 450, "new statement shows the revision (R329)")
    eq(by_pid["p3"]["payment"]["amount"], 250, "new statement shows the revision (R329)")


@test("export_import_v4_roundtrip", "R334 A28 design-29")
def export_import_v4_roundtrip(ctx):
    ada, bob, cyd = s4_users(ctx)
    eq(bob.refund("p1", {"amount": 500}, key="r1").status, 201, "refund")
    batch = ada.cbatch([
        {"payment_id": "p2", "expected_revision": 1, "amount": 450,
         "effective_at": S(3000), "reason": "x"},
        {"payment_id": "p3", "expected_revision": 1, "amount": 250,
         "effective_at": S(2000), "reason": "y"}], key="b1")
    eq(batch.status, 201, "batch")
    cbid = batch.json["correction_batch_id"]
    ex = ctx.export()
    eq(ex["format_version"], 4, "export is format_version 4 (A28)")
    refund_row = next(p for p in ex["state"]["payments"] if p.get("refund_of"))
    eq(refund_row["refund_of"], "p1", "refund_of exported")
    tagged = [p for p in ex["state"]["payments"]
              if any(r.get("correction_batch_id") for r in p.get("revisions", []))]
    eq(len(tagged), 2, "batch revisions carry correction_batch_id in the export")
    # round trip: import the v4 export into a fresh service
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    ctx.import_state(ex)
    ada2 = User_(ctx.api, "ada").login()
    bob2 = User_(ctx.api, "bob").login()
    eq(bob2.me()["total"], 3050, "balances preserved")
    eq(len(ada2.revisions("p2").json["revisions"]), 2, "correction history preserved")
    eq(ada2.revisions("p2").json["revisions"][-1]["correction_batch_id"], cbid,
       "correction_batch_id preserved (A28)")
    rpid = next(p["id"] for p in ex["state"]["payments"] if p.get("refund_of"))
    err_is_(ada2.refund(rpid, {"amount": 10}, key="post"),
            422, "invalid_refund_target",
            "ada2 is the refund's receiver: refund_of semantics survive (R334)")
    err_is_(ada2.correct(rpid, {"expected_revision": 1, "amount": 10,
                                "effective_at": S(60), "reason": "x"}, key="post2"),
            422, "linked_payment_immutable", "refund still immutable after import")
    # v1/v2/v3 payloads import with null defaults (A28)
    for version in (1, 2, 3):
        ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
        ctx.import_state(downgrade(ex, version, strip_auths=(version == 1)))
        ada_v = User_(ctx.api, "ada").login()
        eq(ada_v.me()["total"], 8750, f"v{version} import: balance preserved")
        revs = ada_v.revisions("p2").json["revisions"]
        expect(all(r.get("correction_batch_id") is None for r in revs),
               f"v{version}: correction_batch_id defaults to null (A28)")
        eq(stored_payment(ctx, "p1").get("refund_of"), None,
           f"v{version}: refund_of defaults to null (A28/R305)")
    # referential integrity of refund_of (design §29)
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    broken = json.loads(json.dumps(ex))
    for p in broken["state"]["payments"]:
        if p.get("refund_of"):
            p["refund_of"] = "p_ghost"
    err_is_(ctx.api.post("/_test/import", body=broken, timeout=15), 422,
            "validation_failed", "refund_of naming a missing payment (design-29)")
    selfref = json.loads(json.dumps(ex))
    for p in selfref["state"]["payments"]:
        if p.get("refund_of"):
            p["refund_of"] = p["id"]
    err_is_(ctx.api.post("/_test/import", body=selfref, timeout=15), 422,
            "validation_failed", "refund_of naming itself (design-29)")
    badtype = json.loads(json.dumps(ex))
    for p in badtype["state"]["payments"]:
        if p.get("revisions"):
            p["revisions"][-1]["correction_batch_id"] = 7
            break
    err_is_(ctx.api.post("/_test/import", body=badtype, timeout=15), 422,
            "validation_failed", "correction_batch_id must be string or null (design-29)")


@test("concurrent_shared_revision_at_most_one", "R333")
def concurrent_shared_revision_at_most_one(ctx):
    ada, bob, cyd = s4_users(ctx)
    item = lambda pid, amt: {"payment_id": pid, "expected_revision": 1,
                             "amount": amt, "effective_at": S(4000),
                             "reason": "race"}
    # (a) single vs single on p1
    res, errs = parallel(2, lambda i: ada.correct("p1", {"expected_revision": 1,
                                                         "amount": 1500 + i,
                                                         "effective_at": S(4000),
                                                         "reason": f"s{i}"},
                                                 key=f"ss-{i}"), pool=2)
    expect(not errs, f"errors: {errs[:1]}")
    eq(sorted(r.status for r in res), [201, 409],
       "single vs single: one 201, one 409 (R333)")
    eq(res[[r.status for r in res].index(409)].err_code(), "stale_revision",
       "loser gets stale_revision (R333)")
    # (b) single vs batch, both expecting p1's revision 2 (p2 corrected by bob,
    # its sender)
    ok = bob.correct("p2", {"expected_revision": 1, "amount": 450,
                            "effective_at": S(3000), "reason": "setup"}, key="seed")
    eq(ok.status, 201, "setup")
    res, errs = parallel(2, lambda i: (
        ada.correct("p1", {"expected_revision": 2, "amount": 1000,
                           "effective_at": S(4000), "reason": "single"},
                    key="sb-single") if i == 0 else
        ada.cbatch([{"payment_id": "p1", "expected_revision": 2, "amount": 1100,
                     "effective_at": S(4000), "reason": "batch"}], key="sb-batch")),
        pool=2)
    expect(not errs, f"errors: {errs[:1]}")
    wins = [r.status for r in res if r.status == 201]
    eq(len(wins), 1, "single vs batch: at most one wins (R333)")
    eq(sorted(r.status for r in res), [201, 409], "the other gets 409")
    # (c) batch vs batch sharing p1's expected revision (p1 now at revision 3)
    eq(ada.correct("p1", {"expected_revision": 3, "amount": 900,
                          "effective_at": S(4000), "reason": "to rev4"}, key="s2")
       .status, 201, "p1 at revision 4")
    filler = ada.pay("bob", 10, key="cn-filler")
    eq(filler.status, 201, "fresh filler payment at revision 1")
    res, errs = parallel(2, lambda i: ada.cbatch(
        [{"payment_id": "p1", "expected_revision": 4, "amount": 800 + i,
          "effective_at": S(4000), "reason": f"bb-{i}"},
         {"payment_id": filler.json["payment_id"], "expected_revision": 1,
          "amount": 10, "effective_at": S(60), "reason": "filler"}],
        key=f"bb-{i}"), pool=2)
    expect(not errs, f"errors: {errs[:1]}")
    eq(sorted(r.status for r in res), [201, 409],
       "batch vs batch sharing a revision: at most one succeeds (R333)")
    eq(len(ada.revisions("p1").json["revisions"]), 5, "exactly one appended")
    eq(sum(u.me()["total"] for u in (ada, bob, cyd)), 12500, "conservation")


@test("concurrent_same_key_refund_replay", "R3 R302 R293")
def concurrent_same_key_refund_replay(ctx):
    ada, bob, cyd = s4_users(ctx)

    def hit(i):
        return bob.refund("p1", {"amount": 500}, key="same-key")

    results, errs = parallel(20, hit, pool=20)
    expect(not errs, f"request errors: {errs[:2]}")
    created = [r for r in results if r.status == 201]
    replays = [r for r in results if r.status == 200]
    eq(len(created), 1, "exactly one 201")
    eq(len(replays), 19, "the rest are 200 replays (R302)")
    eq(len({json.dumps(r.json, sort_keys=True) for r in created + replays}), 1,
       "identical bodies everywhere")
    eq(bob.me()["total"], 3000, "money moved exactly once (R303)")


@test("ten_idempotent_write_paths", "R293 R331")
def ten_idempotent_write_paths(ctx):
    ada, bob, cyd = s4_users(ctx)
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 100},
                      token=bob.token, key="rq-seed")
    eq(rq.status, 201, "request seeded")
    rid = rq.json["request_id"]
    auth = ada.authorize("bob", 100, key="au-seed")
    eq(auth.status, 201, "authorization seeded")
    aid = auth.json["authorization_id"]
    paths = [
        ("POST /payments", ctx.api.post("/payments", body={
            "to_handle": "bob", "amount": 10}, token=ada.token)),
        ("POST /requests", ctx.api.post("/requests", body={
            "payer_handle": "ada", "amount": 10}, token=bob.token)),
        ("POST /requests/{id}/pay", ctx.api.post(
            f"/requests/{rid}/pay", body={}, token=ada.token)),
        ("POST /splits", ctx.api.post("/splits", body={
            "amount": 10, "participant_handles": ["bob"]}, token=ada.token)),
        ("POST /settlements", ctx.api.post("/settlements", body={
            "transfers": [{"from_handle": "ada", "to_handle": "cyd",
                           "amount": 5}]}, token=ada.token)),
        ("POST /authorizations", ctx.api.post("/authorizations", body={
            "to_handle": "bob", "amount": 10}, token=ada.token)),
        ("POST /authorizations/{id}/capture", ctx.api.post(
            f"/authorizations/{aid}/capture", body={"amount": 10},
            token=bob.token)),
        ("POST /payments/{id}/corrections", ctx.api.post(
            "/payments/p1/corrections", body={
                "expected_revision": 1, "amount": 100,
                "effective_at": S(4000), "reason": "x"}, token=ada.token)),
        ("POST /payments/{id}/refunds", ctx.api.post(
            "/payments/p1/refunds", body={"amount": 10}, token=bob.token)),
        ("POST /correction-batches", ctx.api.post("/correction-batches", body={
            "corrections": [{"payment_id": "p1", "expected_revision": 1,
                             "amount": 1900, "effective_at": S(4000),
                             "reason": "x"}]}, token=ada.token)),
    ]
    for i, (what, r) in enumerate(paths):
        eq(r.status, 400, f"{what} without a key: status")
        eq(r.err_code(), "missing_idempotency_key",
           f"{what} without a key: code (R293/R331: ten idempotent write paths)")


@test("storm_stage4_invariants", "R291 R1 R2 R3 R45 R300 R303 R323 R333 R292")
def storm_stage4_invariants(ctx):
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
    statuses, five_xx = [], []

    def note(r):
        status = r.status if hasattr(r, "status") else 200
        statuses.append(status)
        if status >= 500:
            five_xx.append(repr(r)[:200])
        return r

    def op(i):
        kind = i % 12
        u = users[i % 4]
        v = users[(i + 1) % 4]
        n = i // 12
        if kind in (0, 1):
            return note(u.pay(v.handle, 10 + i, key=f"storm-pay-{i}"))
        if kind == 2:  # retried payment: same key and body as an earlier pay
            j = i - 12
            if j >= 0:
                return note(u.pay(v.handle, 10 + j, key=f"storm-pay-{j}"))
            return note(u.pay(v.handle, 10 + i, key=f"storm-pay-{i}"))
        if kind == 3:
            a = note(u.authorize(v.handle, 50, key=f"storm-auth-{i}"))
            if a.status == 201:
                note(v.capture(a.json["authorization_id"], body={"amount": 20},
                               key=f"storm-cap-{i}"))
            return a
        if kind == 4:
            return note(u.api.get(f"/me?as_of={S(3900)}&known_at={S(60)}",
                                  token=u.token))
        if kind == 5:
            return note(u.api.get("/statement?limit=3", token=u.token))
        if kind == 6:
            return note(u.api.get(f"/statement?snapshot={token}&limit=2",
                                  token=u.token))
        if kind == 7:
            tgt = f"q{1 + n % 4}"
            body = {"expected_revision": 1, "amount": 100 + (n % 4) * 10,
                    "effective_at": S(4000 + 1 + n % 4), "reason": f"storm {n}"}
            return note(users[0].correct(tgt, body, key=f"storm-corr-{n}"))
        if kind == 8:
            # refund: bob refunds what he received (or fails 409/422 trying)
            tgt = f"q{1 + n % 4}"
            return note(users[1].refund(tgt, {"amount": 5 + n},
                                        key=f"storm-ref-{n}"))
        if kind == 9:
            # batch racing the single corrections on q1/q2 (R333)
            items = [{"payment_id": f"q{1 + (n + j) % 2}",
                      "expected_revision": 1, "amount": 200 + j,
                      "effective_at": S(4000 + (n + j) % 4), "reason": "storm"}
                     for j in range(2)]
            return note(users[0].cbatch(items, key=f"storm-batch-{n}"))
        if kind == 10:
            return note(u.api.get("/me", token=u.token))
        return note(u.settle([{"from_handle": "dee", "to_handle": "cyd",
                               "amount": 5}], key=f"storm-set-{i}"))

    results, errs = parallel(50, op, pool=50)
    expect(not errs, f"client-side errors: {errs[:2]}")
    eq(five_xx, [], "no 5xx under 50 concurrent requests (R45)")
    time.sleep(1.2)  # SA-9
    for inst in (S(5000), S(4000), S(3000), S(60)):
        tot = sum(u.me(f"as_of={inst}")["total"] for u in users)
        eq(tot, seed_total, f"conservation at as_of={inst} (R240)")
    for u in users:
        m = u.me()
        check_wallet_shape(m, "post-storm")
        expect(m["total"] >= 0, f"negative balance for {u.handle}: {m}")
    check_auth_invariants([a for u in users for a in u.list_auths()], "post-storm")
    frozen = users[0].statement(f"snapshot={token}")
    eq([e["payment"]["payment_id"] for e in frozen["entries"]],
       [e["payment"]["payment_id"] for e in full_before["entries"]],
       "pre-storm snapshot entries unchanged (R292/R262)")
    eq(frozen["closing_balance"], full_before["closing_balance"],
       "pre-storm snapshot closing unchanged")
    # at most one winning write per expected revision on each seeded payment (R333)
    ex = ctx.export()
    rows = {p["id"]: p for p in ex["state"]["payments"]}
    for q in ("q1", "q2", "q3", "q4"):
        revs = users[0].revisions(q).json["revisions"]
        expect(len(revs) <= 2, f"{q}: at most one correction landed (R333)")
    # refund invariants across the whole state (R300/R301/R303/R332)
    for pid, p in rows.items():
        if p.get("refund_of"):
            tgt = rows[p["refund_of"]]
            eq(p["from_user_id"], tgt["to_user_id"], f"{pid}: opposite direction")
            eq(p["to_user_id"], tgt["from_user_id"], f"{pid}: opposite direction")
            eq(p.get("request_id"), None, f"{pid}: request_id null (R301)")
            eq(p.get("authorization_id"), None, f"{pid}: authorization_id null (R301)")
            eq(p.get("settlement_id"), None, f"{pid}: refund is no member (R332)")
        refunded = sum(q["amount"] for q in rows.values()
                       if q.get("refund_of") == pid)
        current = p["revisions"][-1]["amount"] if p.get("revisions") else p["amount"]
        expect(refunded <= current,
               f"{pid}: refunded {refunded} exceeds current {current} (R300)")
        recs = [parse_ts(r["recorded_at"]) for r in
                (p.get("revisions") or [{"recorded_at": p["created_at"]}])]
        expect(recs == sorted(recs), f"{pid}: recorded_at monotonic (R230)")
    # batch revisions of one batch share recorded_at and are strictly later (R325)
    by_batch = {}
    for p in ex["state"]["payments"]:
        for r_ in p.get("revisions", []):
            if r_.get("correction_batch_id"):
                by_batch.setdefault(r_["correction_batch_id"], []).append(
                    (p["id"], r_["recorded_at"]))
    for cbid, entries in by_batch.items():
        eq(len({ts for _, ts in entries}), 1, f"{cbid}: one shared recorded_at (R325)")
        for pid, ts in entries:
            revs = rows[pid]["revisions"]
            idx = next(i for i, r_ in enumerate(revs)
                       if r_.get("correction_batch_id") == cbid)
            if idx > 0:
                expect(parse_ts(ts) > parse_ts(revs[idx - 1]["recorded_at"]),
                       f"{cbid}/{pid}: strictly later than the prior revision (R325)")
    eq(sum(u.me()["total"] for u in users), seed_total, "final conservation")