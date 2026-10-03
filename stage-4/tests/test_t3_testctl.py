"""T3: reset/seed, export/import round trips (R11, R17-R20, R32-R36, R85-R93)."""

import copy
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


class TestT3Reset(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_reset_seeds_spec_fixture_exactly(self):
        util.reset(self.client)
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "ada@example.com",
                                                  "password": "correct horse"})
        self.assertEqual(status, 200)  # seeded users log in immediately
        ada_token = payload["token"]
        status, me, _ = self.client.request("GET", "/me", token=ada_token)
        self.assertEqual(me, {"user_id": "u_ada", "display_name": "Ada", "handle": "ada",
                              "balance": 10000, "total": 10000, "available": 10000,
                              "held": 0, "currency": "EUR", "minor_units": 2})
        _, bob_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "bob@example.com", "password": "correct horse"})
        _, me_bob, _ = self.client.request("GET", "/me", token=bob_login["token"])
        self.assertEqual(me_bob["balance"], 2500)  # after seeded payments, not replayed

    def test_repeated_reset_supported(self):
        util.reset(self.client)
        util.reset(self.client)
        status, me, _ = self.client.request("GET", "/me", token=util.signup(self.client, "x@x.com"))
        self.assertEqual(status, 200)
        util.reset(self.client)  # signup wiped by reset
        status, _, _ = self.client.request("GET", "/me", token=util.signup(self.client, "x@x.com"))
        self.assertEqual(status, 200)

    def test_negative_balance_fixture_422_no_change(self):
        util.reset(self.client)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "ada@example.com", "password": "correct horse"})
        token = login["token"]
        bad = util.spec_fixture()
        bad["users"][0]["balance"] = -1
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        status, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(me["balance"], 10000)  # state unchanged

    def test_reset_validation_errors(self):
        util.reset(self.client)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "ada@example.com", "password": "correct horse"})
        token = login["token"]
        bad_cases = [
            ({}, 422),  # missing currency
            (util.spec_fixture() | {"minor_units": 1}, 422),
            (util.spec_fixture() | {"users": "nope"}, 400),
            (util.spec_fixture() | {"requests": None}, 400),
            (util.spec_fixture() | {"currency": 42}, 400),
            (util.spec_fixture() | {"settlement_operator_ids": ["u_missing"]}, 422),
            (util.spec_fixture() | {"payments": [{"id": "p_x", "from_user_id": "u_ghost",
                                                  "to_user_id": "u_ada", "amount": 5}]}, 422),
            (util.spec_fixture() | {"payments": [{"id": "p_1", "from_user_id": "u_ada",
                                                  "to_user_id": "u_bob", "amount": 500},
                                                 {"id": "p_1", "from_user_id": "u_ada",
                                                  "to_user_id": "u_bob", "amount": 100}]}, 422),
        ]
        for fixture, expected in bad_cases:
            status, payload, _ = self.client.request("POST", "/_test/reset", fixture)
            self.assertEqual(status, expected, fixture)
            if expected == 422:
                self.assertEqual(payload["error"]["code"], "validation_failed")
        duplicate = util.spec_fixture()
        duplicate["users"][1]["handle"] = "ada"
        status, payload, _ = self.client.request("POST", "/_test/reset", duplicate)
        self.assertEqual(status, 422)
        status, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(me["handle"], "ada")  # still the old state

    def test_reset_accepts_minimal_seeded_payment(self):
        # note/visibility omitted on a seeded payment take the ordinary defaults
        fixture = util.spec_fixture() | {"payments": [{"id": "p_1", "from_user_id": "u_ada",
                                                       "to_user_id": "u_bob", "amount": 500}]}
        status, _, _ = self.client.request("POST", "/_test/reset", fixture)
        self.assertEqual(status, 204)
        _, export, _ = self.client.request("GET", "/_test/export")
        payment = export["state"]["payments"][0]
        self.assertEqual((payment["note"], payment["visibility"]), ("", "public"))

    def test_negative_balance_is_422_not_400(self):
        bad = util.spec_fixture()
        bad["users"][1]["balance"] = -5
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")


