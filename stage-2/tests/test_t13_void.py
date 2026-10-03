"""T13: void — payer-only, naturally idempotent by convergence, closed holds
refused, partial-capture records preserved (R173, R176-R179)."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402


def clean_fixture(**extra):
    fixture = util.spec_fixture()
    fixture["payments"] = []
    fixture["requests"] = []
    fixture.update(extra)
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    assert payload and "token" in payload, payload
    return payload["token"]


class VoidBase(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, clean_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        _, self.authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 2000},
            token=self.ada, key="setup-auth")
        assert self.authz.get("status") == "open", self.authz

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def void(self, token, authz_id=None):
        return self.client.request(
            "POST", "/authorizations/%s/void" % (authz_id or self.authz["authorization_id"]),
            token=token)

    def me(self, token):
        _, me, _ = self.client.request("GET", "/me", token=token)
        return me


class TestT13Void(VoidBase):
    def test_payer_void_releases_hold_without_moving_money(self):
        status, authz, _ = self.void(self.ada)
        self.assertEqual(status, 200)
        self.assertEqual(authz["status"], "voided")
        self.assertEqual(authz["remaining_amount"], 0)
        self.assertEqual(authz["payment_id"], None)
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (10000, 0, 10000))  # hold released, nothing moved
        self.assertEqual(bob["total"], 2500)

    def test_only_payer_may_void(self):
        status, payload, _ = self.void(self.bob)  # receiver
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        _, charlie, _ = self.client.request("POST", "/auth/signup", {
            "email": "charlie@example.com", "password": "correct horse",
            "display_name": "Charlie"})
        status, payload, _ = self.void(charlie["token"])  # neither party
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))

    def test_unknown_authorization_404(self):
        status, payload, _ = self.void(self.ada, authz_id="a_ghost")
        self.assertEqual((status, payload["error"]["code"]), (404, "not_found"))

    def test_voiding_already_voided_is_200_no_double_release(self):
        first_status, first, _ = self.void(self.ada)
        self.assertEqual(first_status, 200)
        second_status, second, _ = self.void(self.ada)
        self.assertEqual(second_status, 200)
        self.assertEqual(second, first)  # current state, unchanged
        self.assertEqual(self.me(self.ada)["available"], 10000)  # released once

    def test_void_after_capture_409(self):
        self.client.request("POST", "/authorizations/%s/capture" % self.authz["authorization_id"],
                            {}, token=self.bob, key="cap-1")
        status, payload, _ = self.void(self.ada)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "authorization_not_open")
        # money already moved; hold stays closed
        self.assertEqual(self.me(self.ada)["total"], 8000)

    def test_void_clock_expired_409(self):
        lapsed = clean_fixture(authorizations=[
            {"id": "a_old", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 500, "status": "open",
             "expires_at": "2020-01-01T00:00:00+00:00"}])
        util.reset(self.client, lapsed)
        ada = login(self.client, "ada@example.com")
        status, payload, _ = self.void(ada, authz_id="a_old")
        self.assertEqual((status, payload["error"]["code"]),
                         (409, "authorization_not_open"))  # R178: same code as captured

    def test_void_partially_captured_preserves_capture_records(self):
        """Void and expiry can close a partially captured authorization, release only
        the remainder, and preserve all capture records (R173)."""
        status, payment, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % self.authz["authorization_id"],
            {"amount": 700, "final": False}, token=self.bob, key="cap-700")
        self.assertEqual(status, 201)
        status, authz, _ = self.void(self.ada)
        self.assertEqual(status, 200)
        self.assertEqual(authz["status"], "voided")
        self.assertEqual(authz["captured_amount"], 700)  # records preserved
        self.assertEqual(authz["payment_ids"], [payment["payment_id"]])
        self.assertEqual(authz["payment_id"], payment["payment_id"])
        self.assertEqual(authz["remaining_amount"], 0)  # only the remainder released
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (9300, 0, 9300))  # 700 moved, 1300 released
        self.assertEqual(bob["total"], 3200)  # 2500 + 700
        # the capture's payment still feeds by the ordinary rule
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(len(feed["payments"]), 1)

    def test_void_does_not_need_idempotency_key(self):
        status, _, _ = self.void(self.ada)  # no key header at all
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()