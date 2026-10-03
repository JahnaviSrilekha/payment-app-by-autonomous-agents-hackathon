"""Reviewer black-box probe for stage-4 T31 (R294-R305, R332, A21, A22).

Independent of stage-4/tests/test_refunds.py: drives the real HTTP server
in-process via stage-4/tests/util.py's harness only, and writes its own
assertions against the requirement text rather than reusing the developer's.

Focus: the A21 error-precedence chain (the part a per-requirement unit test
usually can't show on its own, since each test typically triggers only the
one error it names) and a couple of R303/R305 shape checks via raw HTTP.

Run: cd <checkout>/stage-4 && python3 <repo>/specs/stage-4/probes/probe_t31_refunds.py
"""
import os
import sys

# Resolve relative to the current working directory (run from inside the
# stage-4 checkout being reviewed), not the reviewer's own repo copy — so
# this probe exercises whichever stage-4/ the caller cd'd into.
STAGE4 = os.getcwd()
sys.path.insert(0, os.path.join(STAGE4, "tests"))
sys.path.insert(0, os.path.join(STAGE4, "src"))

import util  # noqa: E402

FAILURES = []


def check(label, cond):
    status = "ok" if cond else "FAIL"
    print("[%s] %s" % (status, label))
    if not cond:
        FAILURES.append(label)


def fixture():
    return {
        "currency": "EUR", "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 100},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "note": "coffee", "visibility": "public",
             "created_at": "2026-01-01T10:00:00+00:00"},
            # bob immediately spends most of it onward, so his final balance (100)
            # is both history-consistent and far below p_1's 6000 — he can't cover
            # a refund from bare balance alone without this second leg.
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 5900, "note": "spent", "visibility": "public",
             "created_at": "2026-01-01T10:30:00+00:00"},
        ],
        "requests": [],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                    {"email": email, "password": "correct horse"})
    return payload["token"]


def refund(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/refunds" % payment_id, body,
                           token=token, key=key)


def main():
    port, srv = util.start_server()
    try:
        client = util.Client(port)
        util.reset(client, fixture())
        ada = login(client, "ada@example.com")
        bob = login(client, "bob@example.com")

        # A21: ownership (403) must precede amount-shape (422) — ada (not the
        # receiver) sends a structurally invalid amount; must still get 403.
        status, body, _ = refund(client, ada, "p_1", {"amount": -5}, "pr1")
        check("ownership(403) precedes amount-shape(422)",
              status == 403 and body["error"]["code"] == "forbidden")

        # A21: amount-shape (422) must precede refund-of-refund (422) — bob
        # (the real receiver) on a non-refund target with a bad amount gets
        # validation_failed, not invalid_refund_target.
        status, body, _ = refund(client, bob, "p_1", {"amount": 0}, "pr2")
        check("amount-shape fires before refund-of-refund check",
              status == 422 and body["error"]["code"] == "validation_failed")

        # A21: refund_exceeds_payment (422) must precede insufficient_funds
        # (409) — bob's available (100) is far below the payment (6000), and
        # the requested amount also exceeds the payment itself.
        status, body, _ = refund(client, bob, "p_1", {"amount": 999999}, "pr3")
        check("refund_exceeds_payment(422) precedes insufficient_funds(409)",
              status == 422 and body["error"]["code"] == "refund_exceeds_payment")

        # R303: a request within the payment's cap but above bob's available
        # (100) must be 409 insufficient_funds, not 422.
        status, body, _ = refund(client, bob, "p_1", {"amount": 5000}, "pr4")
        check("amount under payment cap but over available -> 409 insufficient_funds",
              status == 409 and body["error"]["code"] == "insufficient_funds")

        # A valid refund within bob's available funds succeeds and exposes
        # refund_of (R301/R305), and a second refund of it is rejected (R298).
        status, body, _ = refund(client, bob, "p_1", {"amount": 50}, "pr5")
        check("valid refund -> 201 with refund_of set",
              status == 201 and body["refund_of"] == "p_1" and
              body["from_user_id"] == "u_bob" and body["to_user_id"] == "u_ada")
        refund_id = body["payment_id"]

        status, body, _ = refund(client, ada, refund_id, {"amount": 10}, "pr6")
        check("refund of a refund -> 422 invalid_refund_target",
              status == 422 and body["error"]["code"] == "invalid_refund_target")

        # R300/A22: cumulative cap is enforced across multiple refunds, not
        # just the single-request amount (50 already taken above; 5950 more
        # would hit the 6000 cap exactly at the boundary +1).
        status, body, _ = refund(client, bob, "p_1", {"amount": 5951}, "pr7")
        check("cumulative cap enforced across separate refunds (A22)",
              status == 422 and body["error"]["code"] == "refund_exceeds_payment")

        # Replay: the same idempotency key as the first successful refund
        # (pr5) returns 200 with the identical body, not a second payment.
        status, body, _ = refund(client, bob, "p_1", {"amount": 50}, "pr5")
        check("replay of a committed refund key -> 200 with original body",
              status == 200 and body["payment_id"] == refund_id)
    finally:
        srv.shutdown()

    print()
    if FAILURES:
        print("%d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all probes passed")


if __name__ == "__main__":
    main()
