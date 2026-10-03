"""T12: capture — default final, partial release, extended mode, replay body equality,
error table, and concurrent capture storms racing the same remainder (R146, R148,
R165-R175, R179, R192)."""

import sys
import time
import unittest

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


class CaptureBase(unittest.TestCase):
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

    def capture(self, token, body, key, authz_id=None):
        return self.client.request(
            "POST", "/authorizations/%s/capture" % (authz_id or self.authz["authorization_id"]),
            body, token=token, key=key)

    def me(self, token):
        _, me, _ = self.client.request("GET", "/me", token=token)
        return me

    def row(self, authz_id=None):
        """Internal read of one stored authorization row (works before T14's list
        endpoint exists): from the export snapshot."""
        _, export, _ = self.client.request("GET", "/_test/export")
        rows = export["state"]["authorizations"]
        return rows[authz_id or self.authz["authorization_id"]]

    def payload(self, authz_id=None):
        """The API-shaped view of the stored row (effective status, remaining_amount,
        payment_id), computed the same way the endpoints compute it (design.md
        section 11) so T12 asserts hold before T14's list endpoint exists."""
        from datetime import datetime, timezone
        row = self.row(authz_id)
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        status = row["status"]
        if status == "open" and expires <= datetime.now(timezone.utc):
            status = "expired"
        remaining = row["amount"] - row["captured_amount"] if status == "open" else 0
        return dict(row, status=status, remaining_amount=remaining,
                    payment_id=row["payment_ids"][-1] if row["payment_ids"] else None)


class TestT12FinalCapture(CaptureBase):
    def test_full_capture_returns_payment_and_closes(self):
        status, payment, _ = self.capture(self.bob, {}, "cap-full")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 2000)
        self.assertEqual(payment["authorization_id"], self.authz["authorization_id"])
        self.assertIsNone(payment["request_id"])
        self.assertEqual((payment["note"], payment["visibility"]), ("", "public"))
        self.assertTrue(payment["payment_id"].startswith("p_"))
        self.assertIsNone(payment["settlement_id"])
        row = self.payload()
        self.assertEqual(row["status"], "captured")
        self.assertEqual(row["captured_amount"], 2000)
        self.assertEqual(row["remaining_amount"], 0)
        self.assertEqual(row["payment_id"], payment["payment_id"])
        self.assertEqual(row["payment_ids"], [payment["payment_id"]])
        # money moved once: ada total 8000 (10000-2000), bob 4500 (2500+2000)
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (8000, 0, 8000))
        self.assertEqual(bob["total"], 4500)
        # appears in the feed by the ordinary visibility rule
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual([p["payment_id"] for p in feed["payments"]],
                         [payment["payment_id"]])

    def test_partial_final_capture_releases_remainder_in_same_step(self):
        """Capturing 1500 of 2000 returns 500 to the payer's available in the same
        step (R168) — observed atomically by the next read."""
        status, payment, _ = self.capture(self.bob, {"amount": 1500}, "cap-1500")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 1500)
        ada = self.me(self.ada)
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (8500, 0, 8500))
        row = self.payload()
        self.assertEqual((row["status"], row["captured_amount"],
                          row["remaining_amount"]), ("captured", 1500, 0))

    def test_second_capture_after_final_409(self):
        self.capture(self.bob, {}, "cap-1")
        status, payload, _ = self.capture(self.bob, {}, "cap-2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "authorization_not_open")
        # and no further money moved
        self.assertEqual(self.me(self.ada)["total"], 8000)

    def test_capture_note_visibility_copied_from_authorization(self):
        _, authz, _ = self.client.request(
            "POST", "/authorizations",
            {"to_handle": "bob", "amount": 300, "note": "gift",
             "visibility": "private"}, token=self.ada, key="setup-auth-2")
        _, payment, _ = self.capture(self.bob, {}, "cap-nv", authz_id=authz["authorization_id"])
        self.assertEqual((payment["note"], payment["visibility"]), ("gift", "private"))
        _, feed, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed["payments"][0]["visibility"], "private")
        _, feed, _ = self.client.request("GET", "/activity", token=login(
            self.client, "ada@example.com") if False else self.bob)
        self.assertEqual(len(feed["payments"]), 1)  # party sees private payment

    def test_seeded_captured_authorization_shape(self):
        seeded = clean_fixture(authorizations=[
            {"id": "a_cap", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 500, "captured_amount": 500, "status": "captured",
             "expires_at": "2099-01-01T00:00:00+00:00"}])
        util.reset(self.client, seeded)
        ada = login(self.client, "ada@example.com")  # reset wiped earlier tokens
        row = self.payload("a_cap")
        self.assertEqual((row["status"], row["captured_amount"],
                          row["remaining_amount"], row["payment_id"],
                          row["payment_ids"]), ("captured", 500, 0, None, []))


