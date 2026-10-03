"""T10: fixture/reset/GET /me additions over HTTP (R147, R153-R160, R192)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
FAR_FUTURE = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(timespec="seconds")
PAST = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")


def hold_fixture():
    return util.spec_fixture() | {
        "authorization_ttl_seconds": 600,
        "authorizations": [
            {"id": "a_open", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 2000, "note": "deposit", "visibility": "public",
             "status": "open", "expires_at": FAR_FUTURE},
            {"id": "a_lapsed", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 700, "status": "open", "expires_at": PAST},
            {"id": "a_captured", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 500, "captured_amount": 500, "status": "captured",
             "expires_at": FAR_FUTURE},
            {"id": "a_voided", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 300, "status": "voided", "expires_at": FAR_FUTURE},
            {"id": "a_stored_expired", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 400, "status": "expired", "expires_at": FAR_FUTURE},
        ],
    }


def login_ada(client):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": "ada@example.com", "password": "correct horse"})
    return payload["token"]


class TestT10ResetWithHolds(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_seeded_authorizations_reproduced_verbatim(self):
        status, _, _ = self.client.request("POST", "/_test/reset", hold_fixture())
        self.assertEqual(status, 204)
        _, export, _ = self.client.request("GET", "/_test/export")
        state = export["state"]
        self.assertEqual(state["authorization_ttl_seconds"], 600)
        seeded = {a["id"]: a for a in state["authorizations"].values()}
        self.assertEqual(set(seeded), {"a_open", "a_lapsed", "a_captured",
                                       "a_voided", "a_stored_expired"})
        self.assertEqual(seeded["a_open"]["status"], "open")
        self.assertEqual(seeded["a_open"]["expires_at"], FAR_FUTURE)
        self.assertEqual(seeded["a_open"]["amount"], 2000)
        self.assertEqual(seeded["a_open"]["captured_amount"], 0)
        self.assertEqual(seeded["a_lapsed"]["status"], "open")  # stored, still open
        self.assertEqual(seeded["a_captured"]["captured_amount"], 500)
        self.assertEqual(state["authorization_order"],
                         ["a_open", "a_lapsed", "a_captured", "a_voided",
                          "a_stored_expired"])

    def test_me_reports_total_available_held(self):
        util.reset(self.client, hold_fixture())
        status, me, _ = self.client.request("GET", "/me", token=login_ada(self.client))
        self.assertEqual(status, 200)
        self.assertEqual(me["balance"], 10000)
        self.assertEqual(me["total"], 10000)          # balance == total always
        self.assertEqual(me["held"], 2000)            # only the unexpired open hold
        self.assertEqual(me["available"], 8000)       # total - held
        _, me_bob, _ = self.client.request("GET", "/me", token=util.signup(
            self.client, "x@x.com"))  # placeholder; bob checked below
        # bob: all his seeded authorizations are closed, so nothing is held
        _, bob_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "bob@example.com",
                                               "password": "correct horse"})
        _, me_bob, _ = self.client.request("GET", "/me", token=bob_login["token"])
        self.assertEqual(me_bob["total"], 2500)
        self.assertEqual(me_bob["held"], 0)
        self.assertEqual(me_bob["available"], 2500)

    def test_expired_hold_releases_remainder_in_available(self):
        util.reset(self.client, hold_fixture())
        token = login_ada(self.client)
        _, me, _ = self.client.request("GET", "/me", token=token)
        # a_lapsed expires_at is in the past though stored open: holds nothing (R159)
        self.assertEqual(me["held"], 2000)
        self.assertEqual(me["available"], 8000)

    def test_conservation_of_total_with_holds(self):
        util.reset(self.client, hold_fixture())
        _, me_ada, _ = self.client.request("GET", "/me", token=login_ada(self.client))
        _, bob_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "bob@example.com",
                                               "password": "correct horse"})
        _, me_bob, _ = self.client.request("GET", "/me", token=bob_login["token"])
        # a hold moves no money: totals still sum to the seeded amounts
        self.assertEqual(me_ada["total"] + me_bob["total"], 12500)

    def test_seeded_open_holds_above_balance_422_unchanged(self):
        util.reset(self.client)
        token = login_ada(self.client)
        bad = hold_fixture()
        bad["authorizations"][0]["amount"] = 10001  # > ada's 10000 balance
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(me["total"], 10000)  # nothing changed
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["state"]["authorizations"], {})

    def test_seeded_open_holds_equal_balance_allowed(self):
        equal = hold_fixture()
        equal["authorizations"][0]["amount"] = 10000  # available exactly 0, not negative
        status, _, _ = self.client.request("POST", "/_test/reset", equal)
        self.assertEqual(status, 204)
        _, me, _ = self.client.request("GET", "/me", token=login_ada(self.client))
        self.assertEqual((me["held"], me["available"]), (10000, 0))

    def test_expired_seed_may_exceed_balance(self):
        """Only *unexpired* open holds count against R156; a stored-open row whose
        expires_at is past is already expired and holds nothing."""
        lapsed_only = hold_fixture()
        lapsed_only["authorizations"] = [
            a for a in lapsed_only["authorizations"] if a["id"] == "a_lapsed"]
        lapsed_only["authorizations"][0]["amount"] = 50000
        status, _, _ = self.client.request("POST", "/_test/reset", lapsed_only)
        self.assertEqual(status, 204)
        _, me, _ = self.client.request("GET", "/me", token=login_ada(self.client))
        self.assertEqual((me["held"], me["available"], me["total"]), (0, 10000, 10000))

    def test_stage1_fixture_without_authorizations_key(self):
        """R158: an earlier fixture may omit authorizations (and the ttl) altogether."""
        util.reset(self.client)  # util.spec_fixture has neither key
        token = login_ada(self.client)
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual((me["held"], me["available"], me["total"]), (0, 10000, 10000))
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["state"]["authorizations"], {})
        self.assertEqual(export["state"]["authorization_order"], [])
        self.assertEqual(export["state"]["authorization_ttl_seconds"], 600)

    def test_ttl_fixture_validation(self):
        util.reset(self.client)
        token = login_ada(self.client)
        for bad_ttl in (0, -5, "600", 600.5, True):
            fixture = util.spec_fixture() | {"authorization_ttl_seconds": bad_ttl}
            status, payload, _ = self.client.request("POST", "/_test/reset", fixture)
            self.assertEqual(status, 422, bad_ttl)
            self.assertEqual(payload["error"]["code"], "validation_failed")
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(me["total"], 10000)  # nothing changed

    def test_authorization_fixture_validation(self):
        util.reset(self.client)
        token = login_ada(self.client)

        def with_auth(**overrides):
            authz = {"id": "a_x", "from_user_id": "u_ada", "to_user_id": "u_bob",
                     "amount": 500, "status": "open", "expires_at": FAR_FUTURE}
            authz.update(overrides)
            return util.spec_fixture() | {"authorizations": [authz]}

        bad_cases = [
            with_auth(from_user_id="u_ghost"),
            with_auth(to_user_id="u_ghost"),
            with_auth(amount=0),
            with_auth(amount=-1),
            with_auth(status="frozen"),
            with_auth(status=None) if False else util.spec_fixture() | {
                "authorizations": [{"id": "a_x", "from_user_id": "u_ada",
                                    "to_user_id": "u_bob", "amount": 500,
                                    "expires_at": FAR_FUTURE}]},  # status required
            with_auth(expires_at="not-a-time"),
            with_auth(captured_amount=501),
            with_auth(captured_amount=-1),
            with_auth(visibility="friends"),
            with_auth(note="x" * 201),
            util.spec_fixture() | {"authorizations": [
                with_auth()["authorizations"][0], with_auth()["authorizations"][0]]},
            util.spec_fixture() | {"authorizations": {"a_x": {}}},
        ]
        for fixture in bad_cases:
            status, payload, _ = self.client.request("POST", "/_test/reset", fixture)
            self.assertIn(status, (400, 422), fixture)
            if status == 422:
                self.assertEqual(payload["error"]["code"], "validation_failed", fixture)
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(me["total"], 10000)  # destination unchanged throughout

    def test_concurrent_me_reads_consistent_while_holds_exist(self):
        """R192: reads under concurrency are equivalent to some serial order."""
        util.reset(self.client, hold_fixture())
        ada_token = login_ada(self.client)
        _, bob_login, _ = self.client.request("POST", "/auth/login",
                                              {"email": "bob@example.com",
                                               "password": "correct horse"})
        bob_token = bob_login["token"]

        def read(i):
            status, me, _ = self.client.request(
                "GET", "/me", token=ada_token if i % 2 == 0 else bob_token)
            assert status == 200, (status, me)
            return me

        results = util.concurrent(32, read)
        self.assertNotIn(None, results)  # a crashed worker leaves None: fail the test
        for i, me in enumerate(results):
            self.assertGreaterEqual(me["available"], 0)
            self.assertEqual(me["available"], me["total"] - me["held"])
            expected = {"u_ada": 2000, "u_bob": 0}[me["user_id"]]
            self.assertEqual(me["held"], expected)

    def test_concurrent_reset_and_reads_never_negative(self):
        """A snapshot taken under the lock at any instant — old or new state — never
        shows a user's open holds exceeding their balance."""
        util.reset(self.client, hold_fixture())
        plain = util.spec_fixture()
        now = datetime.now(timezone.utc)

        def flip(_):
            for _ in range(10):
                self.client.request("POST", "/_test/reset", hold_fixture())
                self.client.request("POST", "/_test/reset", plain)
            return True

        def read(_):
            for _ in range(10):
                status, export, _ = self.client.request("GET", "/_test/export")
                assert status == 200, (status, export)
                state = export["state"]
                balances = {uid: u["balance"] for uid, u in state["users"].items()}
                for authz in state["authorizations"].values():
                    expires = datetime.fromisoformat(
                        authz["expires_at"].replace("Z", "+00:00"))
                    if authz["status"] == "open" and expires > now:
                        balances[authz["from_user_id"]] -= (
                            authz["amount"] - authz["captured_amount"])
                for uid, available in balances.items():
                    assert available >= 0, (uid, available)
            return True

        results = util.concurrent(8, flip) + util.concurrent(8, read)
        self.assertNotIn(None, results)  # a crashed worker leaves None: fail the test


