"""T32: corrections extensions for refunds (R306-R309; design section 27, A24/A25).
The single-correction endpoint's linked_payment_immutable check gains the refund
disjunct, refund_exceeds_payment guards the already-refunded floor, and the per-item
checks live in corrections.validate_item (shared with the stage-4 batch endpoint,
ADR-009) — every stage-3 correction behaviour passing unchanged."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"


def corrections_fixture():
    return {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 8500},
            {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
             "display_name": "Carol", "handle": "carol", "balance": 10000},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "created_at": T0},
        ],
        "requests": [],
        "settlement_operator_ids": ["u_ada"],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def correct(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/corrections" % payment_id, body,
                          token=token, key=key)


class TestT32CorrectionsRefunds(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, corrections_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_refund_payment_is_linked_immutable(self):
        """R307/A24: refunding a refund payment cannot be corrected — the third
        linked_payment_immutable disjunct, reachable only after a refund exists."""
        _, refund, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 1000},
            token=self.bob, key="r-1")
        status, payload, _ = correct(self.client, self.bob, refund["payment_id"],
                                     {"expected_revision": 1, "amount": 500,
                                      "reason": "fix", "effective_at": T1},
                                     key="c-ref")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")
        # a non-sender of the refund payment is 403 first (A15's sender check
        # precedes the structural check)
        status, payload, _ = correct(self.client, self.ada, refund["payment_id"],
                                     {"expected_revision": 1, "amount": 500,
                                      "reason": "fix", "effective_at": T1},
                                     key="c-ref2")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_capture_still_linked_immutable(self):
        """R307 regression: the capture disjunct survives the refactor."""
        _, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 400},
            token=self.ada, key="a-1")
        _, capture, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=self.bob, key="cap-1")
        status, payload, _ = correct(self.client, self.ada, capture["payment_id"],
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix", "effective_at": T1},
                                     key="c-cap")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")

    def test_correction_below_refunded_total_is_422(self):
        """R308/A25: a correction to an amount below refunded_total is 422
        refund_exceeds_payment, no state change — and it is a 422 that fires
        before the caller-supplied expected_revision's 409 (A25's bucket order)."""
        self.client.request("POST", "/payments/p_1/refunds", {"amount": 2000},
                            token=self.bob, key="r-cap")
        before = self.client.request("GET", "/_test/export")[1]
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 1500,
                                      "reason": "reduce", "effective_at": T1},
                                     key="c-below")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)
        # a stale expected_revision with an equally below-floor amount is still the
        # 422 (refund_exceeds_payment sits before stale_revision, A25)
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 99, "amount": 1500,
                                      "reason": "reduce", "effective_at": T1},
                                     key="c-below2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")

    def test_correction_to_exactly_refunded_total_is_allowed(self):
        """R308's floor is inclusive: amount == refunded_total does not reduce the
        payment below its already-refunded amount, so it commits."""
        self.client.request("POST", "/payments/p_1/refunds", {"amount": 2000},
                            token=self.bob, key="r-cap")
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 2000,
                                      "reason": "down to refunds", "effective_at": T1},
                                     key="c-floor")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["amount"], 2000)
        me_ada = self.client.request("GET", "/me", token=self.ada)[1]
        me_bob = self.client.request("GET", "/me", token=self.bob)[1]
        # correction moved +4000 to ada: the original receiver (bob) funds the decrease (R235)
        self.assertEqual((me_ada["total"], me_bob["total"]), (16000, 2500))
        # now refunds (2000 so far) hit the corrected cap exactly: a further 1 fails
        status, payload, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 1},
            token=self.bob, key="r-after")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")

    def test_zero_refund_total_leaves_corrections_unchanged(self):
        """R306 regression: without refunds, every stage-3 correction behaviour is
        byte-identical — shape errors, staleness, money movement."""
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 1, "amount": 6400,
                                      "reason": "tip", "effective_at": T1},
                                     key="c-reg")
        self.assertEqual(status, 201)
        self.assertEqual(payload["revision"], 2)
        me_ada = self.client.request("GET", "/me", token=self.ada)[1]
        me_bob = self.client.request("GET", "/me", token=self.bob)[1]
        self.assertEqual((me_ada["total"], me_bob["total"]), (9600, 8900))
        status, payload, _ = correct(self.client, self.ada, "p_1",
                                     {"expected_revision": 5, "amount": 100,
                                      "reason": "stale", "effective_at": T1},
                                     key="c-reg2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "stale_revision")

    def test_validate_item_shared_implementation(self):
        """ADR-009: validate_item is the one per-item implementation — the batch
        endpoint (T33) calls it with allow_settlement=True; here the single-endpoint
        variant still rejects settlement members while a direct validate_item call
        with allow_settlement=True accepts the same item, proving the disjunct is
        the only difference."""
        import corrections
        import state as state_mod
        util.reset(self.client, corrections_fixture())
        self.ada = login(self.client, "ada@example.com")
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [{"from_handle": "ada", "to_handle": "bob",
                            "amount": 300}]},
            token=self.ada, key="st-1")
        member_id = settlement["payments"][0]["payment_id"]
        # the endpoint: settlement members stay immutable (R272)
        status, payload, _ = correct(self.client, self.ada, member_id,
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix", "effective_at": T1},
                                     key="c-st")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")
        # the shared helper with the batch's flag: no linked_payment_immutable
        body = {"expected_revision": 1, "amount": 100, "reason": "fix",
                "effective_at": T1}
        with state_mod.STATE_LOCK:
            payment, amount, delta, effective_raw = corrections.validate_item(
                state_mod.get(), member_id, body,
                state_mod.parse_rfc3339(T1), allow_settlement=True)
            self.assertEqual((payment["id"], amount, delta), (member_id, 100, -200))
            self.assertEqual(effective_raw, T1)


if __name__ == "__main__":
    unittest.main()