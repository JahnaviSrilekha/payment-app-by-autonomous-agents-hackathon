"""T31: refunds — POST /payments/{payment_id}/refunds (R294-R305, R332; design
section 26, A21). Every error is independently triggerable, in A21's order; a refund
is an ordinary opposite-direction payment (ADR-008) and leaves every linked object
untouched (R304, R332). T35's concurrency/money-invariant tests for refunds (R300,
R303: identical-key replay under load, conservation across refund chains at every
as_of/known_at) live here too."""

import sys
import unittest
from datetime import timedelta

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"


def refunds_fixture(operator=False, extra_authorizations=None):
    fixture = {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 8500},
            {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
             "display_name": "Carol", "handle": "carol", "balance": 10000},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "note": "coffee", "visibility": "public",
             "created_at": T0},
        ],
        "requests": [],
    }
    if operator:
        fixture["settlement_operator_ids"] = ["u_ada"]
    if extra_authorizations:
        fixture["authorizations"] = extra_authorizations
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def refund(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/refunds" % payment_id, body,
                          token=token, key=key)


class TestT31Refunds(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, refunds_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        self.carol = login(self.client, "carol@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def me(self, token):
        return self.client.request("GET", "/me", token=token)[1]

    def test_create_refund_201_shape(self):
        """R301: a refund is a new payment in the opposite direction with refund_of
        naming the target, null request/authorization linkage and the original
        note/visibility; it moves real money (R303)."""
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 2000}, key="r-1")
        self.assertEqual(status, 201, payload)
        self.assertTrue(payload["payment_id"].startswith("p_"))
        self.assertEqual(payload["from_user_id"], "u_bob")
        self.assertEqual(payload["from_handle"], "bob")
        self.assertEqual(payload["to_user_id"], "u_ada")
        self.assertEqual(payload["to_handle"], "ada")
        self.assertEqual(payload["amount"], 2000)
        self.assertEqual(payload["refund_of"], "p_1")
        self.assertIsNone(payload["request_id"])
        self.assertIsNone(payload["authorization_id"])
        self.assertIsNone(payload["settlement_id"])
        self.assertEqual(payload["note"], "coffee")
        self.assertEqual(payload["visibility"], "public")
        self.assertEqual((self.me(self.bob)["total"], self.me(self.ada)["total"]),
                         (6500, 12000))

    def test_replay_returns_200_original_body(self):
        """R302: a same-key replay returns the original body with 200 even after
        further refunds; a different body under the same key is 409."""
        _, original, _ = refund(self.client, self.bob, "p_1", {"amount": 1000},
                                key="r-idem")
        refund(self.client, self.bob, "p_1", {"amount": 500}, key="r-second")
        status, replay, _ = refund(self.client, self.bob, "p_1", {"amount": 1000},
                                   key="r-idem")
        self.assertEqual(status, 200)
        self.assertEqual(replay, original)
        self.assertEqual(self.me(self.bob)["total"], 7000)  # replayed, not re-run
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 2000}, key="r-idem")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_missing_key_is_400(self):
        """R294: a refund requires an idempotency key, before any body validation."""
        status, payload, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 100}, token=self.bob)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")
        status, payload, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {}, token=self.bob,
            with_key_header_empty=True)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")

    def test_unknown_payment_is_404(self):
        """R296."""
        status, payload, _ = refund(self.client, self.bob, "p_nope",
                                    {"amount": 100}, key="r-404")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_non_receiver_is_403(self):
        """R295: only the target's original receiver may refund — its sender and a
        third party both get 403."""
        for token in (self.ada, self.carol):
            status, payload, _ = refund(self.client, token, "p_1",
                                        {"amount": 100}, key="r-403-%s" % token)
            self.assertEqual(status, 403, token)
            self.assertEqual(payload["error"]["code"], "forbidden")

    def test_amount_shape_is_422(self):
        """R299: the same integer-range rule as every other amount field."""
        for amount in (-1, 0, "500", 1.5, True, 1000000001):
            status, payload, _ = refund(self.client, self.bob, "p_1",
                                        {"amount": amount}, key="r-shape-%r" % amount)
            self.assertEqual(status, 422, (amount, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")
            self.assertIn("amount", payload["error"]["message"])
        status, payload, _ = refund(self.client, self.bob, "p_1", {}, key="r-namt")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertIn("amount", payload["error"]["message"])

    def test_refund_of_refund_is_422_invalid_refund_target(self):
        """R297/R298: refunding a refund is 422 invalid_refund_target, no state
        change; refunding a request payment or capture stays possible (A23)."""
        _, first, _ = refund(self.client, self.bob, "p_1", {"amount": 1000},
                             key="r-first")
        before = self.client.request("GET", "/_test/export")[1]
        # a non-receiver of the refund payment is 403 first (A21's precedence)
        status, payload, _ = refund(self.client, self.bob, first["payment_id"],
                                    {"amount": 100}, key="r-refref")
        self.assertEqual(status, 403)
        # the refund payment's own receiver reaches the refund-of-refund check
        self.ada = login(self.client, "ada@example.com")
        before = self.client.request("GET", "/_test/export")[1]
        status, payload, _ = refund(self.client, self.ada, first["payment_id"],
                                    {"amount": 100}, key="r-refref2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "invalid_refund_target")
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)

    def test_cumulative_refunds_may_not_exceed_current_amount(self):
        """R300: cumulative refunds are capped by the payment's current corrected
        amount; a failing one changes nothing and the key stays free."""
        refund(self.client, self.bob, "p_1", {"amount": 4000}, key="r-c1")
        before = self.client.request("GET", "/_test/export")[1]
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 2500}, key="r-c2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)
        # the remaining 2000 exactly hits the cap (6000 total)
        status, _, _ = refund(self.client, self.bob, "p_1", {"amount": 2000},
                              key="r-c3")
        self.assertEqual(status, 201)

    def test_cap_is_the_current_corrected_amount(self):
        """R300/A22: "the payment's current corrected amount" is the latest
        revision's amount, so after a correction downward the refund cap follows."""
        status, _, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 5000, "reason": "overcharge",
             "effective_at": T1}, token=self.ada, key="c-cap")
        self.assertEqual(status, 201)
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 5500}, key="r-corr")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")
        status, _, _ = refund(self.client, self.bob, "p_1", {"amount": 5000},
                              key="r-corr2")
        self.assertEqual(status, 201)

    def test_insufficient_funds_against_available_not_balance(self):
        """R303: the refunder's available (balance minus open holds) is checked, not
        bare balance; a failing refund leaves every balance, record and idempotency
        key unchanged."""
        util.reset(self.client, {
            "currency": "EUR", "minor_units": 2,
            "users": [
                {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                 "display_name": "Ada", "handle": "ada", "balance": 10000},
                {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
                 "display_name": "Bob", "handle": "bob", "balance": 500},
            ],
            "payments": [
                {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 6000, "created_at": T0},
                {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 7500, "created_at": T1},
            ],
            "requests": [],
        })
        self.bob = login(self.client, "bob@example.com")
        # bob's balance itself cannot cover the refund: he paid his seed onward again
        before = self.client.request("GET", "/_test/export")[1]
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 1000}, key="r-po")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)
        # a refunder whose balance affords it but whose hold eats the headroom is
        # still rejected — the check is available, not balance
        util.reset(self.client, refunds_fixture(
            extra_authorizations=[
                {"id": "a_hold", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 4000, "status": "open", "created_at": T1,
                 "expires_at": "2099-01-01T00:00:00+00:00"},
            ]))
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        status, _, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 8000, "reason": "tip",
             "effective_at": T1}, token=self.ada, key="c-po")
        self.assertEqual(status, 201)  # bob's balance rises to 8500+2000=10500
        # bob: balance 10500 covers 8000, but available = 10500-4000 = 6500 does not
        status, payload, _ = refund(self.client, self.bob, "p_1",
                                    {"amount": 8000}, key="r-po2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")

    def test_refund_of_capture_leaves_authorization_untouched(self):
        """R304/A23: refunding a capture succeeds under the ordinary rules and never
        changes the parent authorization's status, captured_amount or payment_ids."""
        _, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 400},
            token=self.ada, key="a-1")
        _, capture, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=self.bob, key="cap-1")
        status, payload, _ = refund(self.client, self.bob, capture["payment_id"],
                                    {"amount": 400}, key="r-cap")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["refund_of"], capture["payment_id"])
        _, rows, _ = self.client.request("GET", "/authorizations", token=self.ada)
        row = rows["authorizations"][0]
        self.assertEqual(row["status"], "captured")
        self.assertEqual(row["captured_amount"], 400)
        self.assertEqual(len(row["payment_ids"]), 1)
        # refund moved the captured money back: ada 10000-400+400, bob 8500+400-400
        self.assertEqual((self.me(self.ada)["total"], self.me(self.bob)["total"]),
                         (10000, 8500))

    def test_refund_of_paid_request_leaves_request(self):
        """R304/A23: refunding a paid request's payment never reopens the request."""
        util.reset(self.client, {
            "currency": "EUR", "minor_units": 2,
            "users": [
                {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                 "display_name": "Ada", "handle": "ada", "balance": 10000},
                {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
                 "display_name": "Bob", "handle": "bob", "balance": 2500},
            ],
            "payments": [],
            "requests": [
                {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
                 "amount": 1200, "note": "taxi", "status": "pending",
                 "created_at": T0},
            ],
        })
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        _, paid, _ = self.client.request("POST", "/requests/rq_1/pay", {},
                                         token=self.ada, key="rq-pay")
        payment_id = paid["payment_id"]
        status, payload, _ = refund(self.client, self.bob, payment_id,
                                    {"amount": 1200}, key="r-rq")
        self.assertEqual(status, 201, payload)
        _, rows, _ = self.client.request("GET", "/requests", token=self.ada)
        paid_rows = [r for r in rows["requests"] if r["request_id"] == "rq_1"]
        self.assertEqual(len(paid_rows), 1)
        self.assertEqual(paid_rows[0]["status"], "paid")

    def test_refund_of_settlement_member(self):
        """R332: a settlement payment may be refunded, but the refund payment is not
        a settlement member and the original members keep their settlement_id."""
        util.reset(self.client, refunds_fixture(operator=True))
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [{"from_handle": "ada", "to_handle": "bob",
                            "amount": 300}]},
            token=self.ada, key="st-1")
        member_id = settlement["payments"][0]["payment_id"]
        status, payload, _ = refund(self.client, self.bob, member_id,
                                    {"amount": 300}, key="r-st")
        self.assertEqual(status, 201, payload)
        self.assertIsNone(payload["settlement_id"])
        self.assertEqual(payload["refund_of"], member_id)
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        rows = {p["payment_id"]: p for p in feed["payments"]}
        self.assertEqual(rows[member_id]["settlement_id"],
                         settlement["settlement_id"])
        self.assertIsNone(rows[payload["payment_id"]]["settlement_id"])

    def test_conservation_across_refund_and_correction(self):
        """R240 with the refund path in the mix: the seeded total is invariant."""
        expected = 28500
        refund(self.client, self.bob, "p_1", {"amount": 2000}, key="r-cons1")
        self.assertEqual(self.me(self.ada)["total"] + self.me(self.bob)["total"]
                         + self.me(self.carol)["total"], expected)
        _, payload, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 5000, "reason": "overcharge",
             "effective_at": T1}, token=self.ada, key="c-cons")
        self.assertEqual(payload["revision"], 2)
        # 2000 already refunded against the corrected 5000: exactly 3000 more fits
        status, payload2, _ = refund(self.client, self.bob, "p_1",
                                     {"amount": 3001}, key="r-cons2")
        self.assertEqual(status, 422)
        self.assertEqual(payload2["error"]["code"], "refund_exceeds_payment")
        status, _, _ = refund(self.client, self.bob, "p_1", {"amount": 3000},
                              key="r-cons3")
        self.assertEqual(status, 201)
        self.assertEqual(self.me(self.ada)["total"] + self.me(self.bob)["total"]
                         + self.me(self.carol)["total"], expected)


    def test_concurrent_identical_key_refunds_exactly_once(self):
        """T35/R300/R303 under load: two racing refunds with the same key and body
        give exactly one 201 and one 200-replay, the money moves once, and both
        callers see the same payment id."""
        results = util.concurrent(2, lambda i: refund(
            self.client, self.bob, "p_1", {"amount": 2000}, key="r-race"))
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [200, 201])
        self.assertEqual(results[0][1]["payment_id"], results[1][1]["payment_id"])
        self.assertEqual(self.me(self.bob)["total"], 8500 - 2000)
        self.assertEqual(self.me(self.ada)["total"], 10000 + 2000)
        refunded = [p for p in self.client.request("GET", "/activity",
                                                   token=self.ada)[1]["payments"]
                    if p["refund_of"] == "p_1"]
        self.assertEqual(len(refunded), 1)

    def test_refund_chain_never_negative_available_at_any_instant(self):
        """T35/R303: payment -> refund -> correction near the refunded boundary —
        at every (as_of, known_at) combination the response's available (total
        minus held) stays nonnegative for both parties."""
        util.reset(self.client, refunds_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        _, refund_response, _ = refund(self.client, self.bob, "p_1",
                                       {"amount": 2000}, key="r-chain")
        status, _, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 2000, "reason": "down to refunds",
             "effective_at": T1}, token=self.ada, key="c-chain")
        self.assertEqual(status, 201)
        refund_at = state_mod.parse_rfc3339(refund_response["created_at"])
        grid = {
            T0, T1,
            "2020-01-01T00:00:00Z",  # before everything: base balances only
            "2099-01-01T00:00:00Z",  # after everything
            refund_response["created_at"],
            (refund_at - timedelta(seconds=1)).isoformat(),
        }
        for as_of in grid:
            for known_at in grid | {None}:
                query = "?as_of=%s" % as_of.replace("+", "%2B")
                if known_at is not None:
                    query += "&known_at=%s" % known_at.replace("+", "%2B")
                for token in (self.ada, self.bob):
                    status, me, _ = self.client.request("GET", "/me" + query,
                                                        token=token)
                    self.assertEqual(status, 200, (as_of, known_at))
                    self.assertGreaterEqual(me["available"], 0, (as_of, known_at))
                    self.assertGreaterEqual(me["total"], 0, (as_of, known_at))
                    self.assertEqual(me["available"], me["total"] - me["held"])


if __name__ == "__main__":
    unittest.main()