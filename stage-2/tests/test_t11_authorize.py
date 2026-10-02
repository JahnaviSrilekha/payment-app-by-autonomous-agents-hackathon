"""T11: POST /authorizations — create path, replay rules, activity exclusion, and a
50-concurrent storm racing the same headroom (R149, R152, R161-R164, R150, R192)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


def clean_fixture(**extra):
    fixture = util.spec_fixture()
    fixture["payments"] = []
    fixture["requests"] = []
    fixture["settlement_operator_ids"] = ["u_ada"]
    fixture.update(extra)
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    assert payload and "token" in payload, payload
    return payload["token"]


class TestT11Create(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, clean_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def create(self, token, body, key="k-create"):
        return self.client.request("POST", "/authorizations", body, token=token, key=key)

    def test_spec_create_example_reproduced(self):
        status, authz, _ = self.create(self.ada, {
            "to_handle": "bob", "amount": 2000, "note": "deposit",
            "visibility": "private"})
        self.assertEqual(status, 201)
        self.assertEqual(authz["from_handle"], "ada")
        self.assertEqual(authz["to_handle"], "bob")
        self.assertEqual(authz["from_user_id"], "u_ada")
        self.assertEqual(authz["to_user_id"], "u_bob")
        self.assertEqual(authz["amount"], 2000)
        self.assertEqual(authz["captured_amount"], 0)
        self.assertEqual(authz["remaining_amount"], 2000)
        self.assertEqual(authz["currency"], "EUR")
        self.assertEqual(authz["note"], "deposit")
        self.assertEqual(authz["visibility"], "private")
        self.assertEqual(authz["status"], "open")
        self.assertIsNone(authz["payment_id"])
        self.assertEqual(authz["payment_ids"], [])
        self.assertTrue(authz["authorization_id"].startswith("a_"))
        created = datetime.fromisoformat(authz["created_at"])
        expires = datetime.fromisoformat(authz["expires_at"])
        self.assertEqual(expires - created, timedelta(seconds=600))  # R154 default ttl

    def test_ttl_fixture_applies_to_api_creations(self):
        util.reset(self.client, clean_fixture(authorization_ttl_seconds=100))
        ada = login(self.client, "ada@example.com")  # reset wiped the earlier token
        _, authz, _ = self.create(ada, {"to_handle": "bob", "amount": 200}, key="ttl")
        created = datetime.fromisoformat(authz["created_at"])
        expires = datetime.fromisoformat(authz["expires_at"])
        self.assertEqual(expires - created, timedelta(seconds=100))

    def test_note_visibility_defaults_like_payments(self):
        _, authz, _ = self.create(self.ada, {"to_handle": "bob", "amount": 200})
        self.assertEqual((authz["note"], authz["visibility"]), ("", "public"))

    def test_hold_reserves_without_moving_money(self):
        _, before_ada, _ = self.client.request("GET", "/me", token=self.ada)
        _, before_bob, _ = self.client.request("GET", "/me", token=self.bob)
        self.create(self.ada, {"to_handle": "bob", "amount": 2000})
        _, after_ada, _ = self.client.request("GET", "/me", token=self.ada)
        _, after_bob, _ = self.client.request("GET", "/me", token=self.bob)
        # totals unchanged; ada's available fell, bob untouched (R144, R155)
        self.assertEqual(after_ada["total"], before_ada["total"])
        self.assertEqual(after_ada["held"], 2000)
        self.assertEqual(after_ada["available"], 8000)
        self.assertEqual(after_bob["total"], before_bob["total"])
        self.assertEqual(after_bob["held"], 0)

    def test_error_precedence_canonical_order(self):
        """design.md section 13 order: amount shape -> note -> visibility ->
        self_payment -> unknown handle -> insufficient_funds last (R163, A8)."""
        cases = [
            ({"to_handle": "bob", "amount": 0}, 422, "validation_failed"),
            ({"to_handle": "bob", "amount": 1000000001}, 422, "validation_failed"),
            ({"to_handle": "bob", "amount": "x"}, 422, "validation_failed"),
            ({"to_handle": "bob", "amount": 200.5}, 422, "validation_failed"),
            ({"to_handle": "bob", "amount": 200, "note": "x" * 201}, 422,
             "validation_failed"),
            ({"to_handle": "bob", "amount": 200, "visibility": "friends"}, 422,
             "validation_failed"),
            # note shape is checked before self_payment
            ({"to_handle": "ada", "amount": 200, "note": "x" * 201}, 422,
             "validation_failed"),
            ({"to_handle": "ada", "amount": 200}, 422, "self_payment"),
            ({"to_handle": "ghost", "amount": 2000000000}, 422,
             "validation_failed"),  # amount range is part of shape, checked first
            ({"to_handle": "ghost", "amount": 0}, 422, "validation_failed"),
            ({"to_handle": "bob", "amount": 10001}, 409, "insufficient_funds"),
            ({"to_handle": "ghost", "amount": 10001}, 404, "not_found"),
        ]
        for body, expected_status, expected_code in cases:
            status, payload, _ = self.create(self.ada, body, key="k-%s-%s" % (
                expected_status, expected_code))
            self.assertEqual(status, expected_status, body)
            self.assertEqual(payload["error"]["code"], expected_code, body)

    def test_missing_idempotency_key_400(self):
        status, payload, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 200},
            token=self.ada)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")

    def test_replay_same_body_returns_original_201(self):
        body = {"to_handle": "bob", "amount": 2000, "note": "deposit"}
        status, first, _ = self.create(self.ada, dict(body, key_marker=1), key="K1")
        self.assertEqual(status, 201)
        status, replay, _ = self.create(self.ada, dict(body, key_marker=1), key="K1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        # the replay created exactly one authorization (read internally)
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(len(export["state"]["authorizations"]), 1)

    def test_different_body_same_key_409(self):
        self.create(self.ada, {"to_handle": "bob", "amount": 2000}, key="K2")
        status, payload, _ = self.create(self.ada,
                                         {"to_handle": "bob", "amount": 3000},
                                         key="K2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_failed_attempt_leaves_key_free(self):
        status, _, _ = self.create(self.ada, {"to_handle": "bob", "amount": 0},
                                   key="K3")
        self.assertEqual(status, 422)
        status, authz, _ = self.create(self.ada, {"to_handle": "bob", "amount": 200},
                                       key="K3")
        self.assertEqual(status, 201)
        self.assertEqual(authz["amount"], 200)

    def test_open_authorization_never_in_activity(self):
        self.create(self.ada, {"to_handle": "bob", "amount": 2000, "note": "hold"},
                    key="act-1")
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed["payments"], [])  # R164: a hold is not a feed item

    def test_authorize_against_insufficient_available_409_with_existing_hold(self):
        self.create(self.ada, {"to_handle": "bob", "amount": 6000}, key="h-1")
        status, payload, _ = self.create(self.ada, {"to_handle": "bob", "amount": 4001},
                                         key="h-2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        status, authz, _ = self.create(self.ada, {"to_handle": "bob", "amount": 4000},
                                       key="h-3")
        self.assertEqual(status, 201)  # exactly the remaining headroom


class TestT11Concurrency(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, clean_fixture())
        self.ada = login(self.client, "ada@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_fifty_concurrent_authorizations_race_same_headroom(self):
        """Only as many succeed as available allows: 33 of 300 fit in 10000 (R192)."""
        def authorize(i):
            return self.client.request("POST", "/authorizations",
                                       {"to_handle": "bob", "amount": 300},
                                       token=self.ada, key="storm-%d" % i)

        results = util.concurrent(50, authorize)
        created = [payload for status, payload, _ in results if status == 201]
        refused = [payload for status, payload, _ in results if status != 201]
        self.assertEqual(len(created), 33)
        self.assertEqual(len(refused), 17)
        self.assertTrue(all(p["error"]["code"] == "insufficient_funds"
                            for p in refused))
        self.assertEqual(sum(a["amount"] for a in created), 9900)
        _, me, _ = self.client.request("GET", "/me", token=self.ada)
        self.assertEqual((me["total"], me["held"], me["available"]),
                         (10000, 9900, 100))
        # conservation of total: holds moved no money
        _, bob_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "bob@example.com",
                                               "password": "correct horse"})
        _, me_bob, _ = self.client.request("GET", "/me", token=bob_login["token"])
        self.assertEqual(me_bob["total"], 2500)

    def test_concurrent_reads_during_authorization_storm_never_negative(self):
        stop = []

        def authorize(i):
            if stop:
                return True
            return self.client.request("POST", "/authorizations",
                                       {"to_handle": "bob", "amount": 100},
                                       token=self.ada, key="storm2-%d" % i)[0] in (201, 409)

        def read(i):
            status, me, _ = self.client.request("GET", "/me", token=self.ada)
            assert status == 200, (status, me)
            assert me["available"] == me["total"] - me["held"], me
            assert me["available"] >= 0, me
            return True

        readers = util.concurrent(20, read)
        stop.append(True)
        writers = util.concurrent(50, authorize)
        self.assertNotIn(None, readers + writers)


if __name__ == "__main__":
    unittest.main()