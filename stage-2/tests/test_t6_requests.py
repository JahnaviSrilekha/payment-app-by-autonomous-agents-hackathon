"""T6: request lifecycle (R24, R25, R28, R69-R77): create without balance check, pay
(including insufficient-funds 409 leaving state unchanged and replay-after-paid), decline,
cancel, double-decline/double-cancel success, wrong-caller 403, non-pending 409, GET
/requests filters and pagination, requests scoped to their two parties."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


class TestT6Requests(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")

    def _login(self, handle):
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": handle + "@example.com",
                                             "password": "correct horse"})
        return payload["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _create(self, token, payer_handle, amount=1200, note="taxi", key=None):
        return self.client.request("POST", "/requests",
                                   {"payer_handle": payer_handle, "amount": amount,
                                    "note": note},
                                   token=token, key=key or "cr-%s-%d" % (payer_handle, amount))

    def test_create_request_example(self):
        status, payload, _ = self._create(self.bob, "ada", key="ex1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["requester_handle"], "bob")
        self.assertEqual(payload["payer_handle"], "ada")
        self.assertEqual(payload["requester_id"], "u_bob")
        self.assertEqual(payload["payer_id"], "u_ada")
        self.assertEqual(payload["amount"], 1200)
        self.assertEqual(payload["note"], "taxi")
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["payment_id"], None)
        self.assertIn("created_at", payload)

    def test_create_errors(self):
        cases = [
            ("ada", 0, "validation_failed", "zero"),
            ("ada", -1, "validation_failed", "negative"),
            ("ada", 1000000001, "validation_failed", "over"),
            ("ada", "x", "validation_failed", "string amount"),
            ("bob", 5, "self_request", "self"),
            ("ghost", 5, "not_found", "unknown"),
            ("ada", 5, "validation_failed", "note"),
        ]
        for payer, amount, code, label in cases:
            body = {"payer_handle": payer, "amount": amount}
            if label == "note":
                body["note"] = "x" * 201
            _, payload, _ = self.client.request("POST", "/requests", body,
                                                token=self.bob, key="cerr-" + label)
            self.assertEqual(payload["error"]["code"], code, (label, payload))

    def test_payers_balance_not_checked_at_creation(self):
        status, payload, _ = self._create(self.bob, "ada", amount=999999999, key="big")
        self.assertEqual(status, 201)
        self.assertEqual(payload["status"], "pending")

    def test_pay_lifecycle(self):
        _, rq, _ = self._create(self.bob, "ada", amount=300, key="lc1")
        status, payment, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                                 {"visibility": "private"},
                                                 token=self.ada, key="pay-lc1")
        self.assertEqual(status, 201)
        self.assertEqual(payment["request_id"], rq["request_id"])
        self.assertEqual(payment["to_handle"], "bob")
        self.assertEqual(payment["from_handle"], "ada")
        self.assertEqual(payment["visibility"], "private")
        self.assertEqual(payment["amount"], 300)
        # request now paid and carries the payment id
        _, listing, _ = self.client.request("GET", "/requests?status=paid", token=self.ada)
        self.assertEqual(listing["requests"][0]["status"], "paid")
        self.assertEqual(listing["requests"][0]["payment_id"], payment["payment_id"])
        # balances moved
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 - 300)
        _, me, _ = self.client.request("GET", "/me", token=self.bob)
        self.assertEqual(me["balance"], 2500 + 300)

    def test_pay_errors(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        _, rq, _ = self._create(self.bob, "ada", amount=20000, key="pe1")
        rid = rq["request_id"]
        # unknown request first
        _, payload, _ = self.client.request("POST", "/requests/rq_nope/pay", {},
                                            token=self.ada, key="pe-u")
        self.assertEqual(payload["error"]["code"], "not_found")
        # caller not the payer
        _, payload, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                            token=self.bob, key="pe-f")
        self.assertEqual(payload["error"]["code"], "forbidden")
        # insufficient funds: ada holds 10000 < 20000
        _, payload, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                            token=self.ada, key="pe-i")
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        # nothing changed: still pending, still payable when money arrives
        _, listing, _ = self.client.request("GET", "/requests?status=pending", token=self.bob)
        self.assertEqual(len(listing["requests"]), 1)
        # top ada up via a public payment from bob? bob only has 2500; instead pay in two
        # steps: request 20000 stays pending while ada cannot pay (R25)
        status, payload, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                                 token=self.ada, key="pe-2")
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        # a failed attempt freed the key: now with a smaller request ada pays
        _, small, _ = self._create(self.bob, "ada", amount=50, key="pe-small")
        status, _, _ = self.client.request("POST", "/requests/%s/pay" % small["request_id"],
                                           {}, token=self.ada, key="pe-3")
        self.assertEqual(status, 201)

    def test_money_arrives_then_request_payable(self):
        _, rq, _ = self._create(self.bob, "ada", amount=11000, key="ma1")
        status, payload, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                                 {}, token=self.ada, key="ma-pay")
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        # carol pays ada... carol is new with 0; instead create carol rich via fixture? Use
        # the seeded pair: bob cannot fund ada (only 2500). Simplest: pay ada from bob what
        # he can, still short; then verify the request is still pending and payable after
        # ada receives enough. bob sends 2000 to ada.
        self.client.request("POST", "/payments", {"to_handle": "ada", "amount": 2000},
                            token=self.bob, key="ma-top")
        status, payment, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                                 {}, token=self.ada, key="ma-pay2")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 11000)

    def test_replay_after_paid_returns_original_payment(self):
        _, rq, _ = self._create(self.bob, "ada", amount=100, key="ra1")
        status, payment, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                                 {"visibility": "private"},
                                                 token=self.ada, key="ra-pay")
        self.assertEqual(status, 201)
        status, replay, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                                {"visibility": "private"},
                                                token=self.ada, key="ra-pay")
        self.assertEqual(status, 200)
        self.assertEqual(replay, payment)
        # balances moved once
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 - 100)
        # a different visibility body with the same key is a reuse
        _, payload, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                            {"visibility": "public"},
                                            token=self.ada, key="ra-pay")
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_pay_empty_body_vs_visibility_default_are_different_values(self):
        _, rq, _ = self._create(self.bob, "ada", amount=10, key="eb1")
        status, first, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                               {}, token=self.ada, key="eb-pay")
        self.assertEqual(status, 201)
        status, payload, _ = self.client.request("POST", "/requests/%s/pay" % rq["request_id"],
                                            {"visibility": "public"},
                                            token=self.ada, key="eb-pay")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_decline_and_cancel(self):
        _, rq, _ = self._create(self.bob, "ada", amount=20, key="dc1")
        rid = rq["request_id"]
        # not the payer / not the requester
        _, payload, _ = self.client.request("POST", "/requests/%s/decline" % rid, {},
                                            token=self.bob, key=None)
        self.assertEqual(payload["error"]["code"], "forbidden")
        _, payload, _ = self.client.request("POST", "/requests/%s/cancel" % rid, {},
                                            token=self.ada, key=None)
        self.assertEqual(payload["error"]["code"], "forbidden")
        # payer declines
        status, payload, _ = self.client.request("POST", "/requests/%s/decline" % rid, {},
                                                 token=self.ada, key=None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "declined")
        # double decline is 200 with current state
        status, payload, _ = self.client.request("POST", "/requests/%s/decline" % rid, {},
                                                 token=self.ada, key=None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "declined")
        # a declined request cannot be paid or cancelled
        _, payload, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                            token=self.ada, key="dc-pay")
        self.assertEqual(payload["error"]["code"], "request_not_pending")
        _, payload, _ = self.client.request("POST", "/requests/%s/cancel" % rid, {},
                                            token=self.bob, key=None)
        self.assertEqual(payload["error"]["code"], "request_not_pending")

    def test_cancel_lifecycle(self):
        _, rq, _ = self._create(self.bob, "ada", amount=20, key="cl1")
        rid = rq["request_id"]
        status, payload, _ = self.client.request("POST", "/requests/%s/cancel" % rid, {},
                                                 token=self.bob, key=None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "cancelled")
        status, payload, _ = self.client.request("POST", "/requests/%s/cancel" % rid, {},
                                                 token=self.bob, key=None)
        self.assertEqual(status, 200)
        _, payload, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                            token=self.ada, key="cl-pay")
        self.assertEqual(payload["error"]["code"], "request_not_pending")

    def test_cancel_paid_is_409(self):
        _, rq, _ = self._create(self.bob, "ada", amount=10, key="cp1")
        self.client.request("POST", "/requests/%s/pay" % rq["request_id"], {},
                            token=self.ada, key="cp-pay")
        _, payload, _ = self.client.request("POST", "/requests/%s/cancel" % rq["request_id"],
                                            {}, token=self.bob, key=None)
        self.assertEqual(payload["error"]["code"], "request_not_pending")

    def test_requests_scoped_to_two_parties(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        self._create(self.bob, "ada", amount=10, key="sc1")
        _, carol, _ = self.client.request("POST", "/auth/signup",
                                          {"email": "c@x.com", "password": "correct horse",
                                           "display_name": "C"})
        _, listing, _ = self.client.request("GET", "/requests", token=carol["token"])
        self.assertEqual(listing["requests"], [])
        self.assertEqual(listing["has_more"], False)
        _, listing, _ = self.client.request("GET", "/requests", token=self.ada)
        self.assertEqual(len(listing["requests"]), 1)
        _, listing, _ = self.client.request("GET", "/requests", token=self.bob)
        self.assertEqual(len(listing["requests"]), 1)

    def test_direction_and_status_filters(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        self._create(self.bob, "ada", amount=10, key="f1")   # bob requests from ada
        _, carol, _ = self.client.request("POST", "/auth/signup",
                                          {"email": "c2@x.com", "password": "correct horse",
                                           "display_name": "C"})
        self.client.request("POST", "/payments", {"to_handle": "bob", "amount": 100},
                            token=self.ada, key="f0")
        self.client.request("POST", "/requests", {"payer_handle": "bob", "amount": 30},
                            token=carol["token"], key="f2")  # carol requests from bob
        # pay carol's request so its status becomes paid
        _, listing, _ = self.client.request("GET", "/requests?direction=incoming&status=pending",
                                            token=self.bob)
        rid = listing["requests"][0]["request_id"]  # newest first: carol's request
        self.client.request("POST", "/requests/%s/pay" % rid, {}, token=self.bob, key="f3")
        _, listing, _ = self.client.request("GET", "/requests?direction=incoming", token=self.bob)
        self.assertEqual([r["status"] for r in listing["requests"]], ["paid"])
        _, listing, _ = self.client.request("GET", "/requests?direction=outgoing", token=self.bob)
        self.assertEqual([r["payer_handle"] for r in listing["requests"]], ["ada"])
        self.assertEqual([r["status"] for r in listing["requests"]], ["pending"])
        for query in ("direction=both", "direction=IN", "status=unknown", "status="):
            status, payload, _ = self.client.request("GET", "/requests?" + query,
                                                     token=self.bob)
            self.assertEqual(status, 422, query)
            self.assertEqual(payload["error"]["code"], "validation_failed", query)

    def test_pagination(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        for i in range(7):
            self._create(self.bob, "ada", amount=1 + i, key="pgd-%d" % i)
        _, page, _ = self.client.request("GET", "/requests?limit=3&offset=0", token=self.bob)
        self.assertEqual(len(page["requests"]), 3)
        self.assertTrue(page["has_more"])
        amounts = [r["amount"] for r in page["requests"]]
        self.assertEqual(amounts, [7, 6, 5])  # newest first
        _, page, _ = self.client.request("GET", "/requests?limit=3&offset=6", token=self.bob)
        self.assertEqual(len(page["requests"]), 1)
        self.assertFalse(page["has_more"])
        _, page, _ = self.client.request("GET", "/requests?limit=200&offset=0", token=self.ada)
        self.assertEqual(len(page["requests"]), 7)

    def test_concurrent_pays_only_one_wins(self):
        _, rq, _ = self._create(self.bob, "ada", amount=40, key="cc1")
        rid = rq["request_id"]

        def pay(i):
            return self.client.request("POST", "/requests/%s/pay" % rid, {},
                                       token=self.ada, key="cc-pay")[0]

        statuses = util.concurrent(12, pay)
        self.assertEqual(len([s for s in statuses if s == 201]), 1)
        self.assertEqual(len([s for s in statuses if s == 200]), 11)
        self.assertFalse([s for s in statuses if s >= 500])
        _, listing, _ = self.client.request("GET", "/requests?status=paid", token=self.ada)
        self.assertEqual(len(listing["requests"]), 1)
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 - 40)  # moved once


if __name__ == "__main__":
    unittest.main()