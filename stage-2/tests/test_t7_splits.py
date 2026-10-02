"""T7: splits (R29, R78-R80, R82-R84). The stage-1 rounding table row by row, share
ordering, caller-only splits, zero shares, reordering effects, feed isolation."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import splits  # noqa: E402


class TestSplitSharesPure(unittest.TestCase):
    def test_stage1_table(self):
        cases = [
            (1000, 3, [334, 333, 333]),
            (1, 3, [1, 0, 0]),
            (10, 3, [4, 3, 3]),
            (999, 3, [333, 333, 333]),
            (5, 5, [1, 1, 1, 1, 1]),
        ]
        for amount, count, expected in cases:
            self.assertEqual(splits.split_shares(amount, count), expected, (amount, count))

    def test_properties(self):
        for amount in range(1, 300):
            for count in range(1, 12):
                shares = splits.split_shares(amount, count)
                self.assertEqual(sum(shares), amount, (amount, count))
                self.assertEqual(max(shares) - min(shares) <= 1, True, (amount, count))
                self.assertTrue(all(s >= 0 for s in shares))
                # first participants get the extra units
                extra = amount % count
                for i, share in enumerate(shares):
                    self.assertEqual(share, amount // count + (1 if i < extra else 0))


class TestT7SplitsHttp(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset_clean(self.client)
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

    def _signup(self, email, display_name="C"):
        _, payload, _ = self.client.request("POST", "/auth/signup",
                                            {"email": email, "password": "correct horse",
                                             "display_name": display_name})
        return payload

    def test_spec_example(self):
        # bob splits 3000 among ada, bob, cy (bob is the caller and is included)
        _, cy, _ = self.client.request("POST", "/auth/signup",
                                       {"email": "cy@x.com", "password": "correct horse",
                                        "display_name": "Cy"})
        status, payload, _ = self.client.request(
            "POST", "/splits",
            {"amount": 3000, "participant_handles": ["ada", "bob", "cy"], "note": "dinner"},
            token=self.bob, key="sp1")
        self.assertEqual(status, 201)
        self.assertEqual([s["amount"] for s in payload["shares"]], [1000, 1000, 1000])
        self.assertEqual([s["handle"] for s in payload["shares"]], ["ada", "bob", "cy"])
        # one request per participant except the caller, in the same order
        self.assertEqual([r["payer_handle"] for r in payload["requests"]], ["ada", "cy"])
        self.assertEqual([r["requester_handle"] for r in payload["requests"]], ["bob", "bob"])
        self.assertTrue(all(r["status"] == "pending" for r in payload["requests"]))
        self.assertTrue(all(r["amount"] == 1000 for r in payload["requests"]))
        self.assertIn("split_id", payload)
        self.assertEqual(payload["amount"], 3000)
        self.assertEqual(payload["note"], "dinner")
        self.assertEqual(payload["currency"], "EUR")
        self.assertIn("created_at", payload)
        # the two created requests are visible to their own two parties only
        _, listing, _ = self.client.request("GET", "/requests", token=self.ada)
        self.assertEqual(len(listing["requests"]), 1)
        _, listing, _ = self.client.request("GET", "/requests", token=cy["token"])
        self.assertEqual(len(listing["requests"]), 1)

    def test_rounding_table_over_http(self):
        for handle in ("cy", "one", "two"):
            self.client.request("POST", "/auth/signup",
                                {"email": handle + "@x.com", "password": "correct horse",
                                 "display_name": handle})
        for amount, expected in ((1000, [334, 333, 333]), (1, [1, 0, 0]),
                                 (10, [4, 3, 3]), (999, [333, 333, 333]),
                                 (5, [1, 1, 1, 1, 1])):
            participants = (["ada", "bob", "cy"] if len(expected) == 3
                            else ["ada", "bob", "cy", "one", "two"])
            status, payload, _ = self.client.request(
                "POST", "/splits",
                {"amount": amount, "participant_handles": participants},
                token=self.bob, key="rt-%d-%d" % (amount, len(participants)))
            self.assertEqual(status, 201, (amount, payload))
            self.assertEqual([s["amount"] for s in payload["shares"]], expected, amount)

    def test_caller_omitted_gets_no_share(self):
        # ada (caller) splits among bob and cy only: two shares summing to amount
        self.client.request("POST", "/auth/signup",
                            {"email": "cy@x.com", "password": "correct horse",
                             "display_name": "Cy"})
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 10, "participant_handles": ["bob", "cy"]},
            token=self.ada, key="om1")
        self.assertEqual(status, 201)
        self.assertEqual([s["amount"] for s in payload["shares"]], [5, 5])
        self.assertEqual([s["handle"] for s in payload["shares"]], ["bob", "cy"])
        self.assertEqual([r["payer_handle"] for r in payload["requests"]], ["bob", "cy"])
        self.assertEqual(sum(s["amount"] for s in payload["shares"]), 10)

    def test_caller_only_split_creates_no_requests(self):
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 500, "participant_handles": ["ada"]},
            token=self.ada, key="only1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["shares"], [{"handle": "ada", "amount": 500}])
        self.assertEqual(payload["requests"], [])

    def test_zero_share_still_produces_request(self):
        # 1 minor unit among three participants: shares 1,0,0 -> two requests of 0
        self.client.request("POST", "/auth/signup",
                            {"email": "cy@x.com", "password": "correct horse",
                             "display_name": "Cy"})
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 1, "participant_handles": ["ada", "bob", "cy"]},
            token=self.ada, key="z1")
        self.assertEqual(status, 201)
        self.assertEqual([s["amount"] for s in payload["shares"]], [1, 0, 0])
        self.assertEqual(len(payload["requests"]), 2)
        self.assertEqual([r["amount"] for r in payload["requests"]], [0, 0])
        # the zero-share request is real: visible to its payer and payable? 0-amount
        # payment is legal in lifecycle terms (request for 0)
        rid = payload["requests"][0]["request_id"]
        status, payment, _ = self.client.request("POST", "/requests/%s/pay" % rid, {},
                                                 token=self.bob, key="z1-pay")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 0)

    def test_reordering_moves_the_extra_unit(self):
        # 11 among two people does not divide: the FIRST participant in the given order
        # gets the extra unit, so reordering moves it (R83)
        for order, expected in ((["ada", "bob"], [6, 5]), (["bob", "ada"], [6, 5])):
            status, payload, _ = self.client.request(
                "POST", "/splits", {"amount": 11, "participant_handles": order},
                token=self.ada, key="ro-%s" % "-".join(order))
            self.assertEqual(status, 201)
            self.assertEqual([s["handle"] for s in payload["shares"]], order)
            self.assertEqual([s["amount"] for s in payload["shares"]], expected)
        by_handle = {}
        for order in (["ada", "bob"], ["bob", "ada"]):
            _, payload, _ = self.client.request("POST", "/splits",
                                                {"amount": 11, "participant_handles": order},
                                                token=self.ada,
                                                key="ro2-%s" % "-".join(order))
            for share in payload["shares"]:
                by_handle.setdefault(share["handle"], set()).add(share["amount"])
        self.assertEqual(by_handle["ada"], {5, 6})  # the extra unit moved with the order
        self.assertEqual(by_handle["bob"], {5, 6})
        # the splits are independent (R84): conservation still exact
        _, export, _ = self.client.request("GET", "/_test/export")
        total = sum(u["balance"] for u in export["state"]["users"].values())
        self.assertEqual(total, 12500)

    def test_no_balance_checks_anywhere(self):
        # ada is the requester; payer bob owes shares regardless of anyone's balance
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 1000000000, "participant_handles": ["bob"]},
            token=self.ada, key="nb1")
        self.assertEqual(status, 201)
        _, listing, _ = self.client.request("GET", "/requests", token=self.bob)
        self.assertEqual(listing["requests"][0]["amount"], 1000000000)

    def test_error_table(self):
        cases = [
            ({"participant_handles": ["bob"]}, 422, "validation_failed", "missing amount"),
            ({"amount": 0, "participant_handles": ["bob"]}, 422, "validation_failed", "zero"),
            ({"amount": -1, "participant_handles": ["bob"]}, 422, "validation_failed", "neg"),
            ({"amount": 1000000001, "participant_handles": ["bob"]}, 422, "validation_failed", "over"),
            ({"amount": "5", "participant_handles": ["bob"]}, 422, "validation_failed", "string"),
            ({"amount": 5, "participant_handles": []}, 422, "validation_failed", "empty"),
            ({"amount": 5, "participant_handles": ["bob", "bob"]}, 422, "validation_failed", "dup"),
            ({"amount": 5, "participant_handles": ["ghost"]}, 404, "not_found", "unknown"),
            # participant_handles carries endpoint-specific rules (R79), so wrong JSON
            # types, including explicit null, are 422 validation_failed (R41)
            ({"amount": 5, "participant_handles": "bob"}, 422, "validation_failed", "not array"),
            ({"amount": 5, "participant_handles": None}, 422, "validation_failed", "null"),
            ({"amount": 5}, 422, "validation_failed", "missing"),
            # field validation (note length) precedes the resource check (unknown handle),
            # matching POST /payments and POST /requests
            ({"amount": 100, "participant_handles": ["nobody"], "note": "x" * 201}, 422,
             "validation_failed", "unknown+note"),
            ({"amount": 5, "participant_handles": ["bob"], "note": "x" * 201}, 422,
             "validation_failed", "note"),
        ]
        for body, status, code, label in cases:
            _, payload, _ = self.client.request("POST", "/splits", body,
                                                token=self.ada, key="sperr-" + label)
            self.assertEqual(payload["error"]["code"], code, (label, payload))

    def test_split_not_in_activity(self):
        self.client.request("POST", "/splits",
                            {"amount": 30, "participant_handles": ["ada", "bob"]},
                            token=self.ada, key="act1")
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed["payments"], [])  # the split itself is not a feed item

    def test_split_requests_then_pay_conserves_total(self):
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 30, "participant_handles": ["ada", "bob"]},
            token=self.ada, key="pay1")
        for request in payload["requests"]:
            status, payment, _ = self.client.request("POST", "/requests/%s/pay" %
                                                     request["request_id"], {},
                                                     token=self.bob, key="pay-%s" % request["request_id"])
            self.assertEqual(status, 201)
        _, export, _ = self.client.request("GET", "/_test/export")
        users = {u["handle"]: u["balance"] for u in export["state"]["users"].values()}
        self.assertEqual(sum(users.values()), 12500)  # conservation after splits paid (R84)
        # bob's 15 share moved to ada (the split itself moves no money)
        self.assertEqual(users["ada"], 10000 + 15)
        self.assertEqual(users["bob"], 2500 - 15)

    def test_idempotent_replay_and_reuse(self):
        body = {"amount": 30, "participant_handles": ["ada", "bob"]}
        status, first, _ = self.client.request("POST", "/splits", body,
                                               token=self.ada, key="idem1")
        self.assertEqual(status, 201)
        status, replay, _ = self.client.request("POST", "/splits", body,
                                                token=self.ada, key="idem1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        # exactly one pending request exists for bob (no duplicates from the replay)
        _, listing, _ = self.client.request("GET", "/requests?direction=outgoing",
                                            token=self.ada)
        self.assertEqual(len(listing["requests"]), 1)
        status, payload, _ = self.client.request("POST", "/splits",
                                                 {"amount": 31, "participant_handles": ["ada", "bob"]},
                                                 token=self.ada, key="idem1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_minor_units_zero(self):
        fixture = {"currency": "JPY", "minor_units": 0,
                   "users": [
                       {"id": "u_a", "email": "a@x.com", "password": "correct horse",
                        "display_name": "A", "handle": "a", "balance": 10},
                       {"id": "u_b", "email": "b@x.com", "password": "correct horse",
                        "display_name": "B", "handle": "b", "balance": 0},
                   ], "payments": [], "requests": []}
        self.client.request("POST", "/_test/reset", fixture)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "a@x.com", "password": "correct horse"})
        status, payload, _ = self.client.request(
            "POST", "/splits", {"amount": 10, "participant_handles": ["a", "b"]},
            token=login["token"], key="jpy1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["currency"], "JPY")
        self.assertEqual([s["amount"] for s in payload["shares"]], [5, 5])


if __name__ == "__main__":
    unittest.main()