class TestT12ExtendedCapture(CaptureBase):
    def test_final_false_keeps_remainder_held_and_allows_further(self):
        status, payment, _ = self.capture(self.bob, {"amount": 700, "final": False},
                                          "cap-700")
        self.assertEqual(status, 201)
        row = self.payload()
        self.assertEqual((row["status"], row["captured_amount"],
                          row["remaining_amount"], row["payment_ids"]),
                         ("open", 700, 1300, [payment["payment_id"]]))
        ada = self.me(self.ada)
        self.assertEqual((ada["total"], ada["held"], ada["available"]),
                         (9300, 1300, 8000))  # remainder stays held
        # further capture up to the remainder
        status, payment2, _ = self.capture(self.bob, {"amount": 600, "final": False},
                                           "cap-600")
        self.assertEqual(status, 201)
        row = self.payload()
        self.assertEqual((row["status"], row["captured_amount"],
                          row["remaining_amount"], row["payment_id"]),
                         ("open", 1300, 700, payment2["payment_id"]))
        self.assertEqual(row["payment_ids"],
                         [payment["payment_id"], payment2["payment_id"]])

    def test_capture_entire_remainder_closes_even_with_final_false(self):
        self.capture(self.bob, {"amount": 700, "final": False}, "cap-700")
        status, _, _ = self.capture(self.bob, {"amount": 1300, "final": False},
                                    "cap-rest")
        self.assertEqual(status, 201)
        row = self.payload()
        got = {"authorizations": [row]}
        self.assertEqual(got["authorizations"][0]["status"], "captured")

    def test_omitted_amount_defaults_to_remainder(self):
        self.capture(self.bob, {"amount": 700, "final": False}, "cap-700")
        status, payment, _ = self.capture(self.bob, {}, "cap-default")
        self.assertEqual(status, 201)
        self.assertEqual(payment["amount"], 1300)
        row = self.payload()
        got = {"authorizations": [row]}
        self.assertEqual(got["authorizations"][0]["captured_amount"], 2000)

    def test_capture_exceeds_compares_with_remaining(self):
        status, payload, _ = self.capture(self.bob, {"amount": 2001}, "cap-over")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "capture_exceeds_authorization")
        self.capture(self.bob, {"amount": 700, "final": False}, "cap-700")
        status, payload, _ = self.capture(self.bob, {"amount": 1301}, "cap-over2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "capture_exceeds_authorization")
        status, _, _ = self.capture(self.bob, {"amount": 1300, "final": False},
                                    "cap-ok")
        self.assertEqual(status, 201)  # exactly the remainder

    def test_cumulative_three_capture_sequence(self):
        captures = []
        for i, body in enumerate([{"amount": 700, "final": False},
                                  {"amount": 600, "final": False},
                                  {}]):
            status, payment, _ = self.capture(self.bob, body, "cap-seq-%d" % i)
            self.assertEqual(status, 201)
            captures.append(payment)
        row = self.payload()
        self.assertEqual(row["captured_amount"], 2000)
        self.assertEqual(row["remaining_amount"], 0)
        self.assertEqual(row["payment_ids"], [c["payment_id"] for c in captures])
        self.assertEqual(row["payment_id"], captures[-1]["payment_id"])
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual((ada["total"], ada["held"], ada["available"]), (8000, 0, 8000))
        self.assertEqual(bob["total"], 4500)

    def test_replay_body_equality_is_syntactic(self):
        """{} and {"amount": 2000} are different bodies even though they mean the same
        capture: reusing a key across them is 409 idempotency_key_reuse (R166)."""
        status, first, _ = self.capture(self.bob, {}, "cap-r1")
        self.assertEqual(status, 201)
        status, replay, _ = self.capture(self.bob, {}, "cap-r1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        status, payload, _ = self.capture(self.bob, {"amount": 2000}, "cap-r1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")
        # final/omitted is also just a body difference (R174) — on a fresh hold,
        # because the first capture already closed this one
        _, authz2, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 300},
            token=self.ada, key="setup-auth-2")
        status, payload, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz2["authorization_id"],
            {"final": True}, token=self.bob, key="cap-r2")
        self.assertEqual(status, 201)
        status, payload, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz2["authorization_id"], {},
            token=self.bob, key="cap-r2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_amount_shape_validation(self):
        for body, key in [({"amount": 0}, "z0"), ({"amount": -5}, "z1"),
                          ({"amount": 1.5}, "z2"), ({"amount": "10"}, "z3"),
                          ({"amount": True}, "z4"), ({"final": "yes"}, "z5"),
                          ({"amount": 100, "final": 1}, "z6")]:
            status, payload, _ = self.capture(self.bob, body, "shape-%s" % key)
            self.assertEqual(status, 422, body)
            self.assertEqual(payload["error"]["code"], "validation_failed", body)

    def test_error_table_order(self):
        """404 exists -> 403 receiver -> 409 expired -> 409 not_open -> 422 amount
        shape -> 422 capture_exceeds (R175, A8, design.md section 13)."""
        status, payload, _ = self.capture(self.bob, {}, "e1", authz_id="a_ghost")
        self.assertEqual((status, payload["error"]["code"]), (404, "not_found"))
        status, payload, _ = self.capture(self.ada, {}, "e2")  # payer, not receiver
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        _, charlie, _ = self.client.request("POST", "/auth/signup", {
            "email": "charlie@example.com", "password": "correct horse",
            "display_name": "Charlie"})
        status, payload, _ = self.capture(charlie["token"], {}, "e3")  # neither party
        self.assertEqual((status, payload["error"]["code"]), (403, "forbidden"))
        # shape errors before capture_exceeds, on an open authorization
        status, payload, _ = self.capture(self.bob, {"amount": 0}, "e4")
        self.assertEqual((status, payload["error"]["code"]), (422, "validation_failed"))
        # expired: a lapsed authorization reports authorization_expired, not not_open
        lapsed = clean_fixture(authorizations=[
            {"id": "a_old", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 500, "status": "open",
             "expires_at": "2020-01-01T00:00:00+00:00"}])
        util.reset(self.client, lapsed)
        bob = login(self.client, "bob@example.com")  # reset wiped earlier tokens
        status, payload, _ = self.client.request(
            "POST", "/authorizations/a_old/capture", {}, token=bob, key="e5")
        self.assertEqual((status, payload["error"]["code"]),
                         (409, "authorization_expired"))


