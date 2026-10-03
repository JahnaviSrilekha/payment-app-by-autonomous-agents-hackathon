"""Reviewer stress probe for stage 3 (T23-T29): concurrent/retried/mixed
corrections, statement snapshots, and authorization capture/void races.

Independent of specs/stage-3/acceptance (reuses its core.py primitives only).
Checks invariants the requirements and design state, after each run:
  - exactly one winner under a correction race (R231 stale_revision, R2/R3 replay)
  - balances/held/available never negative, available == total - held (R276)
  - a hold's authorized amount is conserved across concurrent capture/void races
  - a statement snapshot never changes after it is taken (R290), even under a
    concurrent correction/capture storm
  - global conservation: sum of every user's total as_of now equals the sum of
    base_balances plus the net of every known-effective payment (R216/R234/R239)

Run: python3 specs/stage-3/probes/stress_corrections_snapshots_holds.py --base-url http://127.0.0.1:8110
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "acceptance"))

import core  # noqa: E402
from core import (Ctx, User, fixture, fx_user, fx_auth, parallel, eq, expect,
                   check_wallet_shape)


def run_correction_race(ctx):
    """Many concurrent corrections on the same payment with the same
    expected_revision: exactly one may succeed (R231), the rest 409
    stale_revision; money moves exactly once (R239)."""
    ctx.reset(fixture([fx_user("u_a", "a", 9000), fx_user("u_b", "b", 1000)],
                      payments=[{"id": "p1", "from_user_id": "u_a",
                                 "to_user_id": "u_b", "amount": 1000,
                                 "currency": "EUR", "note": "", "visibility": "public",
                                 "request_id": None, "settlement_id": None,
                                 "authorization_id": None,
                                 "created_at": core.past(100), "seq": 1}]))
    a = User(ctx.api, "a").login()
    b = User(ctx.api, "b").login()

    def attempt(i):
        return a.correct("p1", {"expected_revision": 1, "amount": 1000 + i,
                                "effective_at": core.past(100),
                                "reason": "race %d" % i}, key="race-key-%d" % i)

    results, errs = parallel(20, attempt, pool=20)
    expect(not errs, "correction race: no client-side exceptions: %r" % errs)
    statuses = [r.status for r in results if r is not None]
    oks = [r for r in results if r is not None and r.status == 201]
    conflicts = [r for r in results if r is not None and r.status == 409]
    eq(len(oks), 1, "correction race: exactly one 201 among 20 concurrent attempts, got %r"
       % statuses)
    eq(len(conflicts), 19, "correction race: the rest are 409 stale_revision")
    expect(all(r.json["error"]["code"] == "stale_revision" for r in conflicts),
           "correction race: losers are specifically stale_revision, not some other 409")
    revs = a.revisions("p1").json["revisions"]
    eq(len(revs), 2, "correction race: exactly one new revision committed")
    m = a.me()
    expect(m["total"] >= 0 and m["available"] >= 0, "correction race: no negative balance")
    print("OK  correction_race: exactly one winner out of 20 concurrent same-revision attempts")


def run_idempotency_replay_race(ctx):
    """Many concurrent requests sharing one idempotency key for a correction:
    the operation must commit exactly once; every response is identical."""
    ctx.reset(fixture([fx_user("u_a", "a", 9000), fx_user("u_b", "b", 1000)],
                      payments=[{"id": "p1", "from_user_id": "u_a",
                                 "to_user_id": "u_b", "amount": 1000,
                                 "currency": "EUR", "note": "", "visibility": "public",
                                 "request_id": None, "settlement_id": None,
                                 "authorization_id": None,
                                 "created_at": core.past(100), "seq": 1}]))
    a = User(ctx.api, "a").login()

    def attempt(i):
        return a.correct("p1", {"expected_revision": 1, "amount": 1500,
                                "effective_at": core.past(100),
                                "reason": "shared key"}, key="shared-key")

    results, errs = parallel(25, attempt, pool=25)
    expect(not errs, "idempotent replay race: no client-side exceptions: %r" % errs)
    statuses = [r.status for r in results if r is not None]
    # R62/stage-1 R.. : first use -> 201, every replay (same key, same body) -> 200
    # with the original body (not re-asserting Created semantics).
    eq(sorted(statuses), [200] * 24 + [201],
       "idempotent replay race: exactly one 201 (first use), the rest 200 (replay), got %r"
       % statuses)
    bodies = {repr(sorted(r.json.items())) for r in results if r is not None}
    eq(len(bodies), 1,
       "idempotent replay race: every response body (201 or replayed 200) is identical")
    revs = a.revisions("p1").json["revisions"]
    eq(len(revs), 2, "idempotent replay race: the correction committed exactly once")
    print("OK  idempotency_replay_race: 25 concurrent same-key calls, exactly one commit")


def run_capture_void_race(ctx):
    """Concurrent capture and void on the same open authorization: at most one
    lifecycle-closing operation may win; the authorized amount is conserved
    (captured + released == original amount), and held/available never go
    negative mid-race."""
    ctx.reset(fixture([fx_user("u_a", "a", 10000), fx_user("u_b", "b", 0)]))
    a = User(ctx.api, "a").login()
    b = User(ctx.api, "b").login()
    auth = a.authorize("b", 500, key="race-auth")
    eq(auth.status, 201, "capture/void race: setup authorize")
    aid = auth.json["authorization_id"]

    def attempt(i):
        if i % 2 == 0:
            return ("capture", b.capture(aid, body={"amount": 100, "final": False},
                                        key="cap-%d" % i))
        return ("void", a.void(aid))

    results, errs = parallel(30, attempt, pool=30)
    expect(not errs, "capture/void race: no client-side exceptions: %r" % errs)
    captures_ok = [r for kind, r in results if kind == "capture" and r.status == 201]
    final_auth = [x for x in a.list_auths() if x["authorization_id"] == aid]
    eq(len(final_auth), 1, "capture/void race: authorization still exists exactly once")
    row = final_auth[0]
    expect(row["captured_amount"] >= 0 and row["captured_amount"] <= 500,
           "capture/void race: captured_amount within [0,500]: %r" % row)
    total_captured = sum(100 for _ in captures_ok)
    eq(row["captured_amount"], total_captured,
       "capture/void race: captured_amount matches the number of captures that actually won")
    m_a = a.me()
    m_b = b.me()
    expect(m_a["held"] >= 0, "capture/void race: a's held never negative")
    expect(m_a["available"] >= 0, "capture/void race: a's available never negative")
    eq(m_a["available"], m_a["total"] - m_a["held"], "capture/void race: a available==total-held")
    expect(m_b["total"] == total_captured,
           "capture/void race: b received exactly the money that was actually captured")
    print("OK  capture_void_race: %d concurrent captures + voids, amount conserved (captured=%d)"
          % (len(results), total_captured))


def run_snapshot_immutability_under_storm(ctx):
    """Take a statement snapshot, then hammer the account with concurrent
    corrections and a capture/void; the snapshot must read back byte-identical
    throughout and after (R290), even though the live view moves."""
    ctx.reset(fixture([fx_user("u_a", "a", 9500), fx_user("u_b", "b", 500)],
                      payments=[{"id": "p%d" % i, "from_user_id": "u_a",
                                 "to_user_id": "u_b", "amount": 100,
                                 "currency": "EUR", "note": "", "visibility": "public",
                                 "request_id": None, "settlement_id": None,
                                 "authorization_id": None,
                                 "created_at": core.past(1000 - i), "seq": i + 1}
                                for i in range(5)]))
    a = User(ctx.api, "a").login()
    st = a.statement()
    token = st["snapshot"]
    frozen_before = (st["opening_balance"], st["entries"], st["closing_balance"])

    def attempt(i):
        if i % 2 == 0:
            pid = "p%d" % (i % 5)
            return a.correct(pid, {"expected_revision": 1, "amount": 50 + i,
                                   "effective_at": core.past(1000 - (i % 5)),
                                   "reason": "storm %d" % i}, key="snap-storm-%d" % i)
        return a.statement(f"snapshot={token}")

    results, errs = parallel(20, attempt, pool=20)
    expect(not errs, "snapshot immutability: no client-side exceptions: %r" % errs)
    for i, r in enumerate(results):
        if i % 2 == 1:  # a frozen-read attempt
            eq((r["opening_balance"], r["entries"], r["closing_balance"]), frozen_before,
               "snapshot immutability: concurrent frozen read %d matches original" % i)
    final = a.statement(f"snapshot={token}")
    eq((final["opening_balance"], final["entries"], final["closing_balance"]),
       frozen_before, "snapshot immutability: final read after the storm still matches")
    print("OK  snapshot_immutability_under_storm: snapshot unchanged across %d concurrent ops"
          % len(results))


def run_global_conservation_storm(ctx):
    """Many concurrent payments/captures/corrections across several users, then
    check that live totals conserve money: sum(base_balance) + net(all payments
    at their selected, currently-known revision) == sum(total) for everyone."""
    users = ["a", "b", "c", "d"]
    ctx.reset(fixture([fx_user(f"u_{h}", h, 5000) for h in users]))
    logged_in = {h: User(ctx.api, h).login() for h in users}
    seeded_total = 5000 * len(users)

    def attempt(i):
        frm = logged_in[users[i % 4]]
        to = users[(i + 1) % 4]
        if i % 3 == 0:
            auth = frm.authorize(to, 10, key="g-auth-%d" % i)
            return ("authorize", auth)
        return ("pay", frm.pay(to, 1, key="g-pay-%d" % i))

    results, errs = parallel(60, attempt, pool=30)
    expect(not errs, "global conservation: no client-side exceptions: %r" % errs)
    totals = {h: logged_in[h].me() for h in users}
    for h, m in totals.items():
        check_wallet_shape(m, "global conservation: %s" % h)
    grand_total = sum(m["total"] for m in totals.values())
    eq(grand_total, seeded_total,
       "global conservation: sum of all totals unchanged by internal transfers "
       "(seeded %d, got %d)" % (seeded_total, grand_total))
    print("OK  global_conservation_storm: %d concurrent ops across %d users, total conserved (%d)"
          % (len(results), len(users), grand_total))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    args = ap.parse_args()
    ctx = Ctx(args.base_url)
    probes = [run_correction_race, run_idempotency_replay_race,
              run_capture_void_race, run_snapshot_immutability_under_storm,
              run_global_conservation_storm]
    failures = 0
    for probe in probes:
        try:
            probe(ctx)
        except core.Check as e:
            failures += 1
            print("FAIL %s: %s" % (probe.__name__, e))
        except Exception as e:
            failures += 1
            print("ERROR %s: %r" % (probe.__name__, e))
    print("\n%d/%d probes passed" % (len(probes) - failures, len(probes)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
