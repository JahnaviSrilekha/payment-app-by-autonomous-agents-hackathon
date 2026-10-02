"""Pocketful stage-2 acceptance — API-level black-box tests.

Every test derives from we-are-devs/pocketful spec stage-2.md (and stage-1.md
by reference) via specs/stage-2/requirements.md. No product code is read.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from core import (Check, Skip, User, check_all_wallets, check_auth_invariants,
                  eq, expect, fixture, fx_auth, fx_user, parallel, parse_ts,
                  rfc3339_like, test)

PW = "hunter2hunter2"


def four_users(balances=(10000, 10000, 10000, 10000)):
    return [fx_user(f"u_{h}", h, b) for h, b in
            zip(("ada", "bob", "cyd", "dee"), balances)]


def seeded(ctx, fx):
    ctx.reset(fx)
    return [User_login(ctx, h) for h in ("ada", "bob", "cyd", "dee")]


def User_login(ctx, handle):
    return User(ctx.api, handle).login()


def fresh(ctx, operators=("u_ada",)):
    return seeded(ctx, fixture(four_users(), operators=list(operators)))


def err_is(r, status, code, what):
    eq(r.status, status, f"{what}: status")
    eq(r.err_code(), code, f"{what}: error code (body {r.raw[:200]!r})")


# ----------------------------------------------------------------- model/reset

@test("reset_me_holds", "R147 R155 R159 R144 R145")
def reset_me_holds(ctx):
    fx = fixture(four_users(), authorizations=[
        fx_auth("a_open", "u_ada", "u_bob", 2000, "open", 7200),
        fx_auth("a_cap", "u_ada", "u_bob", 500, "captured", 7200),
        fx_auth("a_void", "u_bob", "u_cyd", 300, "voided", 7200),
        fx_auth("a_exp", "u_bob", "u_cyd", 400, "expired", -7200),
    ])
    ada, bob, cyd, dee = seeded(ctx, fx)
    m = ada.me()
    eq(m["balance"], 10000, "ada balance")
    eq(m["total"], 10000, "ada total")
    eq(m["held"], 2000, "ada held == open seeded hold only")
    eq(m["available"], 8000, "ada available")
    eq(bob.me()["held"], 0, "bob holds nothing (only the payer holds)")
    eq(cyd.me()["held"], 0, "cyd held")
    m2 = dee.me()
    eq((m2["held"], m2["available"], m2["total"]), (0, 10000, 10000),
       "dee untouched: available==total, held 0")
    err_is(ctx.api.get("/me"), 401, "unauthenticated", "no token")
    eq(m["currency"], "EUR", "currency")
    eq(m["minor_units"], 2, "minor_units")


@test("reset_no_holds_baseline", "R147 R148")
def reset_no_holds_baseline(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    m = ada.me()
    eq((m["balance"], m["total"], m["available"], m["held"]),
       (10000, 10000, 10000, 0), "no holds: all agree, held zero")


@test("reset_v1_fixture_and_default_ttl", "R158 R154 R155")
def reset_v1_fixture_and_default_ttl(ctx):
    users = four_users()
    fx = fixture(users)
    expect("authorizations" not in fx, "v1-shaped fixture omits authorizations")
    expect("authorization_ttl_seconds" not in fx, "v1 fixture omits ttl")
    ada, bob, cyd, dee = seeded(ctx, fx)
    eq(ada.me()["held"], 0, "omission means empty list")
    r = ada.authorize("bob", 500, key="ttl-default-1")
    eq(r.status, 201, "create ok")
    a = r.json
    eq((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(),
       600, "default ttl 600 when fixture omits it")
    expect(abs((parse_ts(a["created_at"]) -
                datetime.now(timezone.utc)).total_seconds()) < 30,
           "created_at near now")


@test("reset_ttl_validation", "R154")
def reset_ttl_validation(ctx):
    ada, *_ = fresh(ctx)
    for bad in (0, -5, "600", 1.5, True, [600]):
        r = ctx.api.post("/_test/reset", timeout=15,
                         body=fixture(four_users(), ttl=bad))
        err_is(r, 422, "validation_failed", f"ttl {bad!r}")
    eq(ada.me()["total"], 10000, "failed reset changes nothing")
    eq(ada.list_auths(), [], "no auths installed by failed reset")
    eq(ctx.api.post("/_test/reset", timeout=15,
                    body=fixture(four_users(), ttl=1)).status, 204,
       "ttl 1 is a positive integer -> ok")


@test("reset_hold_over_balance", "R156")
def reset_hold_over_balance(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    r = ctx.api.post("/_test/reset", timeout=15, body=fixture(
        [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)],
        authorizations=[fx_auth("a_big", "u_ada", "u_bob", 10001, "open", 7200)]))
    err_is(r, 422, "validation_failed", "unexpired open hold 10001 > balance 10000")
    eq(ada.me()["total"], 10000, "previous state still intact after failed reset")
    eq(ada.list_auths(), [], "no auths installed by failed reset")
    r = ctx.api.post("/_test/reset", timeout=15, body=fixture(
        [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)],
        authorizations=[fx_auth("a_eq", "u_ada", "u_bob", 10000, "open", 7200)]))
    eq(r.status, 204, "boundary: hold exactly == balance is fine")
    ada2 = User_login(ctx, "ada")
    eq(ada2.me()["available"], 0, "fully held")
    r = ctx.api.post("/_test/reset", timeout=15, body=fixture(
        [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0)],
        authorizations=[fx_auth("a_old", "u_ada", "u_bob", 99999, "open", -7200)]))
    eq(r.status, 204, "expired seeded hold never counts (only unexpired open)")
    ada3 = User_login(ctx, "ada")
    eq(ada3.me()["available"], 10000, "expired hold releases on read")


@test("reset_seeded_statuses", "R157 R159 R153")
def reset_seeded_statuses(ctx):
    fx = fixture(four_users(), authorizations=[
        fx_auth("a_o", "u_ada", "u_bob", 2000, "open", 7200),
        fx_auth("a_c", "u_bob", "u_cyd", 700, "captured", 7200),
        fx_auth("a_v", "u_cyd", "u_dee", 600, "voided", 7200),
        fx_auth("a_e", "u_dee", "u_ada", 500, "expired", -7200),
    ])
    ada, bob, cyd, dee = seeded(ctx, fx)
    eq(ada.me()["held"], 2000, "only open holds")
    eq(bob.me()["held"], 0, "captured holds nothing")
    eq(cyd.me()["held"], 0, "voided holds nothing")
    eq(dee.me()["held"], 0, "expired holds nothing")
    by_id = {a["authorization_id"]: a for a in ada.list_auths()}
    for aid, status in (("a_o", "open"), ("a_c", "captured"),
                        ("a_v", "voided"), ("a_e", "expired")):
        expect(aid in by_id, f"seeded {aid} listed")
        eq(by_id[aid]["status"], status, f"{aid} status")
        eq(by_id[aid]["remaining_amount"], 2000 if status == "open" else 0,
           f"{aid} remaining_amount")


@test("seeded_expired_lazy_read", "R159 R160 R182")
def seeded_expired_lazy_read(ctx):
    fx = fixture(four_users(), authorizations=[
        fx_auth("a_past", "u_ada", "u_bob", 3000, "open", -7200)])
    ada, bob, cyd, dee = seeded(ctx, fx)
    rows = ada.list_auths()
    eq(len(rows), 1, "listed")
    eq(rows[0]["status"], "expired",
       "lazy expiry shows expired without any action at the deadline")
    m = ada.me()
    eq(m["held"], 0, "held released")
    eq(m["available"], 10000, "remainder back in available")
    eq(bob.me()["total"], 10000, "receiver untouched by expiry")
    r = ctx.api.get("/authorizations?status=open", token=ada.token)
    eq([a["authorization_id"] for a in r.json["authorizations"]], [],
       "clock-expired never matches status=open")
    r = ctx.api.get("/authorizations?status=expired", token=ada.token)
    eq([a["authorization_id"] for a in r.json["authorizations"]], ["a_past"],
       "clock-expired matches status=expired")


@test("lazy_expiry_created_auth", "R143 R159 R154 R145")
def lazy_expiry_created_auth(ctx):
    ada, bob, cyd, dee = seeded(ctx, fixture(four_users(), ttl=1))
    r = ada.authorize("bob", 1500, key="exp-1")
    eq(r.status, 201, "created")
    aid = r.json["authorization_id"]
    eq(ada.me()["held"], 1500, "held immediately")
    time.sleep(2.2)  # the deadline passes with no action taken at it
    rows = ada.list_auths()
    eq(rows[0]["status"], "expired", "expired on read after ttl")
    m = ada.me()
    eq(m["held"], 0, "held released lazily")
    eq(m["available"], m["total"], "available restored")
    err_is(bob.capture(aid, {}, key="exp-cap"), 409, "authorization_expired",
           "capture after expiry")
    eq(ada.me()["total"], 10000, "no money moved by expiry")


# ----------------------------------------------------------------- create

@test("authz_create_shape", "R161 R162 R172")
def authz_create_shape(ctx):
    ada, bob, cyd, dee = seeded(ctx, fixture(four_users(), ttl=3600))
    r = ada.authorize("bob", 2000, note="deposit", visibility="private", key="c1")
    eq(r.status, 201, f"201: {r}")
    a = r.json
    eq(a["from_handle"], "ada", "from_handle")
    eq(a["to_handle"], "bob", "to_handle")
    eq(a["from_user_id"], "u_ada", "from_user_id")
    eq(a["to_user_id"], "u_bob", "to_user_id")
    eq(a["amount"], 2000, "amount")
    eq(a["captured_amount"], 0, "captured_amount 0")
    eq(a["status"], "open", "status open")
    eq(a["payment_id"], None, "payment_id null")
    eq(a["remaining_amount"], 2000, "remaining == amount")
    eq(a["note"], "deposit", "note verbatim")
    eq(a["visibility"], "private", "visibility")
    eq(a["currency"], "EUR", "currency")
    rfc3339_like(a["expires_at"], "expires_at")
    rfc3339_like(a["created_at"], "created_at")
    eq((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(),
       3600, "expires_at == created_at + ttl")
    m = ada.me()
    eq((m["held"], m["available"]), (2000, 8000), "hold placed, nothing moved")
    eq(bob.me()["total"], 10000, "receiver total unchanged by a hold")
    r2 = ada.authorize("bob", 5, key="c2")
    eq(r2.status, 201, "defaults ok")
    eq(r2.json["note"], "", "note default empty")
    eq(r2.json["visibility"], "public", "visibility default public")


@test("authz_create_errors", "R163 R18 R31")
def authz_create_errors(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    eq(ada.pay("bob", 5000, key="half").status, 201, "ada down to 5000")
    err_is(ada.authorize("bob", 5001, key="e1"), 409, "insufficient_funds",
           "available < amount")
    eq(ada.me()["available"], 5000, "refused authorization changes nothing")
    eq(ada.authorize("bob", 5000, key="e1b").status, 201,
       "boundary: amount == available ok")

    ada2, *_ = fresh(ctx)
    for bad in (0, -1, 1000000001, "5", True, 1.5):
        err_is(ada2.authorize("bob", bad, key=f"e2-{bad!r}"), 422,
               "validation_failed", f"amount {bad!r}")
    eq(ada2.authorize("bob", 1000000000, key="e2max").status, 201,
       "boundary 1e9 ok when funded")

    ada3, *_ = fresh(ctx)
    err_is(ada3.authorize("ada", 50, key="e3"), 422, "self_payment",
           "own handle")
    err_is(ada3.authorize("bob", 50, note="x" * 201, key="e4"), 422,
           "validation_failed", "note 201 chars")
    eq(ada3.authorize("bob", 50, note="x" * 200, key="e4b").status, 201,
       "note 200 chars ok")

    ada4, *_ = fresh(ctx)
    for v in ("Public", "friends", "", 1, None):
        err_is(ada4.authorize("bob", 50, visibility=v, key=f"e5-{v!r}"), 422,
               "validation_failed", f"visibility {v!r}")
    err_is(ada4.authorize("nobody", 50, key="e6"), 404, "not_found",
           "unknown handle")
    body = {"to_handle": "bob", "amount": 60, "bogus": 1}
    eq(ada4.api.post("/authorizations", body=body, token=ada4.token,
                     key="e7").status, 201, "unknown body fields ignored")


@test("authz_create_error_precedence", "R163")
def authz_create_error_precedence(ctx):
    """R163 table read as precedence (A8): available first, unknown handle last."""
    ada, bob, cyd, dee = fresh(ctx)
    eq(ada.pay("bob", 9500, key="drain").status, 201, "ada down to 500")
    err_is(ada.authorize("ghost_handle", 1000, key="p1"), 409,
           "insufficient_funds", "row1 insufficient_funds beats row5 not_found")
    err_is(ada.authorize("ada", 1000, key="p2"), 409, "insufficient_funds",
           "row1 beats row3 self_payment")
    err_is(ada.authorize("bob", 1000, note="x" * 201, key="p3"), 409,
           "insufficient_funds", "row1 beats row4 note")
    err_is(ada.authorize("ghost_handle", 0, key="p4"), 422,
           "validation_failed", "amount 0: row1 cannot fire, row2 does")
    err_is(ada.authorize("ghost_handle", "5", key="p5"), 422,
           "validation_failed", "non-integer amount -> row2")
    err_is(ada.authorize("ada", 100, note="x" * 201, key="p6"), 422,
           "self_payment", "row3 self_payment beats row4 note")


@test("authz_create_idempotency", "R152 R161 R59 R60 R61 R57 R44")
def authz_create_idempotency(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    body = {"to_handle": "bob", "amount": 120}
    err_is(ada.api.post("/authorizations", body=body, token=ada.token), 400,
           "missing_idempotency_key", "key required")
    err_is(ada.api.post("/authorizations", body=body, token=ada.token, key=""),
           400, "missing_idempotency_key", "empty key")
    err_is(ada.api.post("/authorizations", body=body, token=ada.token,
                        key="x" * 256), 422, "validation_failed", "key 256 chars")
    r1 = ada.api.post("/authorizations", body=body, token=ada.token, key="x" * 255)
    eq(r1.status, 201, "key 255 chars ok")
    r2 = ada.api.post("/authorizations",
                      body={"amount": 120, "to_handle": "bob"},
                      token=ada.token, key="x" * 255)
    eq(r2.status, 200, "replay 200 (key order irrelevant)")
    eq(r2.json, r1.json, "replay body identical as a JSON value")
    r3 = ada.api.post("/authorizations", body={"to_handle": "bob", "amount": 121},
                      token=ada.token, key="x" * 255)
    err_is(r3, 409, "idempotency_key_reuse", "same key different body")
    r4 = ada.api.post("/authorizations",
                      body={"to_handle": "bob", "amount": 120, "note": "x"},
                      token=ada.token, key="x" * 255)
    err_is(r4, 409, "idempotency_key_reuse",
           "extra unknown field -> different JSON value")
    eq(ada.me()["held"], 120, "only one hold created")
    err_is(ada.authorize("bob", 0, key="k2"), 422, "validation_failed", "fails")
    eq(ada.authorize("bob", 30, key="k2").status, 201, "key freed after 4xx")
    rb = cyd.api.post("/authorizations", body={"to_handle": "bob", "amount": 40},
                      token=cyd.token, key="x" * 255)
    eq(rb.status, 201, "same key string, other user: independent (R57)")
    eq(ada.me()["held"], 150, "cyd's hold does not affect ada")

    res, errs = parallel(20, lambda i: ada.api.post(
        "/authorizations", body={"to_handle": "bob", "amount": 70},
        token=ada.token, key="race1"))
    expect(not errs, f"concurrent create errors: {errs[:2]}")
    codes = sorted(r.status for r in res)
    eq(codes.count(201), 1, f"exactly one 201, got {codes}")
    expect(all(c == 200 for c in codes if c != 201), f"others 200: {codes}")
    bodies = {json.dumps(r.json, sort_keys=True) for r in res}
    eq(len(bodies), 1, "all responses identical")
    eq(ada.me()["held"], 150 + 70, "exactly one hold placed by the race")


@test("authz_not_in_activity", "R164 R167")
def authz_not_in_activity(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    a = ada.authorize("bob", 700, note="hold", key="act1").json
    for u in (ada, bob, cyd):
        eq(len(u.activity()), 0,
           f"open authorization is not a feed item ({u.handle})")
    pay = bob.capture(a["authorization_id"], {}, key="act2").json
    eq(pay["authorization_id"], a["authorization_id"], "capture payment linked")
    eq(pay["request_id"], None, "request_id null")
    eq(pay["amount"], 700, "payment amount == captured")
    eq(len(ada.activity()), 1, "payer sees the capture payment")
    eq(len(bob.activity()), 1, "receiver sees the capture payment")
    eq(len(cyd.activity()), 1, "default public visibility: third party sees it")
    a2 = ada.authorize("bob", 300, visibility="private", key="act3").json
    pay2 = bob.capture(a2["authorization_id"], {}, key="act4").json
    eq(pay2["visibility"], "private", "visibility copied from authorization")
    eq(pay2["note"], "", "note copied from authorization")
    eq(len(cyd.activity()), 1, "private capture hidden from third party")
    eq(len(bob.activity()), 2, "receiver is never hidden from")


# ----------------------------------------------------------------- capture

@test("capture_default_final_full_and_partial", "R143 R165 R167 R168 R169 R172")
def capture_default_final_full_and_partial(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    a = ada.authorize("bob", 2000, key="cap-a").json
    aid = a["authorization_id"]
    r = bob.capture(aid, {}, key="cap-b")
    eq(r.status, 201, f"capture 201: {r}")
    p = r.json
    eq(p["authorization_id"], aid, "authorization_id set")
    eq(p["request_id"], None, "request_id null")
    eq(p["amount"], 2000, "full capture amount")
    eq(p["from_handle"], "ada", "payment from payer")
    eq(p["to_handle"], "bob", "payment to receiver")
    eq(p["currency"], "EUR", "currency")
    rfc3339_like(p["created_at"], "created_at")
    m = ada.me()
    eq((m["total"], m["held"], m["available"]), (8000, 0, 8000),
       "payer: 2000 moved, hold released in the same step")
    eq(bob.me()["total"], 12000, "receiver credited once")
    row = [x for x in ada.list_auths() if x["authorization_id"] == aid][0]
    eq(row["status"], "captured", "status captured")
    eq(row["captured_amount"], 2000, "captured_amount")
    eq(row["remaining_amount"], 0, "remaining 0")
    eq(row["payment_id"], p["payment_id"], "payment_id = latest capture")
    eq(row["payment_ids"], [p["payment_id"]], "payment_ids in order")
    err_is(bob.capture(aid, {}, key="cap-c"), 409, "authorization_not_open",
           "second capture after a final capture")
    eq(bob.me()["total"], 12000, "no double movement")

    ada, bob, cyd, dee = fresh(ctx)
    a2 = ada.authorize("bob", 2000, key="cap-d").json
    r2 = bob.capture(a2["authorization_id"], {"amount": 1500}, key="cap-e")
    eq(r2.status, 201, "partial final capture")
    eq(r2.json["amount"], 1500, "payment amount is the captured amount")
    m = ada.me()
    eq((m["total"], m["held"], m["available"]), (8500, 0, 8500),
       "1500 of 2000: 500 returned to available in the same step")
    eq(bob.me()["total"], 11500, "receiver got 1500")
    eq(ada.me()["total"] + bob.me()["total"] + cyd.me()["total"] +
       dee.me()["total"], 40000, "conservation of total")


@test("capture_extended_mode", "R143 R170 R172 R173")
def capture_extended_mode(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="x1").json["authorization_id"]
    r1 = bob.capture(aid, {"amount": 700, "final": False}, key="x2")
    eq(r1.status, 201, "nonfinal capture")
    a = ada.list_auths()[0]
    eq(a["status"], "open", "stays open")
    eq(a["captured_amount"], 700, "cumulative captured_amount")
    eq(a["remaining_amount"], 1300, "remainder still held")
    m = ada.me()
    eq((m["held"], m["available"], m["total"]), (1300, 8700, 10000),
       "remainder stays held, total unmoved")
    eq(bob.me()["total"], 10700, "receiver got 700")
    r2 = bob.capture(aid, {"amount": 600, "final": False}, key="x3")
    eq(r2.status, 201, "second nonfinal capture")
    a = ada.list_auths()[0]
    eq((a["captured_amount"], a["remaining_amount"]), (1300, 700),
       "cumulative 1300, remaining 700")
    eq(len(a["payment_ids"]), 2, "payment_ids lists every capture in order")
    eq(a["payment_id"], r2.json["payment_id"], "payment_id is the latest capture")
    eq(a["payment_ids"][1], r2.json["payment_id"], "order preserved")
    r3 = bob.capture(aid, {"amount": 700, "final": False}, key="x4")
    eq(r3.status, 201, "capturing the entire remainder")
    a = ada.list_auths()[0]
    eq(a["status"], "captured", "closes even with final:false")
    eq(a["remaining_amount"], 0, "nothing held")
    eq(ada.me()["held"], 0, "held released")
    eq(ada.me()["total"], 8000, "2000 moved in total")
    eq(bob.me()["total"], 12000, "receiver got 2000")

    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="x5").json["authorization_id"]
    bob.capture(aid, {"amount": 300, "final": False}, key="x6")
    r = bob.capture(aid, {"amount": 100, "final": True}, key="x7")
    eq(r.status, 201, "final capture of part of the remainder")
    a = ada.list_auths()[0]
    eq(a["status"], "captured", "a final capture closes it")
    eq(a["remaining_amount"], 0, "and releases any remainder")
    eq(a["captured_amount"], 400, "captured_amount cumulative")
    eq(ada.me()["held"], 0, "held 0")
    eq(ada.me()["available"], ada.me()["total"], "available == total")


@test("capture_exceeds_vs_remaining", "R171 R165")
def capture_exceeds_vs_remaining(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="ce1").json["authorization_id"]
    eq(bob.capture(aid, {"amount": 700, "final": False}, key="ce2").status, 201,
       "700 captured, 1300 remaining")
    err_is(bob.capture(aid, {"amount": 1400}, key="ce3"), 422,
           "capture_exceeds_authorization",
           "compares with remaining 1300, not original 2000")
    eq(bob.capture(aid, {"amount": 1300}, key="ce4").status, 201,
       "amount == remaining ok (boundary)")

    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="ce5").json["authorization_id"]
    bob.capture(aid, {"amount": 700, "final": False}, key="ce6")
    r = bob.capture(aid, {}, key="ce7")
    eq(r.status, 201, "omitted amount defaults to the remainder")
    eq(r.json["amount"], 1300, "defaulted to remaining 1300")
    a = ada.list_auths()[0]
    eq(a["status"], "captured", "closed")
    eq(a["captured_amount"], 2000, "cumulative full")


@test("capture_errors", "R175 R18 R40")
def capture_errors(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 1000, key="cx1").json["authorization_id"]
    err_is(bob.capture("a_nope", {}, key="cx2"), 404, "not_found",
           "unknown authorisation")
    err_is(ada.capture(aid, {}, key="cx3"), 403, "forbidden",
           "the payer cannot capture")
    err_is(cyd.capture(aid, {}, key="cx4"), 403, "forbidden",
           "a caller who is neither party cannot capture")
    for bad in (0, -5, "5", True, 1.5):
        err_is(bob.capture(aid, {"amount": bad}, key=f"cx-{bad!r}"), 422,
               "validation_failed", f"amount {bad!r}")
    err_is(bob.capture(aid, {"amount": 1001}, key="cx10"), 422,
           "capture_exceeds_authorization", "above the authorized amount")
    eq(bob.capture(aid, {"amount": 1000}, key="cx11").status, 201,
       "boundary: exactly the authorized amount")
    err_is(bob.capture(aid, {"amount": 1}, key="cx12"), 409,
           "authorization_not_open", "capture after a final capture")

    ada, bob, cyd, dee = seeded(ctx, fixture(four_users(), authorizations=[
        fx_auth("a_past", "u_ada", "u_bob", 800, "open", -7200)]))
    err_is(bob.capture("a_past", {}, key="cx13"), 409, "authorization_expired",
           "capture on a clock-expired authorisation")


@test("capture_error_precedence", "R175")
def capture_error_precedence(ctx):
    """R175 table read as precedence (A8): expired beats exceeds/forbidden."""
    ada, bob, cyd, dee = seeded(ctx, fixture(four_users(), authorizations=[
        fx_auth("a_past", "u_ada", "u_bob", 800, "open", -7200)]))
    err_is(bob.capture("a_past", {"amount": 99999}, key="pp1"), 409,
           "authorization_expired", "expired beats capture_exceeds_authorization")
    err_is(ada.capture("a_past", {}, key="pp2"), 409, "authorization_expired",
           "expired beats forbidden (caller is the payer, not the receiver)")


@test("capture_idempotency", "R166 R152 R59 R60 R61 R174")
def capture_idempotency(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="ci1").json["authorization_id"]
    err_is(bob.api.post(f"/authorizations/{aid}/capture", body={},
                        token=bob.token), 400, "missing_idempotency_key",
           "key required")
    r1 = bob.capture(aid, {"amount": 700, "final": False}, key="ci2")
    eq(r1.status, 201, "first capture")
    r2 = bob.capture(aid, {"amount": 700, "final": False}, key="ci2")
    eq(r2.status, 200, "replay 200")
    eq(r2.json, r1.json, "replay body identical")
    err_is(bob.capture(aid, {"amount": 700}, key="ci2"), 409,
           "idempotency_key_reuse",
           "{}-meaning vs explicit same-value amount are different JSON bodies")
    err_is(bob.capture(aid, {"amount": 700, "final": True}, key="ci2"), 409,
           "idempotency_key_reuse",
           "final:false vs final:true differ (new fields do not change equality)")
    err_is(bob.capture(aid, {}, key="ci2"), 409, "idempotency_key_reuse",
           "{} vs {amount:700} are different bodies")
    eq(bob.me()["total"], 10700, "still moved exactly once")
    err_is(bob.capture(aid, {"amount": 99999}, key="ci3"), 422,
           "capture_exceeds_authorization", "failed capture")
    eq(bob.capture(aid, {"amount": 100, "final": False}, key="ci3").status, 201,
       "key freed after a failed 4xx")

    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 1000, key="ci4").json["authorization_id"]
    res, errs = parallel(20, lambda i: bob.capture(aid, {}, key="ci5"))
    expect(not errs, f"errors {errs[:2]}")
    eq(sorted(r.status for r in res).count(201), 1, "exactly one 201")
    bodies = {json.dumps(r.json, sort_keys=True) for r in res}
    eq(len(bodies), 1, "all responses identical")
    eq(bob.me()["total"], 11000, "credited exactly once")
    eq(ada.me()["total"], 9000, "debited exactly once")


@test("capture_money_once_randomized", "R146 R192 R145")
def capture_money_once_randomized(ctx):
    import random
    rng = random.Random(20260924)
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 10000, key="rm0").json["authorization_id"]
    for i in range(30):
        rows = [x for x in ada.list_auths() if x["authorization_id"] == aid]
        auth = rows[0]
        rem = auth["remaining_amount"]
        if auth["status"] != "open":
            eq(rem, 0, "closed -> remaining 0")
            err_is(bob.capture(aid, {"amount": 1}, key=f"rm-x{i}"), 409,
                   "authorization_not_open", f"capture after close #{i}")
            break
        move = rng.choice([1, rem, max(1, rem // 2), rem + rng.randint(1, 50)])
        final = rng.random() < 0.5
        r = bob.capture(aid, {"amount": move, "final": final}, key=f"rm-{i}")
        if move > rem:
            err_is(r, 422, "capture_exceeds_authorization",
                   f"#{i} over-remainder refused")
        else:
            eq(r.status, 201, f"#{i} accepted")
            eq(r.json["amount"], move, f"#{i} moved exactly the asked amount")
        auth2 = [x for x in ada.list_auths()
                 if x["authorization_id"] == aid][0]
        expect(0 <= auth2["captured_amount"] <= 10000,
               f"#{i} cumulative captures within the authorized amount")
        eq(auth2["remaining_amount"],
           0 if auth2["status"] != "open" else 10000 - auth2["captured_amount"],
           f"#{i} remaining consistent")
        m = ada.me()
        eq(m["held"], 10000 - auth2["captured_amount"]
           if auth2["status"] == "open" else 0, f"#{i} held matches remaining")
        eq(m["available"], m["total"] - m["held"], f"#{i} available identity")
        expect(m["available"] >= 0, f"#{i} available never negative")
        eq(bob.me()["total"], 10000 + auth2["captured_amount"],
           f"#{i} receiver credited exactly the cumulative capture")


# ----------------------------------------------------------------- void

@test("void_payer_only_and_states", "R176 R177 R178 R179 R173 R172")
def void_payer_only_and_states(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 1200, key="v1").json["authorization_id"]
    err_is(bob.void(aid), 403, "forbidden", "the receiver cannot void")
    err_is(cyd.void(aid), 403, "forbidden", "a caller who is neither party cannot void")
    r = ada.void(aid)
    eq(r.status, 200, f"the payer voids: {r}")
    a = r.json
    eq(a["status"], "voided", "status voided")
    eq(a["remaining_amount"], 0, "hold released")
    m = ada.me()
    eq((m["held"], m["available"], m["total"]), (0, 10000, 10000),
       "hold released, no money moved")
    eq(bob.me()["total"], 10000, "receiver got nothing")
    r2 = ada.void(aid)
    eq(r2.status, 200, "voiding an already-voided one is 200")
    eq(r2.json["status"], "voided", "with the current state")
    eq(ada.me()["available"], 10000, "no double release")
    err_is(bob.capture(aid, {}, key="v2"), 409, "authorization_not_open",
           "a voided hold cannot be captured")
    err_is(ada.void("a_nope"), 404, "not_found", "unknown authorisation")

    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 400, key="v3").json["authorization_id"]
    bob.capture(aid, {}, key="v4")
    err_is(ada.void(aid), 409, "authorization_not_open", "void of a captured one")

    ada, bob, cyd, dee = seeded(ctx, fixture(four_users(), authorizations=[
        fx_auth("a_past", "u_ada", "u_bob", 400, "open", -7200)]))
    err_is(ada.void("a_past"), 409, "authorization_not_open", "void of an expired one")


@test("void_partially_captured_preserves_records", "R173 R172 R169")
def void_partially_captured_preserves_records(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 2000, key="vp1").json["authorization_id"]
    p = bob.capture(aid, {"amount": 700, "final": False}, key="vp2").json
    r = ada.void(aid)
    eq(r.status, 200, "void closes a partially captured authorisation")
    a = r.json
    eq(a["status"], "voided", "voided")
    eq(a["captured_amount"], 700, "capture records preserved")
    eq(a["payment_ids"], [p["payment_id"]], "payment_ids preserved")
    eq(a["payment_id"], p["payment_id"], "latest payment_id preserved")
    eq(a["remaining_amount"], 0, "only the remainder released")
    m = ada.me()
    eq((m["total"], m["held"], m["available"]), (9300, 0, 9300),
       "700 moved; the 1300 remainder never left available")
    eq(bob.me()["total"], 10700, "receiver keeps the 700 capture")


# ----------------------------------------------------------------- list

@test("authz_list_scope_direction_newest", "R180 R181")
def authz_list_scope_direction_newest(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    ids = [ada.authorize("bob", 100 + i, key=f"L{i}").json["authorization_id"]
           for i in range(4)]
    eq(len(cyd.list_auths()), 0, "a non-party sees none")
    eq({a["authorization_id"] for a in bob.list_auths()}, set(ids),
       "the receiver sees all")
    eq(len(ada.list_auths("direction=incoming")), 0, "ada incoming none")
    eq(len(bob.list_auths("direction=outgoing")), 0, "bob outgoing none")
    eq({a["authorization_id"] for a in ada.list_auths("direction=outgoing")},
       set(ids), "outgoing = caller is the payer")
    eq({a["authorization_id"] for a in bob.list_auths("direction=incoming")},
       set(ids), "incoming = caller is the receiver")
    created = [parse_ts(a["created_at"]) for a in ada.list_auths()]
    expect(all(created[i] >= created[i + 1] for i in range(len(created) - 1)),
           "newest first by created_at (ties in either order)")
    # strict ordering with distinguishable timestamps
    time.sleep(1.1)
    late1 = ada.authorize("bob", 900, key="L-late1").json["authorization_id"]
    time.sleep(1.1)
    late2 = ada.authorize("bob", 901, key="L-late2").json["authorization_id"]
    listed = [a["authorization_id"] for a in ada.list_auths("direction=outgoing")]
    eq(listed[:2], [late2, late1], "newest first with distinct created_at")


@test("authz_list_filters_status", "R182 R181 R159 R44 R42")
def authz_list_filters_status(ctx):
    fx = fixture(four_users(), authorizations=[
        fx_auth("a_o", "u_ada", "u_bob", 100, "open", 7200),
        fx_auth("a_c", "u_ada", "u_bob", 100, "captured", 7200),
        fx_auth("a_v", "u_ada", "u_bob", 100, "voided", 7200),
        fx_auth("a_e", "u_ada", "u_bob", 100, "expired", -7200),
        fx_auth("a_clock", "u_ada", "u_bob", 100, "open", -7200),
    ])
    ada, bob, cyd, dee = seeded(ctx, fx)

    def ids(q):
        r = ctx.api.get(f"/authorizations?{q}", token=ada.token)
        eq(r.status, 200, f"list {q}")
        return sorted(a["authorization_id"] for a in r.json["authorizations"])

    eq(ids("status=open"), ["a_o"], "open excludes a clock-expired row")
    eq(ids("status=expired"), ["a_clock", "a_e"], "expired includes it")
    eq(ids("status=captured"), ["a_c"], "captured filter")
    eq(ids("status=voided"), ["a_v"], "voided filter")
    eq(set(ids("")), {"a_o", "a_c", "a_v", "a_e", "a_clock"}, "absent = all")
    for bogus in ("status=OPEN", "status=pending", "direction=sideways"):
        err_is(ctx.api.get(f"/authorizations?{bogus}", token=ada.token),
               422, "validation_failed", f"unknown filter value {bogus}")


@test("authz_list_pagination_like_requests", "R183 R44 R42")
def authz_list_pagination_like_requests(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    for i in range(52):
        eq(ada.authorize("bob", 1, key=f"P{i}").status, 201, f"create {i}")
    r = ctx.api.get("/authorizations", token=ada.token)
    eq(len(r.json["authorizations"]), 50, "default limit 50")
    eq(r.json["has_more"], True, "has_more true")
    r2 = ctx.api.get("/authorizations?limit=50&offset=50", token=ada.token)
    eq(len(r2.json["authorizations"]), 2, "page 2 remainder")
    eq(r2.json["has_more"], False, "has_more false at the end")
    r3 = ctx.api.get("/authorizations?limit=200&offset=0", token=ada.token)
    eq(len(r3.json["authorizations"]), 52, "limit 200 ok")
    for q in ("limit=0", "limit=201", "offset=-1", "limit=4.0", "limit=1e2",
              "offset=+3"):
        err_is(ctx.api.get(f"/authorizations?{q}", token=ada.token),
               422, "validation_failed", f"bad pagination {q!r}")
    r = ctx.api.get("/authorizations?limit=50&offset=52", token=ada.token)
    eq((r.status, len(r.json["authorizations"]), r.json["has_more"]),
       (200, 0, False), "offset past the end -> empty page, has_more false")


# ----------------------------------------------------------------- holds vs stage-1 paths

@test("held_funds_unspendable_everywhere", "R145 R149 R163")
def held_funds_unspendable_everywhere(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    ada.authorize("bob", 6000, key="h1")
    eq(ada.me()["available"], 4000, "available 4000")
    err_is(ada.pay("bob", 4001, key="h2"), 409, "insufficient_funds",
           "a payment cannot spend held funds")
    eq(ada.me()["total"], 10000, "nothing moved")
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 4001},
                      token=bob.token, key="h3").json
    err_is(bob.api.post(f"/requests/{rq['request_id']}/pay", body={},
                        token=bob.token, key="h4"), 409, "insufficient_funds",
           "request-pay cannot spend held funds")
    err_is(ctx.api.post("/settlements", body={"transfers": [
        {"from_handle": "ada", "to_handle": "cyd", "amount": 4001}]},
        token=ada.token, key="h5"), 409, "insufficient_funds",
        "settlement net debits cannot spend held funds")
    err_is(ada.authorize("bob", 4001, key="h6"), 409, "insufficient_funds",
           "a new authorization cannot spend held funds")
    eq(ada.me()["total"], 10000, "all refusals left the wallet intact")
    eq(ada.me()["held"], 6000, "hold unchanged")
    eq(ada.pay("bob", 4000, key="h7").status, 201,
       "boundary: amount == available spends only free funds")

    ada, bob, cyd, dee = fresh(ctx)
    ada.authorize("bob", 6000, key="h8")
    eq(ada.authorize("bob", 4000, key="h9").status, 201,
       "boundary: authorization for exactly the available amount")


@test("stage1_paths_hold_free_and_marked", "R148 R167 R149")
def stage1_paths_hold_free_and_marked(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    p = ada.pay("bob", 500, note="n", visibility="private", key="s1").json
    eq(p["authorization_id"], None, "direct payment authorization_id null")
    eq(p["request_id"], None, "request_id null")
    eq(ada.me()["held"], 0, "a payment leaves no intermediate hold")
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 300},
                      token=bob.token, key="s2").json
    p2 = bob.api.post(f"/requests/{rq['request_id']}/pay",
                      body={"visibility": "public"},
                      token=bob.token, key="s3").json
    eq(p2["authorization_id"], None, "request-pay payment authorization_id null")
    eq(ada.me()["held"], 0, "request-pay leaves no hold")
    st = ctx.api.post("/settlements", body={"transfers": [
        {"from_handle": "ada", "to_handle": "cyd", "amount": 100}]},
        token=ada.token, key="s4").json
    for pay in st["payments"]:
        eq(pay["authorization_id"], None, "settlement payment authorization_id null")
    eq(ada.me()["held"], 0, "settlement leaves no hold")


@test("splits_unchanged_under_holds", "R151 R80 R82 R83")
def splits_unchanged_under_holds(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    ada.authorize("bob", 6000, key="sp0")
    r = ctx.api.post("/splits", body={
        "amount": 1000, "participant_handles": ["bob", "cyd", "dee"],
        "note": "lunch"}, token=ada.token, key="sp1")
    eq(r.status, 201, "split unaffected by holds (no balance check)")
    s = r.json
    eq([sh["amount"] for sh in s["shares"]], [334, 333, 333],
       "rounding table 1000/3 -> 334,333,333")
    eq([sh["handle"] for sh in s["shares"]], ["bob", "cyd", "dee"],
       "shares in the given order")
    eq(len(s["requests"]), 3, "one request per non-caller participant")
    r2 = ctx.api.post("/splits", body={
        "amount": 1, "participant_handles": ["dee", "cyd", "bob"]},
        token=ada.token, key="sp2")
    eq(r2.status, 201, "second split ok")
    eq([sh["amount"] for sh in r2.json["shares"]], [1, 0, 0],
       "remainder to the first participant in the given order")
    m = ada.me()
    eq((m["held"], m["total"]), (6000, 10000), "a split itself moves no money")


@test("authorize_request_interaction_out_of_scope", "R150 R167")
def authorize_request_interaction_out_of_scope(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 500},
                      token=bob.token, key="rq1").json
    err_is(ctx.api.post(f"/requests/{rq['request_id']}/authorize", body={},
                        token=ada.token, key="rq2"), 404, "not_found",
           "authorizing a request is out of scope: no such endpoint")
    a = ada.authorize("bob", 500, key="rq3").json
    expect(a.get("request_id") is None, "an authorization never references a request")
    p = ada.api.post(f"/requests/{rq['request_id']}/pay", body={},
                     token=ada.token, key="rq4").json
    eq(p["request_id"], rq["request_id"], "payment from paying a request")
    eq(p["authorization_id"], None,
       "a payment is never both request-paid and authorization-captured")


# ----------------------------------------------------------------- concurrency

@test("storm_mixed_concurrent", "R144 R145 R146 R192 R45 R2 R1")
def storm_mixed_concurrent(ctx):
    import random
    ada, bob, cyd, dee = fresh(ctx)
    users = {"ada": ada, "bob": bob, "cyd": cyd, "dee": dee}
    names = list(users)
    stop = time.time() + 6.0
    problems, transport_errors = [], []

    def reader(u):
        while time.time() < stop:
            m = u.api.get("/me", token=u.token)
            if m.status >= 500:
                problems.append(f"5xx on /me: {m.status}")
            elif m.status == 200:
                j = m.json
                if (j["available"] < 0 or j["held"] < 0 or
                        j["available"] != j["total"] - j["held"] or
                        j["balance"] != j["total"]):
                    problems.append(f"/me identity violated: {j}")
            la = u.api.get("/authorizations?limit=200", token=u.token)
            if la.status >= 500:
                problems.append(f"5xx on /authorizations: {la.status}")
            elif la.status == 200:
                try:
                    check_auth_invariants(la.json["authorizations"], "storm-list")
                except Check as e:
                    problems.append(f"list invariant: {e}")
            time.sleep(0.01)

    rng = random.Random(7)

    def worker(i):
        me = users[names[i % 4]]
        other = users[(i + 1) % 4]
        suffix = f"s{i}-{rng.randrange(10**9)}"
        while time.time() < stop:
            op = rng.randrange(10)
            if op == 0:
                me.pay(other.handle, rng.randint(1, 300), key=suffix)
            elif op == 1:
                me.api.post("/requests", body={
                    "payer_handle": other.handle, "amount": rng.randint(1, 300)},
                    token=me.token, key=suffix)
            elif op == 2:
                rows = me.api.get("/requests?limit=5&status=pending",
                                  token=me.token).json["requests"]
                for rq in rows[:2]:
                    if rng.random() < 0.5:
                        me.api.post(f"/requests/{rq['request_id']}/pay", body={},
                                    token=me.token,
                                    key=f"{suffix}-{rq['request_id']}")
                    else:
                        me.api.post(f"/requests/{rq['request_id']}/decline",
                                    body={}, token=me.token)
            elif op == 3:
                rows = me.api.get(
                    "/requests?limit=5&status=pending&direction=outgoing",
                    token=me.token).json["requests"]
                for rq in rows[:1]:
                    me.api.post(f"/requests/{rq['request_id']}/cancel",
                                body={}, token=me.token)
            elif op == 4:
                me.api.post("/splits", body={
                    "amount": rng.randint(1, 400),
                    "participant_handles": rng.sample(names, 3)},
                    token=me.token, key=suffix)
            elif op == 5:
                me.api.post("/settlements", body={"transfers": [
                    {"from_handle": names[i % 4],
                     "to_handle": names[(i + 2) % 4],
                     "amount": rng.randint(1, 200)}]},
                    token=me.token, key=suffix)
            elif op in (6, 7):
                r = me.authorize(other.handle, rng.randint(1, 500), key=suffix)
                if r.status == 201:
                    aid = r.json["authorization_id"]
                    roll = rng.random()
                    if roll < 0.45:
                        other.capture(aid, {}, key=suffix)
                    elif roll < 0.8:
                        other.capture(aid, {"amount": max(1, r.json["amount"] // 2),
                                            "final": False}, key=suffix)
                    else:
                        me.void(aid)
            elif op == 8:
                me.activity("limit=20")
            else:
                me.api.get("/requests?limit=10", token=me.token)

    threads = [threading.Thread(target=reader, args=(u,), daemon=True)
               for u in users.values()]
    for t in threads:
        t.start()
    res, errs = parallel(50, worker, pool=50)
    for t in threads:
        t.join(timeout=10)
    expect(not errs, f"worker errors: {errs[:3]}")
    expect(not problems, f"invariant violations: {problems[:5]}")
    check_all_wallets([ada, bob, cyd, dee], 40000, "storm-end")
    for u in (ada, bob, cyd, dee):
        check_auth_invariants(u.list_auths(), "storm-end")


@test("concurrent_authorizations_same_headroom", "R145 R192 R163")
def concurrent_authorizations_same_headroom(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    problems = []
    stop = time.time() + 3.0

    def reader():
        while time.time() < stop:
            m = ada.me()
            if m["available"] < 0 or m["available"] != m["total"] - m["held"]:
                problems.append(f"transient negative available: {m}")
            time.sleep(0.005)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    res, errs = parallel(20, lambda i: ada.authorize("bob", 1000, key=f"hd-{i}"))
    t.join(timeout=5)
    expect(not errs, f"{errs[:2]}")
    expect(not problems, f"{problems[:3]}")
    codes = [r.status for r in res]
    eq(codes.count(201), 10, "exactly available//1000 authorizations succeed")
    eq(codes.count(409), 10, "the rest are 409 insufficient_funds")
    m = ada.me()
    eq((m["held"], m["available"]), (10000, 0), "all headroom held")


@test("concurrent_captures_and_void_race", "R146 R192 R145 R173")
def concurrent_captures_and_void_race(ctx):
    for trial in range(12):
        ada, bob, cyd, dee = fresh(ctx)
        aid = ada.authorize("bob", 5000,
                            key=f"cv{trial}-a").json["authorization_id"]

        def act(i):
            if i % 3 == 0:
                return ada.void(aid)
            if i % 3 == 1:
                return bob.capture(aid, {"amount": 2000, "final": False},
                                   key=f"cv{trial}-c{i}")
            return bob.capture(aid, {}, key=f"cv{trial}-f{i}")

        res, errs = parallel(9, act)
        expect(not errs, f"trial {trial}: {errs[:2]}")
        moved = sum(r.json["amount"] for r in res
                    if r.status == 201 and isinstance(r.json, dict) and
                    "amount" in r.json)
        a = [x for x in ada.list_auths() if x["authorization_id"] == aid][0]
        ca = a["captured_amount"]
        eq(ca, moved, f"trial {trial}: captured_amount == sum of capture responses")
        expect(0 <= ca <= 5000, f"trial {trial}: captured {ca} within authorized")
        m = ada.me()
        eq(m["total"], 10000 - ca, f"trial {trial}: payer total == 10000 - captured")
        eq(bob.me()["total"], 10000 + ca, f"trial {trial}: receiver == +captured")
        eq(m["available"], m["total"], f"trial {trial}: hold fully resolved")
        eq(a["remaining_amount"], 0, f"trial {trial}: nothing remains held")
        expect(a["status"] in ("captured", "voided"), f"trial {trial}: final status")
        if ca:
            expect(len(a.get("payment_ids", [])) >= 1,
                   f"trial {trial}: captures recorded")
        check_all_wallets([ada, bob, cyd, dee], 40000, f"trial {trial} end")


@test("replay_after_state_change", "R62 R59 R166")
def replay_after_state_change(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    aid = ada.authorize("bob", 800, key="rp1").json["authorization_id"]
    r1 = bob.capture(aid, {}, key="rp2")
    eq(r1.status, 201, "capture commits")
    rc = ada.authorize("bob", 800, key="rp1")
    eq(rc.status, 200, "create replay still 200 after the state changed")
    eq(rc.json["authorization_id"], aid, "same authorization")
    eq(rc.json["captured_amount"], 0, "original create body returned")
    rc2 = bob.capture(aid, {}, key="rp2")
    eq(rc2.status, 200, "capture replay 200 after close")
    eq(rc2.json, r1.json, "identical payment body")
    eq(bob.me()["total"], 10800, "no extra movement")


# ----------------------------------------------------------------- export/import

def downgrade_to_v1(export):
    st = json.loads(json.dumps(export))
    st["format_version"] = 1
    state = st.get("state", {})
    state.pop("authorizations", None)
    state.pop("authorization_ttl_seconds", None)
    for pay in state.get("payments", []) or []:
        pay.pop("authorization_id", None)
    return st


@test("import_v1_export_defaults", "R138 R158 R154 R155")
def import_v1_export_defaults(ctx):
    fx = fixture(four_users(), authorizations=[
        fx_auth("a_o", "u_ada", "u_bob", 1000, "open", 7200)])
    ada, bob, cyd, dee = seeded(ctx, fx)
    ada.pay("bob", 250, key="ex1")
    v1 = downgrade_to_v1(ctx.export())
    eq(v1["format_version"], 1, "v1 payload")
    ctx.reset(fixture([fx_user("u_zed", "zed", 5)]))
    eq(ctx.api.post("/_test/import", body=v1, timeout=15).status, 204,
       "v1-shaped export imports cleanly")
    ada2, bob2 = User_login(ctx, "ada"), User_login(ctx, "bob")
    eq(ada2.me()["total"], 9750, "imported balance (250 was paid)")
    eq(ada2.me()["held"], 0, "v1 carries no holds")
    eq(ada2.list_auths(), [], "authorizations empty after v1 import")
    r = ada2.authorize("bob", 100, key="post-v1")
    eq(r.status, 201, "service fully usable after import")
    eq((parse_ts(r.json["expires_at"]) -
        parse_ts(r.json["created_at"])).total_seconds(), 600,
       "authorization_ttl_seconds defaulted to 600")


@test("import_stage1_live_if_available", "R138 R158")
def import_stage1_live_if_available(ctx):
    if ctx.stage1 is None:
        raise Skip("--stage1-url not given; synthesized v1 covered elsewhere")
    fx = fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 500)])
    eq(ctx.stage1.post("/_test/reset", body=fx, timeout=15).status, 204,
       "stage-1 reset")
    ada1 = User(ctx.stage1, "ada").login()
    bob1 = User(ctx.stage1, "bob").login()
    ada1.pay("bob", 700, note="from stage 1", key="s1x")
    bob1.api.post("/requests", body={"payer_handle": "ada", "amount": 300},
                  token=bob1.token, key="s1r")
    export = ctx.stage1.get("/_test/export", timeout=15)
    eq(export.status, 200, "stage-1 export")
    eq(export.json["format_version"], 1, "stage-1 exports format_version 1")
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    eq(ctx.api.post("/_test/import", body=export.json, timeout=15).status, 204,
       "stage-2 accepts an export produced by the stage-1 service")
    ada2 = User_login(ctx, "ada")
    eq(ada2.me()["total"], 9300, "balances imported")
    eq(ada2.list_auths(), [], "no authorizations")
    eq(ada2.pay("bob", 50, key="s1y").status, 201,
       "fully usable after the stage-1 import")


@test("import_preserves_session_and_retry", "R139 R141 R140 R91")
def import_preserves_session_and_retry(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    token_before = ada.token
    body = {"to_handle": "bob", "amount": 111, "note": "lost response"}
    r = ada.api.post("/payments", body=body, token=ada.token, key="lost-1")
    eq(r.status, 201, "payment committed; response 'lost' to the client")
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 222},
                      token=bob.token, key="rq-pre").json
    export = ctx.export()
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    eq(ctx.api.post("/_test/import", body=export, timeout=15).status, 204,
       "import completes between browser requests")
    r = ctx.api.get("/me", token=token_before)
    eq(r.status, 200, f"the pre-import bearer token is still valid: {r}")
    eq(r.json["handle"], "ada", "same user")
    r2 = ada.api.post("/payments", body=body, token=ada.token, key="lost-1")
    eq(r2.status, 200, f"retry with the same key and body after import: {r2}")
    eq(r2.json["payment_id"], r.json["payment_id"], "original payment recovered")
    eq(ada.me()["total"], 9889, "10000-111: moved exactly once across the import")
    rp = bob.api.post(f"/requests/{rq['request_id']}/pay", body={},
                      token=bob.token, key="rq-post")
    eq(rp.status, 201, f"an existing pending request stays payable: {rp}")
    eq(ada.me()["total"], 9667, "9889-222")


@test("import_replacement_and_validation", "R87 R92 R88 R93 R85")
def import_replacement_and_validation(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    export = ctx.export()
    eq(export["track"], "pocketful", "track")
    expect("format_version" in export and "state" in export, "export shape")
    zoe = User(ctx.api, "zoe").signup()
    eq(ctx.api.post("/_test/import", body=export, timeout=15).status, 204,
       "re-import replaces state")
    eq(ctx.api.get("/me", token=zoe.token).status, 401,
       "destination-only credentials removed (replacement, not merge)")
    eq(User_login(ctx, "ada").me()["total"], 10000, "restored exactly")
    for bad, what in (
            ({**export, "track": "other"}, "wrong track"),
            ({**export, "format_version": 3}, "future version"),
            ({**export, "state": {"users": "nope"}}, "invalid state"),
            ({k: v for k, v in export.items() if k != "state"}, "missing state")):
        err_is(ctx.api.post("/_test/import", body=bad, timeout=15), 422,
               "validation_failed", f"import rejects {what}")
    eq(User_login(ctx, "ada").me()["total"], 10000, "failed imports change nothing")
    eq(ctx.api.post("/_test/reset", body=fixture(four_users()),
                    timeout=15).status, 204, "reset clears imported state")
    eq(User_login(ctx, "ada").me()["total"], 10000, "clean baseline after reset")


# ----------------------------------------------------------------- content negotiation

@test("content_negotiation_shared_routes", "R105 R184")
def content_negotiation_shared_routes(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 50},
                      token=bob.token, key="cn-1").json
    ada.authorize("bob", 60, key="cn-2")
    for path in ("/requests", "/authorizations"):
        r_html = ctx.api.get(path, token=ada.token,
                             headers={"Accept": "text/html"})
        eq(r_html.status, 200, f"{path} Accept: text/html")
        ctype = r_html.headers.get("Content-Type", "")
        expect("text/html" in ctype,
               f"{path}: text/html content type, got {ctype!r}")
        expect(b"data-testid" in r_html.raw,
               f"{path}: HTML page exposes data-testid markup")
        r_json = ctx.api.get(path, token=ada.token)
        eq(r_json.status, 200, f"{path} without Accept header")
        expect(r_json.json is not None and isinstance(r_json.json, dict),
               f"{path}: JSON without text/html: {r_json.raw[:120]!r}")
        r_json2 = ctx.api.get(path, token=ada.token,
                              headers={"Accept": "application/json"})
        expect(r_json2.json is not None,
               f"{path}: JSON for explicit application/json")


# ----------------------------------------------------------------- stage-1 regression smoke

@test("stage1_regression_smoke", "R147 R149 R152 R56 R59 R66 R73 R77 R82 R97")
def stage1_regression_smoke(ctx):
    ada, bob, cyd, dee = fresh(ctx)
    r1 = ada.pay("bob", 100, note="x", key="g1")
    eq(r1.status, 201, "pay 201")
    r2 = ada.pay("bob", 100, note="x", key="g1")
    eq((r2.status, r2.json["payment_id"]), (200, r1.json["payment_id"]),
       "replay 200, same body")
    err_is(ada.pay("bob", 100, note="y", key="g1"), 409,
           "idempotency_key_reuse", "same key different body")
    err_is(ada.pay("ada", 5, key="g2"), 422, "self_payment", "self")
    err_is(ada.pay("bob", 0, key="g3"), 422, "validation_failed", "amount 0")
    err_is(ada.pay("ghost", 5, key="g4"), 404, "not_found", "unknown handle")
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 50},
                      token=bob.token, key="g5").json
    eq(ctx.api.post(f"/requests/{rq['request_id']}/cancel", body={},
                    token=ada.token).status, 403,
       "payer cannot cancel (requester only)")
    eq(bob.api.post(f"/requests/{rq['request_id']}/cancel", body={},
                    token=bob.token).status, 200, "requester cancels")
    err_is(ada.api.post(f"/requests/{rq['request_id']}/pay", body={},
                        token=ada.token, key="g6"), 409, "request_not_pending",
           "payer pays after cancel")
    ada.pay("bob", 30, visibility="private", key="g7")
    expect(all(p["amount"] != 30 for p in cyd.activity()),
           "private payment hidden from a third party")
    expect(any(p["amount"] == 100 for p in ada.activity()),
           "public payment visible to the sender")
    err_is(ctx.api.post("/settlements", body={"transfers": [
        {"from_handle": "ada", "to_handle": "bob", "amount": 50},
        {"from_handle": "cyd", "to_handle": "bob", "amount": 99999999}]},
        token=ada.token, key="g8"), 409, "insufficient_funds",
        "collective settlement check")
    eq(ada.me()["total"], 9870, "failed settlement moved nothing")
    m = ada.me()
    expect({"user_id", "display_name", "handle", "balance", "currency",
            "minor_units"} <= set(m), "GET /me stage-1 fields present")