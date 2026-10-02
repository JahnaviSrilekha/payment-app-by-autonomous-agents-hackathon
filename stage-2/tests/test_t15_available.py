"""T15: every stage-1 affordability check (POST /payments, POST /requests/{id}/pay,
settlement net debits) now reads available() — held funds are unspendable; with zero
open holds results are byte-for-byte stage-1 (R145, R148, R149, R151, R192)."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


def hold_fixture(hold_amount=6000):
    fixture = util.spec_fixture()
    fixture["payments"] = []
    fixture["requests"] = []
    fixture["settlement_operator_ids"] = ["u_ada"]
    fixture["authorizations"] = [
        {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": hold_amount, "status": "open",
         "expires_at": "2099-01-01T00:00:00+00:00"}]
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    assert payload and "token" in payload, payload
    return payload["token"]


class TestT15Available(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, hold_fixture())  # ada: total 10000, held 6000, avail 4000
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def baseline(self):
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (10000, 6000, 4000))

    def test_payment_above_available_refused_even_below_balance(self):
        """available 4000 < 4001 <= total 10000: refused (held funds unspendable)."""
        self.baseline()
        status, payload, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 4001},
            token=self.ada, key="pay-over")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        status, payment, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 4000},
            token=self.ada, key="pay-exact")
        self.assertEqual(status, 201)  # exactly available works

    def test_request_pay_above_available_refused(self):
        self.baseline()
        _, request, _ = self.client.request(
            "POST", "/requests", {"payer_handle": "ada", "amount": 4001,
                                  "note": "rent"}, token=self.bob, key="rq-1")
        status, payload, _ = self.client.request(
            "POST", "/requests/%s/pay" % request["request_id"], {},
            token=self.ada, key="rq-pay-1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        _, request2, _ = self.client.request(
            "POST", "/requests", {"payer_handle": "ada", "amount": 4000},
            token=self.bob, key="rq-2")
        status, payment, _ = self.client.request(
            "POST", "/requests/%s/pay" % request2["request_id"], {},
            token=self.ada, key="rq-pay-2")
        self.assertEqual(status, 201)

    def test_settlement_net_debit_above_available_refused(self):
        self.baseline()
        transfers = [{"from_handle": "ada", "to_handle": "bob", "amount": 4001}]
        status, payload, _ = self.client.request(
            "POST", "/settlements", {"transfers": transfers}, token=self.ada,
            key="st-over")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        transfers = [{"from_handle": "ada", "to_handle": "bob", "amount": 4000}]
        status, settlement, _ = self.client.request(
            "POST", "/settlements", {"transfers": transfers}, token=self.ada,
            key="st-exact")
        self.assertEqual(status, 201)

    def test_new_authorization_above_available_refused(self):
        self.baseline()
        status, payload, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 4001},
            token=self.ada, key="auth-over")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")

    def test_captures_may_spend_the_money_reserved_for_them(self):
        """After spending available on payments, capturing the hold still moves the
        reserved money (R145: captures may spend what is reserved for them)."""
        self.client.request("POST", "/payments", {"to_handle": "bob", "amount": 4000},
                            token=self.ada, key="pay-exact")
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["available"]), (6000, 0))  # all spent
        status, payment, _ = self.client.request(
            "POST", "/authorizations/a_hold/capture", {}, token=self.bob,
            key="cap-reserved")
        self.assertEqual(status, 201)
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["held"], ada["available"]), (0, 0, 0))
        bob = self.client.request("GET", "/me", token=self.bob)[1]
        self.assertEqual(bob["total"], 2500 + 4000 + 6000)

    def test_splits_post_is_unchanged(self):
        """R151: POST /splits itself never checked affordability in stage 1 (money
        moves only when the created requests are paid); unchanged with holds."""
        self.baseline()
        status, split, _ = self.client.request(
            "POST", "/splits", {"amount": 8000,
                                "participant_handles": ["ada", "bob"]},
            token=self.ada, key="split-1")
        self.assertEqual(status, 201)  # no hold-phase, no affordability check at split
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (10000, 6000, 4000))
        # paying the created request still goes through available (R149 path)
        request_id = split["requests"][0]["request_id"]  # bob's share: 4000
        self.assertEqual(split["requests"][0]["amount"], 4000)
        status, payload, _ = self.client.request(
            "POST", "/requests/%s/pay" % request_id, {}, token=self.bob, key="sp-1")
        self.assertEqual(status, 409)  # bob only has 2500 available
        self.assertEqual(payload["error"]["code"], "insufficient_funds")

    def test_zero_hold_regression_stage1_unchanged(self):
        """With no open holds every stage-1 result is byte-for-byte unchanged."""
        util.reset(self.client)  # the plain spec fixture: no authorizations
        ada = login(self.client, "ada@example.com")
        bob = login(self.client, "bob@example.com")
        me = self.client.request("GET", "/me", token=ada)[1]
        self.assertEqual((me["balance"], me["total"], me["available"], me["held"]),
                         (10000, 10000, 10000, 0))
        status, payment, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 10001},
            token=ada, key="z1")
        self.assertEqual((status, payment["error"]["code"]),
                         (409, "insufficient_funds"))
        status, payment, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 10000},
            token=ada, key="z2")
        self.assertEqual(status, 201)
        _, me, _ = self.client.request("GET", "/me", token=ada)
        self.assertEqual((me["balance"], me["total"], me["available"], me["held"]),
                         (0, 0, 0, 0))

    def test_mixed_concurrency_hold_payment_capture_never_negative(self):
        """Storm: while a 6000 hold is open, concurrent payments of 1000 race the
        4000 available; the hold is then captured. At every observable point
        available == total - held >= 0 and totals conserve (R192)."""
        def pay(i):
            return self.client.request(
                "POST", "/payments", {"to_handle": "bob", "amount": 1000},
                token=self.ada, key="mix-%d" % i)

        results = util.concurrent(20, pay)
        moved = [p for s, p, _ in results if s == 201]
        self.assertEqual(len(moved), 4)  # exactly available allows four
        self.assertEqual(sum(p["amount"] for p in moved), 4000)
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["held"], ada["available"]), (6000, 6000, 0))
        status, _, _ = self.client.request(
            "POST", "/authorizations/a_hold/capture", {}, token=self.bob,
            key="mix-capture")
        self.assertEqual(status, 201)
        ada = self.client.request("GET", "/me", token=self.ada)[1]
        self.assertEqual((ada["total"], ada["held"], ada["available"]), (0, 0, 0))
        bob = self.client.request("GET", "/me", token=self.bob)[1]
        self.assertEqual(bob["total"], 2500 + 10000)  # 4000 payments + 6000 capture


if __name__ == "__main__":
    unittest.main()