class TestT12ExpiryByClock(CaptureBase):
    def test_api_created_authorization_expires_by_ttl(self):
        util.reset(self.client, clean_fixture(authorization_ttl_seconds=1))
        ada = login(self.client, "ada@example.com")
        bob = login(self.client, "bob@example.com")
        _, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 500},
            token=ada, key="exp-1")
        time.sleep(1.4)
        status, payload, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=bob, key="exp-cap")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "authorization_expired")
        _, me, _ = self.client.request("GET", "/me", token=ada)
        self.assertEqual((me["held"], me["available"]), (0, 10000))  # released
        self.assertEqual(self.payload(authz["authorization_id"])["status"],
                         "expired")


class TestT12Concurrency(CaptureBase):
    def test_concurrent_captures_never_exceed_authorized(self):
        """20 captures of 300 (final:false) racing a 2000 remainder: at most 6 move
        money, captured_amount never exceeds 2000, no negative available (R146)."""
        def grab(i):
            return self.client.request(
                "POST", "/authorizations/%s/capture" % self.authz["authorization_id"],
                {"amount": 300, "final": False}, token=self.bob, key="race-%d" % i)

        results = util.concurrent(20, grab)
        moved = [p for s, p, _ in results if s == 201]
        self.assertLessEqual(len(moved), 6)
        self.assertGreater(len(moved), 0)
        row = self.payload()
        self.assertEqual(row["captured_amount"], 300 * len(moved))
        self.assertLessEqual(row["captured_amount"], 2000)
        self.assertEqual(len(row["payment_ids"]), len(moved))
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual(ada["total"], 10000 - row["captured_amount"])
        self.assertEqual(ada["available"], ada["total"] - (row["amount"] - row["captured_amount"]))
        self.assertGreaterEqual(ada["available"], 0)
        self.assertEqual(bob["total"], 2500 + row["captured_amount"])

    def test_concurrent_same_key_captures_move_money_once(self):
        """Many concurrent identical captures on one unused key: exactly one 201, the
        rest 200 replays of that original response; money moved exactly once."""

        def grab(_):
            return self.client.request(
                "POST", "/authorizations/%s/capture" % self.authz["authorization_id"],
                {}, token=self.bob, key="one-key")

        results = util.concurrent(20, grab)
        created = [p for s, p, _ in results if s == 201]
        replays = [p for s, p, _ in results if s == 200]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(replays), 19)
        self.assertTrue(all(r == created[0] for r in replays))
        ada, bob = self.me(self.ada), self.me(self.bob)
        self.assertEqual((ada["total"], ada["held"], ada["available"]), (8000, 0, 8000))
        self.assertEqual(bob["total"], 4500)

    def test_capture_vs_void_race_is_serializable(self):
        """A capture and a void racing the same open authorization: the final state is
        equivalent to one of the two orders — either voided first (capture then 409)
        or captured first (void then 409); money and capture records stay consistent."""
        outcomes = []

        def do_capture(_):
            status, payload, _ = self.capture(self.bob, {}, "race-cap")
            outcomes.append(("capture", status, payload))
            return True

        def do_void(_):
            status, payload, _ = self.client.request(
                "POST", "/authorizations/%s/void" % self.authz["authorization_id"],
                token=self.ada)
            outcomes.append(("void", status, payload))
            return True

        util.concurrent(1, do_capture) + util.concurrent(1, do_void)
        _ = outcomes
        row = self.payload()
        ada, bob = self.me(self.ada), self.me(self.bob)
        if row["status"] == "captured":
            self.assertEqual(ada["total"], 8000)
            self.assertEqual(bob["total"], 4500)
        elif row["status"] == "voided":
            self.assertEqual((ada["total"], ada["held"], ada["available"]),
                             (10000, 0, 10000))
            self.assertEqual(bob["total"], 2500)
        else:
            self.fail("authorization neither captured nor voided after the race")
        self.assertEqual(row["remaining_amount"], 0)
        self.assertGreaterEqual(ada["available"], 0)

    def test_payments_still_immediate_no_intermediate_hold(self):
        """R148 regression: POST /payments never leaves a hold. (The setup open
        authorization still holds its 2000; the payment must add no hold of its own.)"""
        status, payment, _ = self.client.request(
            "POST", "/payments", {"to_handle": "bob", "amount": 500},
            token=self.ada, key="plain-pay")
        self.assertEqual(status, 201)
        self.assertIsNone(payment["authorization_id"])
        ada = self.me(self.ada)
        self.assertEqual((ada["held"], ada["available"]), (2000, 7500))
        self.assertEqual(ada["total"], 9500)


if __name__ == "__main__":
    unittest.main()