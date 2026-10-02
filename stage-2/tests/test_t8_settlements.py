"""T8: atomic net settlements (R94-R101). Operator gating, entry-error precedence in
input order before insufficient-funds, collective affordability, all-or-nothing commit,
replays, constituent receipts and feed visibility."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


def operator_fixture():
    fixture = util.spec_fixture()
    fixture["settlement_operator_ids"] = ["u_ada"]
    fixture["payments"] = []
    fixture["requests"] = []
    return fixture


class TestT8Settlements(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, operator_fixture())
        self.ada = self._login("ada")   # operator
        self.bob = self._login("bob")   # plain user

    def _login(self, handle):
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": handle + "@example.com",
                                             "password": "correct horse"})
        return payload["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _settle(self, token, transfers, key="st-1"):
        return self.client.request("POST", "/settlements", {"transfers": transfers},
                                   token=token, key=key)

    def test_no_token_401_non_operator_403(self):
        status, payload, _ = self.client.request("POST", "/settlements",
                                                 {"transfers": [{"from_handle": "ada",
                                                                 "to_handle": "bob",
                                                                 "amount": 10}]})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthenticated")
        status, payload, _ = self._settle(self.bob,
                                          [{"from_handle": "ada", "to_handle": "bob",
                                            "amount": 10}], key="auth1")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")
        # a 403 claims no idempotency key: the operator can now use the same key
        status, payload, _ = self._settle(self.ada,
                                          [{"from_handle": "ada", "to_handle": "bob",
                                            "amount": 10}], key="auth1")
        self.assertEqual(status, 201)

    def test_operator_can_settle_and_receipt_shape(self):
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 100},
             {"from_handle": "bob", "to_handle": "ada", "amount": 50}],
            key="ok1")
        self.assertEqual(status, 201)
        self.assertIn("settlement_id", payload)
        self.assertIn("committed_at", payload)
        self.assertEqual(len(payload["payments"]), 2)
        first, second = payload["payments"]
        self.assertEqual((first["from_handle"], first["to_handle"], first["amount"]),
                         ("ada", "bob", 100))
        self.assertEqual((second["from_handle"], second["to_handle"], second["amount"]),
                         ("bob", "ada", 50))
        self.assertTrue(all(p["settlement_id"] == payload["settlement_id"]
                            for p in payload["payments"]))
        self.assertTrue(all(p["request_id"] is None for p in payload["payments"]))
        self.assertTrue(all(p["created_at"] == payload["committed_at"]
                            for p in payload["payments"]))
        _, export, _ = self.client.request("GET", "/_test/export")
        settled = [p for p in export["state"]["payments"] if p["settlement_id"]]
        self.assertEqual(len(settled), 2)
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 - 100 + 50)  # net -50
        _, me, _ = self.client.request("GET", "/me", token=self.bob)
        self.assertEqual(me["balance"], 2500 + 100 - 50)

    def test_entry_errors_take_precedence_in_input_order(self):
        # entry 1 would be affordable; entry 2 has an unknown handle -> 404, not 409
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 99999},
             {"from_handle": "ghost", "to_handle": "bob", "amount": 1}],
            key="eo1")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        # entry 1 unknown handle, entry 2 self-transfer -> 404 wins (input order)
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ghost", "to_handle": "bob", "amount": 1},
             {"from_handle": "ada", "to_handle": "ada", "amount": 1}],
            key="eo2")
        self.assertEqual(status, 404)
        # entry 1 self-transfer, entry 2 unknown -> 422 self_payment wins
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "ada", "amount": 1},
             {"from_handle": "ghost", "to_handle": "bob", "amount": 1}],
            key="eo3")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "self_payment")
        # malformed shape before insufficient funds: transfers not an array
        status, payload, _ = self._settle(self.ada, "nope", key="eo4")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        # too many entries
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 1}] * 33,
            key="eo5")
        self.assertEqual(status, 422)
        # empty transfers
        status, payload, _ = self._settle(self.ada, [], key="eo6")
        self.assertEqual(status, 422)
        # entry not an object
        status, payload, _ = self._settle(self.ada, ["nope"], key="eo7")
        self.assertEqual(status, 422)
        # missing amount
        status, payload, _ = self._settle(self.ada,
                                          [{"from_handle": "ada", "to_handle": "bob"}],
                                          key="eo8")
        self.assertEqual(status, 422)
        # bad amount type inside a batch is 422 (endpoint rule), before the unknown
        # handle in a LATER entry
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": "x"},
             {"from_handle": "ghost", "to_handle": "bob", "amount": 1}],
            key="eo9")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        # nothing committed by any failed attempt
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["state"]["payments"], [])
        # failed attempts claimed no keys: reuse works as a first use
        status, _, _ = self._settle(self.ada,
                                    [{"from_handle": "ada", "to_handle": "bob",
                                      "amount": 10}], key="eo2")
        self.assertEqual(status, 201)

    def test_collective_not_pairwise_affordability(self):
        # ada has 10000, bob 2500. Sequential legs would fail: ada -> cy 8000 (ok),
        # then cy -> ada 7500, then bob -> ada 2000 would overdraw... construct the two
        # canonical cases:
        self.client.request("POST", "/auth/signup",
                            {"email": "cy@x.com", "password": "correct horse",
                             "display_name": "Cy"})
        # (1) legal only when netted: ada pays out 8000 then receives 7900 back: net -100
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "cy", "amount": 8000},
             {"from_handle": "cy", "to_handle": "ada", "amount": 7900}],
            key="col1")
        self.assertEqual(status, 201)  # pairwise-sequential cy never holds 8000? cy does: 8000 in first. Use a truly netting-only case below.
        # (2) illegal when netted: bob receives 8000 first, pays 8100 -> net -100
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "cy", "to_handle": "bob", "amount": 8000},
             {"from_handle": "bob", "to_handle": "ada", "amount": 8100}],
            key="col2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        _, export, _ = self.client.request("GET", "/_test/export")
        users = {u["handle"]: u["balance"] for u in export["state"]["users"].values()}
        self.assertEqual(users["bob"], 2500)  # the 409 batch left no trace
        self.assertEqual(sum(users.values()), 12500)

    def test_netting_only_batch_is_accepted(self):
        # a batch where each wallet's NET is affordable but some intermediate state
        # (paying sequentially) would go negative: cy starts at 0.
        # cy -> ada 3000 and ada -> cy 3000 in one batch: both nets are 0/0, but
        # pairwise-sequential execution would need cy to hold 3000 first. Netted, this
        # is affordable for everyone.
        _, cy, _ = self.client.request("POST", "/auth/signup",
                                       {"email": "cy@x.com", "password": "correct horse",
                                        "display_name": "Cy"})
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "cy", "to_handle": "ada", "amount": 3000},
             {"from_handle": "ada", "to_handle": "cy", "amount": 3000}],
            key="net1")
        self.assertEqual(status, 201)
        _, me, _ = self.client.request("GET", "/me", token=cy["token"])
        self.assertEqual(me["balance"], 0)
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000)

    def test_all_or_nothing_on_insufficient(self):
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 10},
             {"from_handle": "bob", "to_handle": "ada", "amount": 999999}],
            key="ao1")
        self.assertEqual(status, 409)
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["state"]["payments"], [])  # no leg committed
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000)
        _, me, _ = self.client.request("GET", "/me", token=self.bob)
        self.assertEqual(me["balance"], 2500)

    def test_replay_returns_original_complete_response(self):
        body = [{"from_handle": "ada", "to_handle": "bob", "amount": 10},
                {"from_handle": "bob", "to_handle": "ada", "amount": 4}]
        status, first, _ = self._settle(self.ada, body, key="rp1")
        self.assertEqual(status, 201)
        status, replay, _ = self._settle(self.ada, body, key="rp1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        # moved exactly once
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 + 4 - 10)
        # different body, same key -> reuse
        status, payload, _ = self._settle(
            self.ada, [{"from_handle": "ada", "to_handle": "bob", "amount": 11}], key="rp1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_constituent_feed_visibility(self):
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 10,
              "visibility": "private"},
             {"from_handle": "ada", "to_handle": "bob", "amount": 5}],
            key="vis1")
        self.assertEqual(status, 201)
        _, carol, _ = self.client.request("POST", "/auth/signup",
                                          {"email": "c@x.com", "password": "correct horse",
                                           "display_name": "C"})
        _, feed, _ = self.client.request("GET", "/activity", token=carol["token"])
        self.assertEqual([p["amount"] for p in feed["payments"]], [5])  # only the public one
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual({p["amount"] for p in feed["payments"]}, {10, 5})
        self.assertTrue(all(p["settlement_id"] is not None for p in feed["payments"]))

    def test_operator_permission_scoped_to_settlements(self):
        # being an operator does not grant access to bob's requests or private activity
        self.client.request("POST", "/requests", {"payer_handle": "bob", "amount": 10},
                            token=self.ada, key="rq-a")
        _, listing, _ = self.client.request("GET", "/requests", token=self.ada)
        self.assertEqual(len(listing["requests"]), 1)  # her own outgoing only
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 3, "visibility": "private"},
                            token=self.ada, key="pv1")
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["state"]["settlement_operator_ids"], ["u_ada"])

    def test_permissions_preserved_over_reset_and_import(self):
        _, export, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export)
        status, payload, _ = self._settle(
            self.ada, [{"from_handle": "ada", "to_handle": "bob", "amount": 7}], key="imp1")
        self.assertEqual(status, 201)  # still an operator after import
        # and a replay of a pre-import settlement still returns the original receipt
        status, first, _ = self._settle(
            self.ada, [{"from_handle": "ada", "to_handle": "bob", "amount": 10}], key="pre")
        _, export2, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export2)
        status, replay, _ = self._settle(
            self.ada, [{"from_handle": "ada", "to_handle": "bob", "amount": 10}], key="pre")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)

    def test_concurrent_settlements_conserve(self):
        def run(i):
            return self._settle(self.ada,
                                [{"from_handle": "ada", "to_handle": "bob", "amount": 1},
                                 {"from_handle": "bob", "to_handle": "ada", "amount": 1}],
                                key="conc-%d" % i)[0]

        statuses = util.concurrent(20, run)
        self.assertTrue(all(s in (201, 200, 409) for s in statuses))
        self.assertFalse([s for s in statuses if s >= 500])
        _, export, _ = self.client.request("GET", "/_test/export")
        total = sum(u["balance"] for u in export["state"]["users"].values())
        self.assertEqual(total, 12500)
        self.assertTrue(all(u["balance"] >= 0 for u in export["state"]["users"].values()))

    def test_unknown_fields_ignored(self):
        status, payload, _ = self._settle(
            self.ada,
            [{"from_handle": "ada", "to_handle": "bob", "amount": 10, "junk": 1,
              "memo": "x"}],
            key="uf1")
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()