class TestT10ExportImport(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_export_is_format_version_2_and_round_trips(self):  # see A20: now 3
        util.reset(self.client, hold_fixture())
        token = login_ada(self.client)
        _, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(export["format_version"], 3)  # A20: stage 3 exports version 3
        status, _, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204)
        _, me, _ = self.client.request("GET", "/me", token=token)  # token survives import
        self.assertEqual((me["total"], me["held"], me["available"]), (10000, 2000, 8000))
        _, again, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(again["state"], export["state"])

    def test_import_stage1_format_version_1_payload(self):
        """R138/R158: a stage-1 export (version 1, no authorizations) imports cleanly."""
        util.reset(self.client, hold_fixture())
        token = login_ada(self.client)
        _, export, _ = self.client.request("GET", "/_test/export")
        stage1_payload = {
            "track": export["track"],
            "format_version": 1,
            "state": {k: v for k, v in export["state"].items()
                      if k not in ("authorizations", "authorization_order",
                                   "authorization_ttl_seconds")},
        }
        for payment in stage1_payload["state"]["payments"]:
            payment.pop("authorization_id", None)
        status, _, _ = self.client.request("POST", "/_test/import", stage1_payload)
        self.assertEqual(status, 204)
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual((me["total"], me["held"], me["available"]), (10000, 0, 10000))
        _, reexport, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(reexport["state"]["authorizations"], {})
        self.assertEqual(reexport["state"]["authorization_ttl_seconds"], 600)
        self.assertTrue(all(p["authorization_id"] is None
                            for p in reexport["state"]["payments"]))

    def test_import_rejects_unknown_versions_and_bad_authorizations(self):
        util.reset(self.client, hold_fixture())
        _, export, _ = self.client.request("GET", "/_test/export")
        # A20: format_version 3 is accepted since stage 3; 4 remains unknown
        v4 = dict(export, format_version=4)
        status, payload, _ = self.client.request("POST", "/_test/import", v4)
        self.assertEqual(status, 422)
        bad_state_cases = [
            dict(export["state"], authorizations="x"),
            dict(export["state"], authorization_order=["a_missing"]),
            dict(export["state"], authorizations={
                "a_bad": dict(export["state"]["authorizations"]["a_open"],
                              from_user_id="u_ghost")}),
            dict(export["state"], authorizations={
                "a_bad": dict(export["state"]["authorizations"]["a_open"],
                              status="frozen")}),
            dict(export["state"], authorizations={
                "a_bad": dict(export["state"]["authorizations"]["a_open"],
                              expires_at="not-a-time")}),
            dict(export["state"], authorizations={
                "a_bad": dict(export["state"]["authorizations"]["a_open"],
                              captured_amount=99999)}),
            dict(export["state"], authorization_ttl_seconds=0),
            dict(export["state"], payments=[
                dict(export["state"]["payments"][0] if export["state"]["payments"]
                     else {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                           "amount": 5, "currency": "EUR", "note": "", "visibility": "public",
                           "request_id": None, "settlement_id": None,
                           "created_at": export["state"]["payments"][0]["created_at"]
                           if export["state"]["payments"] else "2026-01-01T00:00:00+00:00",
                           "seq": 99}, authorization_id="a_missing")]),
        ]
        for bad in bad_state_cases:
            status, payload, _ = self.client.request(
                "POST", "/_test/import", {"track": "pocketful", "format_version": 2,
                                          "state": bad})
            self.assertEqual(status, 422, bad)
            self.assertEqual(payload["error"]["code"], "validation_failed")
        _, after, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(after["state"], export["state"])  # destination unchanged


if __name__ == "__main__":
    unittest.main()