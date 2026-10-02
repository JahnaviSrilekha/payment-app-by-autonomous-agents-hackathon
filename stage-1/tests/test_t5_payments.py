"""T5: payments and the activity feed (R23, R26, R27, R30, R31, R56-R63, R65-R68, R81).

Money invariants are driven with concurrent HTTP requests against the real server:
conservation (R1), no transient negative balance (R2), exactly-once idempotent effects
under concurrency (R61), atomic all-or-nothing failures (R67), verbatim notes (R68)."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


def seeded_total(client):
    _, export, _ = client.request("GET", "/_test/export")
    return sum(u["balance"] for u in export["state"]["users"].values())


def balances(client, export=None):
    _, snapshot, _ = client.request("GET", "/_test/export")
    return {u["handle"]: u["balance"] for u in snapshot["state"]["users"].values()}


class TestT5Payments(unittest.TestCase):
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

    def test_spec_example_payment(self):
        status, payload, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 1500, "note": "dinner",
                                  "visibility": "public"},
            token=self.ada, key="pay-1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["to_handle"], "bob")
        self.assertEqual(payload["from_handle"], "ada")
        self.assertEqual(payload["amount"], 1500)
        self.assertEqual(payload["currency"], "EUR")
        self.assertEqual(payload["note"], "dinner")
        self.assertEqual(payload["visibility"], "public")
        self.assertEqual(payload["request_id"], None)
        self.assertIn("created_at", payload)
        self.assertEqual(payload["payment_id"], payload["payment_id"])

    def test_me_balances_move_together(self):
        _, _, _ = self.client.request("POST", "/payments",
                                      {"to_handle": "bob", "amount": 1500},
                                      token=self.ada, key="p1")
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 8500)
        _, me, _ = self.client.request("GET", "/me", token=self.bob)
        self.assertEqual(me["balance"], 4000)

    def test_defaults_note_and_visibility(self):
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 1},
                                                 token=self.ada, key="d1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["note"], "")
        self.assertEqual(payload["visibility"], "public")

    def test_insufficient_funds_leaves_no_trace(self):
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        before = me["balance"]
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": before + 1},
                                                 token=self.ada, key="p2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], before)
        _, me, _ = self.client.request("GET", "/me", token=self.bob)
        self.assertEqual(me["balance"], 2500)
        # the failed attempt freed the key
        status, _, _ = self.client.request("POST", "/payments",
                                           {"to_handle": "bob", "amount": 1},
                                           token=self.ada, key="p2")
        self.assertEqual(status, 201)

    def test_error_table(self):
        cases = [
            ({"to_handle": "ada", "amount": 5}, 422, "self_payment", "self"),
            ({"to_handle": "ghost", "amount": 5}, 404, "not_found", "unknown"),
            ({"to_handle": "bob", "amount": 0}, 422, "validation_failed", "zero"),
            ({"to_handle": "bob", "amount": -5}, 422, "validation_failed", "negative"),
            ({"to_handle": "bob", "amount": 1000000001}, 422, "validation_failed", "over"),
            ({"to_handle": "bob", "amount": 1000000000}, 409, "insufficient_funds", "max"),
            ({"to_handle": "bob", "amount": "10"}, 422, "validation_failed", "string"),
            ({"to_handle": "bob", "amount": True}, 422, "validation_failed", "boolean"),
            ({"to_handle": "bob", "amount": 10.5}, 422, "validation_failed", "fractional"),
            ({"to_handle": "bob"}, 422, "validation_failed", "missing amount"),
            ({"amount": 5}, 422, "validation_failed", "missing to_handle"),
            ({"to_handle": "bob", "amount": 5, "visibility": "secret"}, 422,
             "validation_failed", "visibility"),
            ({"to_handle": "bob", "amount": 5, "note": "x" * 201}, 422,
             "validation_failed", "note"),
        ]
        for body, status, code, label in cases:
            _, payload, _ = self.client.request("POST", "/payments", body,
                                                token=self.ada, key="err-" + label)
            self.assertEqual(payload["error"]["code"], code, (label, payload))

    def test_amount_as_float_and_exp(self):
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 1000.0},
                                                 token=self.ada, key="f1")
        self.assertEqual(status, 201)
        status, payload, _ = self.client.request("POST", "/payments",
                                                 raw_body='{"to_handle": "bob", "amount": 1e3}',
                                                 token=self.ada, key="f2")
        self.assertEqual(status, 201)
        self.assertEqual(payload["amount"], 1000)

    def test_note_verbatim_unicode_emoji(self):
        note = "dinner 🍽️ ünïcodé — line\nbreak  trailing "
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 3, "note": note},
                                                 token=self.ada, key="n1")
        self.assertEqual(status, 201)
        self.assertEqual(payload["note"], note)
        # verbatim in the feed too
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed["payments"][0]["note"], note)

    def test_note_exactly_200_ok(self):
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 3,
                                                  "note": "n" * 200},
                                                 token=self.ada, key="n2")
        self.assertEqual(status, 201)

    def test_idempotent_replay_and_reuse(self):
        body = {"to_handle": "bob", "amount": 500}
        status, first, _ = self.client.request("POST", "/payments", body,
                                               token=self.ada, key="rp1")
        self.assertEqual(status, 201)
        status, replay, _ = self.client.request("POST", "/payments", body,
                                                token=self.ada, key="rp1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 501},
                                                 token=self.ada, key="rp1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")
        # money moved exactly once
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 9500)

    def test_missing_key_header(self):
        status, payload, _ = self.client.request("POST", "/payments",
                                                 {"to_handle": "bob", "amount": 5},
                                                 token=self.ada)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")

    def test_concurrent_identical_payments_exactly_once(self):
        results = util.concurrent(16, lambda i: self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 100},
            token=self.ada, key="race-pay"))
        created = [r for r in results if r[0] == 201]
        replayed = [r for r in results if r[0] == 200]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(replayed), 15)
        self.assertEqual({str(r[1]) for r in replayed}, {str(created[0][1])})
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual(me["balance"], 10000 - 100)  # moved exactly once

    def test_fifty_concurrent_payments_never_negative_and_conserve(self):
        # rich fixture: one wallet with 100 units, 50 concurrent payments of 2 and 50 of 1
        rich = {"currency": "EUR", "minor_units": 2,
                "users": [
                    {"id": "u_one", "email": "one@x.com", "password": "correct horse",
                     "display_name": "One", "handle": "one", "balance": 150},
                    {"id": "u_two", "email": "two@x.com", "password": "correct horse",
                     "display_name": "Two", "handle": "two", "balance": 0},
                ], "payments": [], "requests": []}
        status, _, _ = self.client.request("POST", "/_test/reset", rich)
        self.assertEqual(status, 204)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "one@x.com", "password": "correct horse"})
        token = login["token"]

        def pay(i):
            amount = 2 if i % 2 == 0 else 1
            return self.client.request("POST", "/payments",
                                       {"to_handle": "two", "amount": amount},
                                       token=token, key="load-%d" % i)[0]

        statuses = util.concurrent(100, pay)
        ok = [s for s in statuses if s == 201]
        short = [s for s in statuses if s == 409]
        self.assertEqual(len(ok) + len(short), 100)
        self.assertFalse([s for s in statuses if s >= 500], "no 5xx allowed")
        _, export, _ = self.client.request("GET", "/_test/export")
        users = export["state"]["users"]
        total = sum(u["balance"] for u in users.values())
        self.assertEqual(total, 150)  # conservation (R1)
        self.assertTrue(all(u["balance"] >= 0 for u in users.values()))  # R2
        moved = sum(p["amount"] for p in export["state"]["payments"])
        self.assertEqual(moved, 150 - users["u_one"]["balance"])

    def test_concurrent_drain_never_negative(self):
        rich = {"currency": "EUR", "minor_units": 2,
                "users": [
                    {"id": "u_a", "email": "a@x.com", "password": "correct horse",
                     "display_name": "A", "handle": "a", "balance": 10},
                    {"id": "u_b", "email": "b@x.com", "password": "correct horse",
                     "display_name": "B", "handle": "b", "balance": 0},
                ], "payments": [], "requests": []}
        self.client.request("POST", "/_test/reset", rich)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "a@x.com", "password": "correct horse"})
        token = login["token"]

        def drain(i):
            return self.client.request("POST", "/payments",
                                       {"to_handle": "b", "amount": 1},
                                       token=token, key="drain-%d" % i)[0]

        statuses = util.concurrent(50, drain)
        self.assertEqual(len([s for s in statuses if s == 201]), 10)  # balance is 10
        self.assertEqual(len([s for s in statuses if s == 409]), 40)
        _, export, _ = self.client.request("GET", "/_test/export")
        users = {u["handle"]: u["balance"] for u in export["state"]["users"].values()}
        self.assertEqual(users, {"a": 0, "b": 10})  # exact drain, never negative

    def test_activity_feed_visibility_three_viewpoints(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        # ada->bob public 500, ada->bob private 5, bob->ada private 7
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 500, "visibility": "public"},
                            token=self.ada, key="v1")
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 5, "visibility": "private"},
                            token=self.ada, key="v2")
        self.client.request("POST", "/payments",
                            {"to_handle": "ada", "amount": 7, "visibility": "private"},
                            token=self.bob, key="v3")
        status, carol, _ = self.client.request("POST", "/auth/signup",
                                              {"email": "carol@x.com", "password": "correct horse",
                                               "display_name": "C"})
        carol = carol["token"]
        _, feed, _ = self.client.request("GET", "/activity", token=carol)
        self.assertEqual(len(feed["payments"]), 1)  # only the public one
        self.assertEqual(feed["payments"][0]["amount"], 500)
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual({p["amount"] for p in feed["payments"]}, {500, 5, 7})  # sender sees all
        _, feed, _ = self.client.request("GET", "/activity", token=self.bob)
        self.assertEqual({p["amount"] for p in feed["payments"]}, {500, 5, 7})  # receiver sees all
        self.assertEqual(feed["payments"][0]["amount"], 7)  # newest first
        self.assertEqual(feed["has_more"], False)

    def test_requests_never_in_activity(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        self.client.request("POST", "/requests", {"payer_handle": "ada", "amount": 10},
                            token=self.bob, key="rq-a")
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed["payments"], [])

    def test_pagination_and_visibility_intertwine(self):
        util.reset_clean(self.client)
        self.ada = self._login("ada")
        self.bob = self._login("bob")
        for i in range(5):
            self.client.request("POST", "/payments",
                                {"to_handle": "bob", "amount": 1, "visibility": "public"},
                                token=self.ada, key="pg-%d" % i)
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 1, "visibility": "private"},
                            token=self.ada, key="pg-priv")
        status, feed, _ = self.client.request("GET", "/activity?limit=3&offset=0",
                                              token=self.bob)
        self.assertEqual(len(feed["payments"]), 3)
        self.assertTrue(feed["has_more"])
        status, feed, _ = self.client.request("GET", "/activity?limit=3&offset=3",
                                              token=self.bob)
        self.assertEqual(len(feed["payments"]), 3)
        self.assertFalse(feed["has_more"])
        # private payments are hidden from carol but count in her pagination window
        _, carol, _ = self.client.request("POST", "/auth/signup",
                                          {"email": "carol2@x.com", "password": "correct horse",
                                           "display_name": "C"})
        status, feed, _ = self.client.request("GET", "/activity?limit=3&offset=0",
                                              token=carol["token"])
        self.assertEqual(len(feed["payments"]), 3)
        self.assertTrue(feed["has_more"])
        status, feed, _ = self.client.request("GET", "/activity?limit=3&offset=3",
                                              token=carol["token"])
        # carol sees only the 5 public payments: 3 on page one, 2 on page two
        self.assertEqual(len(feed["payments"]), 2)
        self.assertFalse(feed["has_more"])

    def test_bad_list_parameters_422(self):
        for query in ("limit=0", "limit=201", "limit=1e2", "limit=4.0", "limit=+4",
                      "offset=-1", "offset=abc", "limit="):
            status, payload, _ = self.client.request("GET", "/activity?" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, query)
            self.assertEqual(payload["error"]["code"], "validation_failed", query)
        status, feed, _ = self.client.request("GET", "/activity?limit=200&offset=0&zzz=9",
                                              token=self.ada)
        self.assertEqual(status, 200)  # unknown query params ignored


if __name__ == "__main__":
    unittest.main()