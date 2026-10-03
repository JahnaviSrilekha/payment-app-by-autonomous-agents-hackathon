"""T28: corrections — request shape/validation, money movement, error precedence
(R219-R245, R272, R275, R283-R285, A15; R269's concurrency rule). Every error is
independently triggerable and, where more than one would apply to the same request,
in A15's documented order."""

import sys
import threading
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"
T2 = "2026-01-01T12:00:00+00:00"


def corrections_fixture(with_operator=True):
    fixture = {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 2500},
            {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
             "display_name": "Carol", "handle": "carol", "balance": 10000},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "created_at": T0},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 6000, "created_at": T1},
        ],
        "requests": [],
    }
    if with_operator:
        fixture["settlement_operator_ids"] = ["u_ada"]
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def correct(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/corrections" % payment_id, body,
                          token=token, key=key)


class TestT28Corrections(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, corrections_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        self.carol = login(self.client, "carol@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def revisions(self, payment_id, token):
        status, payload, _ = self.client.request(
            "GET", "/payments/%s/revisions" % payment_id, token=token)
        self.assertEqual(status, 200, payload)
        return payload["revisions"]

    def test_unknown_payment_is_404(self):
        """R221: an unknown payment gets 404 (after auth and key, A15)."""
        status, payload, _ = correct(self.client, self.ada, "p_nope",
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix",
                                      "effective_at": T2}, key="c-404")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_non_sender_is_403(self):
        """R220: an authenticated non-sender gets 403 forbidden."""
        status, payload, _ = correct(self.client, self.bob, "p_1",
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix",
                                      "effective_at": T2}, key="c-403")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_field_validation_is_422_per_field(self):
        """R222-R227: every field's own shape, each independently triggerable."""
        base = {"expected_revision": 1, "amount": 5000, "reason": "overcharge",
                "effective_at": T2}
        cases = [
            ({**base, "expected_revision": 0}, "expected_revision"),
            ({**base, "expected_revision": -1}, "expected_revision"),
            ({**base, "expected_revision": "1"}, "expected_revision"),
            ({**base, "amount": -1}, "amount"),
            ({**base, "amount": 1000000001}, "amount"),
            ({**base, "amount": "5000"}, "amount"),
            ({**base, "reason": ""}, "reason"),
            ({**base, "reason": "x" * 201}, "reason"),
            ({**base, "reason": 7}, "reason"),
            ({**base, "effective_at": "2026-01-01T12:00:00"}, "effective_at"),
            ({**base, "effective_at": "2026-01-01"}, "effective_at"),
            ({**base, "effective_at": 7}, "effective_at"),
            ({**base, "effective_at": "2999-01-01T00:00:00+00:00"}, "effective_at"),
            ({"expected_revision": 1, "amount": 5000, "effective_at": T2}, "reason"),
            ({"amount": 5000, "reason": "r", "effective_at": T2}, "expected_revision"),
            ({"expected_revision": 1, "reason": "r", "effective_at": T2}, "amount"),
        ]
        for body, field in cases:
            status, payload, _ = correct(self.client, self.ada, "p_1", body,
                                         key="c-shape-%s" % field)
            self.assertEqual(status, 422, (body, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")
            self.assertIn(field, payload["error"]["message"])

    def test_settlement_member_and_capture_are_immutable(self):
        """R272/R275: 422 linked_payment_immutable for a settlement member and, in
       dependently, for a capture."""
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 300}]},
            token=self.ada, key="st-1")
        member_id = settlement["payments"][0]["payment_id"] \
            if "payments" in settlement else None
        if member_id is None:  # settlement response carries payment_ids directly
            member_id = settlement["payment_ids"][0]
        status, payload, _ = correct(self.client, self.ada, member_id,
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix", "effective_at": T2},
                                     key="c-settle")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")
        status, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 400},
            token=self.ada, key="auth-1")
        _, capture, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=self.bob, key="cap-1")
        status, payload, _ = correct(self.client, self.ada, capture["payment_id"],
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix", "effective_at": T2},
                                     key="c-cap")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")

    def test_stale_revision_is_409(self):
        """R231: expected_revision != len(revisions) gives 409 stale_revision."""
        for expected in (2,):
            status, payload, _ = correct(self.client, self.ada, "p_1",
                                         {"expected_revision": expected,
                                          "amount": 5000, "reason": "fix",
                                          "effective_at": T2},
                                         key="c-stale-%d" % expected)
            self.assertEqual(status, 409, (expected, payload))
            self.assertEqual(payload["error"]["code"], "stale_revision")

    def test_insufficient_funds_before_historical_overdraft(self):
        """R236/R285 vs R284 ordering: an increase the sender cannot afford NOW is
        insufficient_funds even though the historical sweep would also fail."""
        util.reset(self.client, corrections_fixture())
        util.reset(self.client, {
            "currency": "EUR", "minor_units": 2,
            "users": [
                {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                 "display_name": "Ada", "handle": "ada", "balance": 10000},
                {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
                 "display_name": "Bob", "handle": "bob", "balance": 3000},
            ],
            "payments": [
                {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 3000, "created_at": T0},
                {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 2000, "created_at": T2},
            ],
            "requests": [],
            "authorizations": [
                {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 5000, "status": "open", "created_at": T1,
                 "expires_at": "2099-01-01T00:00:00+00:00"},
            ],
        })
        ada = login(self.client, "ada@example.com")
        # live available(ada) = 10000 - 5000 = 5000; delta +7000 -> insufficient_funds
        status, payload, _ = correct(self.client, ada, "p_1",
                                     {"expected_revision": 1, "amount": 10000,
                                      "reason": "bigger", "effective_at": T0},
                                     key="c-precedence")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")

    def test_historical_overdraft_on_the_available_leg(self):
        """R284's available leg: the live check passes (delta exactly at available),
        but at the hold-creation boundary the historical available would go negative."""
        util.reset(self.client, {
            "currency": "EUR", "minor_units": 2,
            "users": [
                {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                 "display_name": "Ada", "handle": "ada", "balance": 10000},
                {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
                 "display_name": "Bob", "handle": "bob", "balance": 3000},
            ],
            "payments": [
                {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 3000, "created_at": T0},
                {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 2000, "created_at": T2},
            ],
            "requests": [],
            "authorizations": [
                {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 5000, "status": "open", "created_at": T1,
                 "expires_at": "2099-01-01T00:00:00+00:00"},
            ],
        })
        ada = login(self.client, "ada@example.com")
        # live available(ada) = 5000; delta +5000 passes the live check exactly
        status, payload, _ = correct(self.client, ada, "p_1",
                                     {"expected_revision": 1, "amount": 8000,
                                      "reason": "bigger", "effective_at": T0},
                                     key="c-hist")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "historical_overdraft")
        # at the T0 boundary ada's total would be 3000 while at T1 (hold creation)
        # available = 3000 - 5000 = -2000

    def test_historical_overdraft_total_leg_live_check_passes(self):
        """R237/R284's total leg, constructed so the historical check alone rejects:
        ada's live balance (10000) affords the +6000 increase, but her balance at the
        p_1 boundary would go to -2000 once the corrected amount applies there."""
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 12000,
                                      "reason": "bigger", "effective_at": T0},
                                     key="c-hist-total")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "historical_overdraft")

    def test_failure_preserves_everything(self):
        """R239: a failed correction leaves balances, revisions, statements and
        idempotency state unchanged — the same key can then complete a valid one."""
        before = self.client.request("GET", "/_test/export")[1]
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 12000,
                                      "reason": "bigger", "effective_at": T0},
                                     key="c-retry")
        self.assertEqual(status, 409)
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 5000,
                                      "reason": "smaller", "effective_at": T1},
                                     key="c-retry")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["revision"], 2)

    def test_successful_corrections_move_only_the_difference(self):
        """R234/R235/R229: an increase debits the original sender and credits the
        receiver; a decrease reverses; the response carries the full shape."""
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 6400,
                                      "reason": "tip", "effective_at": T1},
                                     key="c-up")
        self.assertEqual(status, 201)
        self.assertEqual(payload, {"payment_id": "p_1", "revision": 2,
                                   "amount": 6400, "effective_at": T1,
                                   "recorded_at": payload["recorded_at"],
                                   "reason": "tip"})
        me_ada = self.client.request("GET", "/me", token=self.ada)[1]
        me_bob = self.client.request("GET", "/me", token=self.bob)[1]
        # live balances moved by the +400 delta only (R234)
        self.assertEqual((me_ada["total"], me_bob["total"]), (9600, 2900))
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 2, "amount": 6000,
                                      "reason": "undo", "effective_at": T1},
                                     key="c-down")
        self.assertEqual(status, 201)
        me_ada = self.client.request("GET", "/me", token=self.ada)[1]
        me_bob = self.client.request("GET", "/me", token=self.bob)[1]
        self.assertEqual((me_ada["total"], me_bob["total"]), (10000, 2500))

    def test_recorded_at_strictly_increases(self):
        """R230: two corrections in the same second still record strictly later."""
        first = correct(self.client, self.ada, "p_1",
                        {"expected_revision": 1, "amount": 6100, "reason": "a",
                         "effective_at": T1}, key="c-rec-1")[1]
        second = correct(self.client, self.ada, "p_1",
                         {"expected_revision": 2, "amount": 6200, "reason": "b",
                          "effective_at": T1}, key="c-rec-2")[1]
        self.assertLess(state_mod.parse_rfc3339(first["recorded_at"]),
                        state_mod.parse_rfc3339(second["recorded_at"]))

    def test_replay_and_reuse(self):
        """R232/R233: a same-key replay returns the original revision with 200 even
        after newer revisions exist; a different body under the same key is 409."""
        original = correct(self.client, self.ada, "p_1",
                           {"expected_revision": 1, "amount": 6100, "reason": "a",
                            "effective_at": T1}, key="c-idem")[1]
        self.assertEqual(original["revision"], 2)
        correct(self.client, self.ada, "p_1",
                {"expected_revision": 2, "amount": 6200, "reason": "b",
                 "effective_at": T1}, key="c-later")
        replay_status, replay, _ = correct(self.client, self.ada, "p_1",
                                           {"expected_revision": 1, "amount": 6100,
                                            "reason": "a", "effective_at": T1},
                                           key="c-idem")
        self.assertEqual(replay_status, 200)
        self.assertEqual(replay, original)
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 6100,
                                      "reason": "different", "effective_at": T1},
                                     key="c-idem")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_missing_key_is_400(self):
        """R219: a correction requires an idempotency key."""
        status, payload, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 100, "reason": "fix",
             "effective_at": T2}, token=self.ada)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")

    def test_revisions_endpoint(self):
        """R243/R244/R245: all revisions in order including revision 1 with reason
        \"\"; only the two parties can read it (third party 404 even on a public
        payment); no token is 401."""
        correct(self.client, self.ada, "p_1",
                {"expected_revision": 1, "amount": 6100, "reason": "a",
                 "effective_at": T1}, key="c-rev")
        rows = self.revisions("p_1", self.ada)
        self.assertEqual([r["revision"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["reason"], "")
        self.assertEqual(rows[0]["amount"], 6000)
        self.assertEqual(rows[1]["reason"], "a")
        bob_rows = self.revisions("p_1", self.bob)
        self.assertEqual(bob_rows, rows)
        status, payload, _ = self.client.request(
            "GET", "/payments/p_1/revisions", token=self.carol)
        self.assertEqual(status, 404)
        status, payload, _ = self.client.request("GET", "/payments/p_1/revisions")
        self.assertEqual(status, 401)

    def test_original_payment_and_feed_unchanged(self):
        """R241/R242: the original payment and the activity feed stay byte-for-byte —
        corrections are not new feed entries."""
        _, feed_before, _ = self.client.request("GET", "/activity", token=self.ada)
        correct(self.client, self.ada, "p_1",
                {"expected_revision": 1, "amount": 6100, "reason": "a",
                 "effective_at": T1}, key="c-feed")
        _, feed_after, _ = self.client.request("GET", "/activity", token=self.ada)
        self.assertEqual(feed_before, feed_after)
        seeded = [p for p in feed_after["payments"] if p["payment_id"] == "p_1"][0]
        self.assertEqual((seeded["amount"], seeded["created_at"]), (6000, T0))

    def test_historical_total_follows_revisions(self):
        """R283: the historical total follows the effective/recorded-time rules —
        verified against ledger.balance_view, because GET /me's as_of wiring is
        batch 3's T25 and this batch does not build on it."""
        import ledger
        from datetime import datetime, timezone

        correct(self.client, self.ada, "p_1",
                {"expected_revision": 1, "amount": 6400, "reason": "tip",
                 "effective_at": T0}, key="c-283")
        boundary = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        before = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        with state_mod.STATE_LOCK:
            service = state_mod.get()
            ada = service["users"]["u_ada"]
            # the corrected amount applies at its effective instant T0
            self.assertEqual(ledger.balance_view(ada, boundary, None,
                                                 service=service), 3600)
            # before any payment: the opening (base_balance, corrections-free)
            self.assertEqual(ledger.balance_view(ada, before, None,
                                                 service=service), 10000)

    def test_concurrent_corrections_same_expected_revision(self):
        """R269/R231/R240 under load: exactly one of 50 racing corrections with the
        same expected_revision succeeds (the rest stale_revision), and the sum of
        balances equals the seeded total throughout (concurrent readers)."""
        expected_total = 10000 + 2500 + 10000
        start = threading.Barrier(50)
        results = [None] * 50
        violations = []

        def worker(i):
            start.wait()
            status, payload, _ = correct(self.client, self.ada, "p_1",
                                         {"expected_revision": 1, "amount": 6100,
                                          "reason": "race", "effective_at": T1},
                                         key="cc-%d" % i)
            results[i] = (status, payload)
            me_ada = self.client.request("GET", "/me", token=self.ada)[1]
            me_bob = self.client.request("GET", "/me", token=self.bob)[1]
            me_carol = self.client.request("GET", "/me", token=self.carol)[1]
            total = me_ada["total"] + me_bob["total"] + me_carol["total"]
            if total != expected_total:
                violations.append(total)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        created = [r for r in results if r[0] == 201]
        stale = [r for r in results if r[0] == 409
                 and r[1]["error"]["code"] == "stale_revision"]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(stale), 49)
        self.assertEqual(violations, [])

    def test_zero_amount_correction_reverses(self):
        """R224: amount 0 reverses the entire payment — the receiver gives the
        original amount back (R235: a decrease debits the original receiver), so
        this uses a payment bob can afford to return."""
        util.reset(self.client, {
            "currency": "EUR", "minor_units": 2,
            "users": [
                {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                 "display_name": "Ada", "handle": "ada", "balance": 10000},
                {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
                 "display_name": "Bob", "handle": "bob", "balance": 2500},
            ],
            "payments": [
                {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 500, "created_at": T0},
            ],
            "requests": [],
        })
        ada = login(self.client, "ada@example.com")
        status, payload, _ = correct(self.client, ada, "p_1",
                                     {"expected_revision": 1, "amount": 0,
                                      "reason": "refund", "effective_at": T1},
                                     key="c-zero")
        self.assertEqual(status, 201)
        self.assertEqual(payload["amount"], 0)
        me_ada = self.client.request(
            "GET", "/me", token=login(self.client, "ada@example.com"))[1]
        me_bob = self.client.request(
            "GET", "/me", token=login(self.client, "bob@example.com"))[1]
        self.assertEqual((me_ada["total"], me_bob["total"]), (10500, 2000))
        # the reversal still appears as a revision with zero amount (R257)
        rows = self.revisions("p_1", ada)
        self.assertEqual(rows[-1]["amount"], 0)

    def test_conservation_through_mixed_corrections(self):
        """R240: the seeded total is preserved across a randomized correction mix."""
        import random
        random.seed(20260301)
        for i in range(20):
            amount = random.randrange(0, 12000)
            expected = len(self.revisions("p_1", self.ada))
            status, payload, _ = correct(self.client, self.ada, "p_1",
                                         {"expected_revision": expected,
                                          "amount": amount, "reason": "r%d" % i,
                                          "effective_at": T1},
                                         key="c-mix-%d" % i)
            if status == 201:
                me_ada = self.client.request("GET", "/me", token=self.ada)[1]
                me_bob = self.client.request("GET", "/me", token=self.bob)[1]
                me_carol = self.client.request("GET", "/me", token=self.carol)[1]
                self.assertEqual(me_ada["total"] + me_bob["total"] + me_carol["total"],
                                 22500)


if __name__ == "__main__":
    unittest.main()