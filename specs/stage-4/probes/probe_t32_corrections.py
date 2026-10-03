"""Reviewer black-box probe for stage-4 T32 (R306-R309; A24, A25).

Independent of stage-4/tests/test_t32_corrections.py: writes its own assertions,
focused on the two precedence decisions the developer flagged as judgment calls
rather than the straightforward single-check tests their own suite already has:

1. refund_exceeds_payment (A25) fires before stale_revision and before
   insufficient_funds, when more than one would otherwise fire.
2. linked_payment_immutable fires before refund_exceeds_payment, when a target
   is both immutable (already a capture) and would separately fail the
   refunded-floor check.
3. The effective_at-not-later-than-now check now fires after stale_revision
   (moved to match A26 phase 2's order, shared via validate_item) rather than
   immediately after parsing, as it did pre-T32.

Run: cd <checkout>/stage-4 && python3 <repo>/specs/stage-4/probes/probe_t32_corrections.py
"""
import os
import sys

STAGE4 = os.getcwd()
sys.path.insert(0, os.path.join(STAGE4, "tests"))
sys.path.insert(0, os.path.join(STAGE4, "src"))

import util  # noqa: E402

FAILURES = []


def check(label, cond, extra=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, label, (" -- " + extra) if extra and not cond else ""))
    if not cond:
        FAILURES.append(label)


def fixture():
    return {
        "currency": "EUR", "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 8500},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "note": "coffee", "visibility": "public",
             "created_at": "2026-01-01T10:00:00+00:00"},
        ],
        "requests": [],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                    {"email": email, "password": "correct horse"})
    return payload["token"]


def correct(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/corrections" % payment_id, body,
                           token=token, key=key)


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

        # Refund 1000 off p_1 (bob is the receiver), so refunded_total(p_1) = 1000.
        status, body, _ = refund(client, bob, "p_1", {"amount": 1000}, "setup-r1")
        check("setup: refund succeeds, refunded_total(p_1) becomes 1000", status == 201)

        # --- Decision/order check A: refund_exceeds_payment before stale_revision ---
        # expected_revision is wrong (stale, payment is still at revision 1) AND the
        # requested amount (500) is below refunded_total (1000): A25 says
        # refund_exceeds_payment (422) must win over stale_revision (409).
        status, body, _ = correct(client, ada, "p_1", {
            "expected_revision": 99, "amount": 500, "reason": "oops",
            "effective_at": "2026-01-01T10:00:00+00:00"}, "k-a")
        check("refund_exceeds_payment(422) precedes stale_revision(409)",
              status == 422 and body["error"]["code"] == "refund_exceeds_payment",
              "got %s %s" % (status, body))

        # --- Decision/order check B: refund_exceeds_payment boundary (R308) ---
        # amount == refunded_total (1000) is explicitly allowed by R308's wording
        # ("below its already-refunded amount") — equality is not "below".
        status, body, _ = correct(client, ada, "p_1", {
            "expected_revision": 1, "amount": 1000, "reason": "down to the floor",
            "effective_at": "2026-01-01T10:00:00+00:00"}, "k-b")
        check("amount == refunded_total is allowed (boundary, R308)",
              status == 201, "got %s %s" % (status, body))

        # --- Decision/order check C: linked_payment_immutable before
        # refund_exceeds_payment --- build a captured payment with its own
        # refund, so it is both immutable (authorization_id set) and separately
        # over its refunded floor; correcting it must cite immutability, not
        # refund_exceeds_payment.
        util.reset(client, fixture())
        ada = login(client, "ada@example.com")
        bob = login(client, "bob@example.com")
        _, authz, _ = client.request("POST", "/authorizations",
                                      {"to_handle": "bob", "amount": 2000},
                                      token=ada, key="authz-1")
        _, capture, _ = client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=bob, key="cap-1")
        capture_id = capture["payment_id"]
        status, body, _ = refund(client, bob, capture_id, {"amount": 500}, "r-cap")
        check("setup: capture refundable, refunded_total(capture) becomes 500",
              status == 201, "got %s %s" % (status, body))
        status, body, _ = correct(client, ada, capture_id, {
            "expected_revision": 1, "amount": 100, "reason": "below floor too",
            "effective_at": "2026-01-01T10:00:00+00:00"}, "k-c")
        check("linked_payment_immutable(422) precedes refund_exceeds_payment(422)",
              status == 422 and body["error"]["code"] == "linked_payment_immutable",
              "got %s %s" % (status, body))

        # --- Decision/order check D: effective_at-too-late after stale_revision ---
        # A future effective_at AND a stale expected_revision together: A26 phase
        # 2's order (now shared via validate_item) puts stale_revision (409)
        # before the effective_at check (422) — pre-T32 stage-3 code checked
        # effective_at immediately after parsing, so this ordering is new.
        util.reset(client, fixture())
        ada = login(client, "ada@example.com")
        future = "2099-01-01T00:00:00+00:00"
        status, body, _ = correct(client, ada, "p_1", {
            "expected_revision": 99, "amount": 500, "reason": "oops",
            "effective_at": future}, "k-d")
        check("stale_revision(409) precedes effective_at-too-late(422)",
              status == 409 and body["error"]["code"] == "stale_revision",
              "got %s %s" % (status, body))

        # Sanity: effective_at-too-late still fires on its own (not deleted).
        status, body, _ = correct(client, ada, "p_1", {
            "expected_revision": 1, "amount": 500, "reason": "oops",
            "effective_at": future}, "k-e")
        check("effective_at-too-late(422) still fires when nothing else is wrong",
              status == 422 and body["error"]["code"] == "validation_failed",
              "got %s %s" % (status, body))

        # R307: a refund payment itself cannot be corrected.
        util.reset(client, fixture())
        ada = login(client, "ada@example.com")
        bob = login(client, "bob@example.com")
        _, refund_p, _ = refund(client, bob, "p_1", {"amount": 500}, "r-x")
        status, body, _ = correct(client, bob, refund_p["payment_id"], {
            "expected_revision": 1, "amount": 100, "reason": "nope",
            "effective_at": "2026-01-01T10:00:00+00:00"}, "k-f")
        check("correcting a refund payment itself -> 422 linked_payment_immutable",
              status == 422 and body["error"]["code"] == "linked_payment_immutable",
              "got %s %s" % (status, body))
    finally:
        srv.shutdown()

    print()
    if FAILURES:
        print("%d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all probes passed")


if __name__ == "__main__":
    main()
