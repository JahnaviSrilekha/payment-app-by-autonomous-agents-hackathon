"""T22: format_version export/import round-trips (R138-R142, R158, R174); the
version is 3 since stage 3 (A20), with versions 1-3 still accepted on import."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402

FAR = "2099-01-01T00:00:00+00:00"


def rich_fixture():
    fx = util.spec_fixture()
    fx["payments"] = [
        {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 500, "note": "coffee", "visibility": "public"},
    ]
    fx["requests"] = [
        {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
         "amount": 700, "note": "taxi", "status": "pending"},
    ]
    fx["authorization_ttl_seconds"] = 300
    fx["authorizations"] = [
        {"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 2000, "note": "deposit", "visibility": "public",
         "status": "open", "expires_at": FAR},
        {"id": "a_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
         "amount": 400, "captured_amount": 150, "status": "captured",
         "expires_at": FAR},
        {"id": "a_3", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 100, "status": "voided", "expires_at": FAR},
    ]
    return fx


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


class TestT22SeededCapturedDefault(unittest.TestCase):
    """R153 lists no captured_amount for seeded entries: a seeded `captured`
    authorization implies a completed full capture."""

    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_seeded_captured_defaults_to_full_amount(self):
        fx = util.spec_fixture()
        fx["payments"] = []
        fx["requests"] = []
        fx["authorizations"] = [
            {"id": "a_c", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 500, "status": "captured", "expires_at": FAR}]
        util.reset(self.client, fx)
        ada = login(self.client, "ada@example.com")
        _, listed, _ = self.client.request("GET", "/authorizations", token=ada)
        row = listed["authorizations"][0]
        self.assertEqual((row["status"], row["captured_amount"],
                          row["remaining_amount"]), ("captured", 500, 0))


class TestT22RoundTrips(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_v2_round_trip_is_byte_identical(self):
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        bob = login(self.client, "bob@example.com")  # bob is the receiver of a_1
        # make a payment via capture so a payment carries authorization_id
        self.client.request("POST", "/authorizations/a_1/capture",
                            {"amount": 600, "final": False}, token=bob, key="cap-1")
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["format_version"], 4)  # A28: stage 4 exports version 4
        state = export["state"]
        self.assertEqual(state["authorization_ttl_seconds"], 300)
        self.assertEqual(len(state["authorizations"]), 3)
        self.assertTrue(all("authorization_id" in p for p in state["payments"]))
        self.assertEqual(state["payments"][0]["authorization_id"], None)
        self.assertEqual(state["payments"][-1]["authorization_id"], "a_1")
        status, _, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204)
        _, again, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(again, export)  # byte-for-byte round trip

    def test_v1_payload_imports_with_defaults(self):
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        _, export, _ = self.client.request("GET", "/_test/export")
        stage1 = {
            "track": export["track"],
            "format_version": 1,
            "state": {k: v for k, v in export["state"].items()
                      if k not in ("authorizations", "authorization_order",
                                   "authorization_ttl_seconds")},
        }
        for payment in stage1["state"]["payments"]:
            payment.pop("authorization_id", None)
        status, _, _ = self.client.request("POST", "/_test/import", stage1)
        self.assertEqual(status, 204)
        _, reexport, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(reexport["state"]["authorizations"], {})  # R158
        self.assertEqual(reexport["state"]["authorization_ttl_seconds"], 600)
        self.assertTrue(all(p["authorization_id"] is None
                            for p in reexport["state"]["payments"]))
        # every stage-1 behaviour intact: pending request payable, balance whole
        ada = login(self.client, "ada@example.com")
        _, me, _ = self.client.request("GET", "/me", token=ada)
        self.assertEqual((me["total"], me["held"], me["available"]), (10000, 0, 10000))
        _, pay, _ = self.client.request("POST", "/requests/rq_1/pay", {},
                                        token=ada, key="after-import-1")
        self.assertEqual(pay["amount"], 700)

    def test_browser_token_survives_import(self):
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        _, export, _ = self.client.request("GET", "/_test/export")
        status, _, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204)
        status, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(status, 200)  # same token, same browser
        self.assertEqual(me["total"], 10000)

    def test_lost_payment_response_retryable_after_import(self):
        """A payment whose response was dropped before export: the same key/body
        returns the original 201 after import and moves money exactly once (R139)."""
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        body = {"to_handle": "bob", "amount": 250, "note": "lost"}
        key = "lost-before-export"
        status, first, _ = self.client.request("POST", "/payments", body,
                                               token=token, key=key)
        self.assertEqual(status, 201)
        _, export, _ = self.client.request("GET", "/_test/export")
        status, _, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204)
        status, replay, _ = self.client.request("POST", "/payments", body,
                                                token=token, key=key)
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)  # original payment body recovered
        _, me_ada, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(me_ada["total"], 9750)  # 10000 - 500 seeded - 250 once
        _, me_bob, _ = self.client.request(
            "GET", "/me", token=login(self.client, "bob@example.com"))
        self.assertEqual(me_bob["total"], 2500 + 250)  # moved exactly once

    def test_pending_request_payable_after_import(self):
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        _, export, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export)
        status, payment, _ = self.client.request("POST", "/requests/rq_1/pay", {},
                                                 token=token, key="rq-after-import")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 700)

    def test_open_holds_survive_import_exactly(self):
        util.reset(self.client, rich_fixture())
        token = login(self.client, "ada@example.com")
        _, before_me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual((before_me["held"], before_me["available"]), (2000, 8000))
        _, export, _ = self.client.request("GET", "/_test/export")
        self.client.request("POST", "/_test/import", export)
        _, after_me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual((after_me["held"], after_me["available"]), (2000, 8000))
        # the imported hold can still be captured (only the receiver may capture)
        bob = login(self.client, "bob@example.com")  # bob is the receiver of a_1
        status, payment, _ = self.client.request(
            "POST", "/authorizations/a_1/capture", {"amount": 500},
            token=bob, key="post-import-capture")
        self.assertEqual(status, 201)
        self.assertEqual(payment["authorization_id"], "a_1")


if __name__ == "__main__":
    unittest.main()