class TestT3ExportImport(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_export_shape(self):
        util.reset(self.client)
        status, payload, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(status, 200)
        self.assertEqual(payload["track"], "pocketful")
        self.assertEqual(payload["format_version"], 3)  # stage 3 exports version 3 (A20)
        self.assertIsInstance(payload["state"], dict)

    def test_export_is_atomic_snapshot(self):
        util.reset(self.client)
        _, e1, _ = self.client.request("GET", "/_test/export")
        util.signup(self.client, "later@example.com")
        snapshot_payments = e1["state"]["payments"]
        util.reset(self.client)  # mutate everything afterwards
        self.assertEqual(e1["state"]["users"]["u_ada"]["balance"], 10000)
        self.assertEqual(snapshot_payments[0]["id"], "p_1")  # snapshot never changed

    def test_round_trip_preserves_everything(self):
        util.reset(self.client)
        _, ada_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "ada@example.com", "password": "correct horse"})
        ada_token = ada_login["token"]
        signup_token = util.signup(self.client, "charlie@example.com", display_name="Charlie")
        # an idempotent write (test stub) so its record must survive too
        status, first, _ = self.client.request("POST", "/_test/idem/echo",
                                               {"hello": "world"}, token=ada_token, key="K-1")
        self.assertEqual(status, 201)
        _, export, _ = self.client.request("GET", "/_test/export")
        # mutate after export: another stub write and a new signup
        self.client.request("POST", "/_test/idem/echo", {"other": 1}, token=ada_token, key="K-2")
        util.signup(self.client, "dave@example.com")

        status, _, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204)

        # accounts and tokens preserved...
        status, me, _ = self.client.request("GET", "/me", token=ada_token)
        self.assertEqual(status, 200)
        self.assertEqual((me["handle"], me["balance"]), ("ada", 10000))
        status, me, _ = self.client.request("GET", "/me", token=signup_token)
        self.assertEqual(status, 200)
        self.assertEqual(me["handle"], "charlie")
        # ...hashed-password login preserved...
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "ada@example.com", "password": "correct horse"})
        self.assertEqual(status, 200)
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "charlie@example.com", "password": "correct horse"})
        self.assertEqual(status, 200)
        # ...post-export mutations discarded (replacement, not merge)...
        status, _, _ = self.client.request("POST", "/auth/login",
                                           {"email": "dave@example.com", "password": "correct horse"})
        self.assertEqual(status, 401)
        # ...idempotency records preserved: replay works after import...
        status, replay, _ = self.client.request("POST", "/_test/idem/echo",
                                                {"hello": "world"}, token=ada_token, key="K-1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        # ...and the key used after import with a different body is still a reuse...
        status, payload, _ = self.client.request("POST", "/_test/idem/echo",
                                                 {"different": True}, token=ada_token, key="K-1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")
        # ...failed keys remain reusable: K-2 succeeded pre-import? No: K-2 was
        # recorded after the export, so importing removed it; retry is a first use.
        status, payload, _ = self.client.request("POST", "/_test/idem/echo",
                                                 {"fresh": 1}, token=ada_token, key="K-2")
        self.assertEqual(status, 201)

    def test_repeating_import_restores_without_duplication(self):
        util.reset(self.client)
        _, export, _ = self.client.request("GET", "/_test/export")
        for _ in range(3):
            status, _, _ = self.client.request("POST", "/_test/import", export)
            self.assertEqual(status, 204)
        _, again, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(again["state"], export["state"])

    def test_import_invalid_422_destination_unchanged(self):
        util.reset(self.client)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "ada@example.com", "password": "correct horse"})
        token = login["token"]
        _, good, _ = self.client.request("GET", "/_test/export")

        bad_payloads = [
            {"track": "other", "format_version": 1, "state": good["state"]},
            {"track": "pocketful", "format_version": 4, "state": good["state"]},  # 3 is accepted since A20
            {"track": "pocketful", "format_version": 1},
            {"track": "pocketful", "format_version": 1, "state": {"currency": "EUR"}},
            {"track": "pocketful", "format_version": 1, "state": dict(good["state"], users={})},
            {"track": "pocketful", "format_version": 1, "state": dict(good["state"], payments="x")},
            {"track": "pocketful", "format_version": 1, "state": dict(good["state"], tokens={"t": "u_ghost"})},
            {"track": "pocketful", "format_version": 1, "state": dict(good["state"], idempotency=[{"nope": 1}])},
        ]
        for bad in bad_payloads:
            status, payload, _ = self.client.request("POST", "/_test/import", bad)
            self.assertEqual(status, 422, bad)
            self.assertEqual(payload["error"]["code"], "validation_failed")
        status, payload, _ = self.client.request("POST", "/_test/import", raw=b"{bad json") \
            if False else self.client.request("POST", "/_test/import", raw_body=b"{bad json")
        self.assertEqual(status, 400)
        status, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(me["balance"], 10000)  # destination untouched throughout
        _, after, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(after["state"], good["state"])

    def test_import_accepts_unchanged_export_and_replays(self):
        # reset → write → export → import the same object twice → replay old receipts
        util.reset(self.client)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "ada@example.com", "password": "correct horse"})
        token = login["token"]
        _, created, _ = self.client.request("POST", "/_test/idem/counter", {},
                                            token=token, key="count-1")
        self.assertEqual(created, {"n": created["n"]})
        _, export, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export)
        status, replay, _ = self.client.request("POST", "/_test/idem/counter", {},
                                                token=token, key="count-1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, created)  # original receipt, counter not re-incremented

    def test_reset_clears_imported_state(self):
        util.reset(self.client)
        _, ada_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "ada@example.com", "password": "correct horse"})
        ada_token = ada_login["token"]
        _, export, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export)
        fresh = {"currency": "JPY", "minor_units": 0,
                 "users": [{"id": "u_yen", "email": "y@example.com", "password": "correct horse",
                            "display_name": "Y", "handle": "yen", "balance": 500}],
                 "payments": [], "requests": []}
        status, _, _ = self.client.request("POST", "/_test/reset", fresh)
        self.assertEqual(status, 204)
        _, login, _ = self.client.request("POST", "/auth/login",
                                          {"email": "y@example.com", "password": "correct horse"})
        status, me, _ = self.client.request("GET", "/me", token=login["token"])
        self.assertEqual((me["currency"], me["minor_units"], me["balance"]), ("JPY", 0, 500))
        # imported state is gone after reset: ada's old token no longer works
        status, payload, _ = self.client.request("GET", "/me", token=ada_token)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()