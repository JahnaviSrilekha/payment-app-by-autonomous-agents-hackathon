"""T33: POST /correction-batches (R310-R331, R333; design section 28, A26, ADR-009).
Six-phase precedence, first failing item in input order, settlement completeness,
combined affordability, combined historical sweep with all tentatives popped on
failure, one shared recorded_at and one correction_batch_id on success."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"
T2 = "2026-01-01T12:00:00+00:00"


def batch_fixture(operator="u_ada"):
    fixture = {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 13000},
            {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
             "display_name": "Carol", "handle": "carol", "balance": 10000},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "created_at": T0},
            {"id": "p_2", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "created_at": T1},
        ],
        "requests": [],
    }
    if operator:
        fixture["settlement_operator_ids"] = [operator]
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def batch(client, token, corrections, key):
    return client.request("POST", "/correction-batches", {"corrections": corrections},
                          token=token, key=key)


def item(payment_id, amount, expected_revision=1, effective_at=T2, reason="fix"):
    return {"payment_id": payment_id, "expected_revision": expected_revision,
            "amount": amount, "effective_at": effective_at, "reason": reason}


class TestT33CorrectionBatches(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, batch_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        self.carol = login(self.client, "carol@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def export(self):
        return self.client.request("GET", "/_test/export")[1]

    def test_request_shape_is_422(self):
        """R311/R312: missing/non-array corrections, 0 and 33 items, non-object
        elements, missing or non-string payment_id, duplicate payment_ids."""
        cases = [
            ({}, "corrections"),
            ({"corrections": "no"}, "corrections"),
            ({"corrections": []}, "1 to 32"),
            ({"corrections": [item("p_nope_%d" % i, 100) for i in range(33)]},
             "1 to 32"),
            ({"corrections": [{"expected_revision": 1, "amount": 100,
                               "effective_at": T2, "reason": "r"}]}, "payment_id"),
            ({"corrections": [item(7, 100)]}, "payment_id"),
            ({"corrections": [item("p_1", 100), item("p_1", 200)]}, "distinct"),
        ]
        for body, fragment in cases:
            status, payload, _ = batch(self.client, self.ada, body["corrections"]
                                       if "corrections" in body else body,
                                       key="b-shape-%r" % sorted(body)[:1])
            self.assertEqual(status, 422, (body, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")
            self.assertIn(fragment, payload["error"]["message"])

    def test_element_not_object_is_422(self):
        """R312: a non-object element is 422 before any lookup."""
        status, payload, _ = batch(self.client, self.ada, ["nope"], key="b-nonobj")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_per_item_errors_in_input_order(self):
        """R313/R314: each per-item code is reachable and the first failing item in
        input order wins — a valid item followed by an unknown payment is 404, while
        a failing item before an unknown payment is that item's own 422."""
        status, payload, _ = batch(self.client, self.ada,
                                   [item("p_1", 6100), item("p_nope", 100)],
                                   key="b-order-404")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        status, payload, _ = batch(self.client, self.ada,
                                   [item("p_1", -1), item("p_nope", 100)],
                                   key="b-order-422")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertIn("amount", payload["error"]["message"])
        # each remaining per-item code is reachable on its own item
        for corrections, code in [
                ([item("p_1", 6100, expected_revision=9)], "stale_revision"),
                ([item("p_1", 6100,
                       effective_at="2999-01-01T00:00:00+00:00")],
                 "validation_failed")]:
            status, payload, _ = batch(self.client, self.ada, corrections,
                                       key="b-code-%s" % code)
            self.assertEqual(status, 422 if code != "stale_revision" else 409,
                             (corrections, payload))
            self.assertEqual(payload["error"]["code"], code)

    def test_captures_and_refunds_immutable_in_batches(self):
        """R315: captures and refunds stay linked_payment_immutable in a batch even
        for the operator; ordinary payments are eligible."""
        _, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 400},
            token=self.ada, key="a-1")
        _, capture, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=self.bob, key="cap-1")
        status, payload, _ = batch(self.client, self.ada,
                                   [item(capture["payment_id"], 100)],
                                   key="b-cap")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")
        _, refund, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 1000},
            token=self.bob, key="r-1")
        status, payload, _ = batch(self.client, self.ada,
                                   [item(refund["payment_id"], 100)],
                                   key="b-ref")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")

    def test_non_operator_403_after_item_validation(self):
        """R310/A26 phase 3: a non-operator with all-valid items is 403; a
        non-operator whose item is itself invalid gets the item's error first."""
        status, payload, _ = batch(self.client, self.bob, [item("p_1", 6100)],
                                   key="b-op")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")
        status, payload, _ = batch(self.client, self.bob, [item("p_nope", 100)],
                                   key="b-nonop-404")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_missing_key_is_400(self):
        """R310: a batch requires an idempotency key."""
        status, payload, _ = self.client.request(
            "POST", "/correction-batches", {"corrections": [item("p_1", 100)]},
            token=self.ada)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")

    def test_incomplete_settlement_is_422(self):
        """R316: correcting one member of a two-member settlement without the other
        is 422 incomplete_settlement."""
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [
                {"from_handle": "ada", "to_handle": "bob", "amount": 300},
                {"from_handle": "bob", "to_handle": "carol", "amount": 200}]},
            token=self.ada, key="st-1")
        m1, m2 = settlement["payments"][0]["payment_id"], \
            settlement["payments"][1]["payment_id"]
        status, payload, _ = batch(self.client, self.ada, [item(m1, 250)],
                                   key="b-inc")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "incomplete_settlement")
        before = self.export()
        status, payload, _ = batch(self.client, self.ada,
                                   [item(m2, 150)], key="b-inc2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "incomplete_settlement")
        self.assertEqual(self.export(), before)

    def test_settlement_effective_instants_must_match_offset_tolerantly(self):
        """R317: members of one settlement must share one effective instant —
        compared by parsed instant, so Z and +00:00 spellings do NOT trip it,
        while two different instants are 422 validation_failed."""
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [
                {"from_handle": "ada", "to_handle": "bob", "amount": 300},
                {"from_handle": "bob", "to_handle": "carol", "amount": 200}]},
            token=self.ada, key="st-1")
        m1 = settlement["payments"][0]["payment_id"]
        m2 = settlement["payments"][1]["payment_id"]
        status, payload, _ = batch(self.client, self.ada,
                                   [item(m1, 250, effective_at="2026-01-01T12:00:00Z"),
                                    item(m2, 150,
                                         effective_at="2026-01-01T13:00:00+01:00")],
                                   key="b-inst")
        self.assertEqual(status, 201, payload)  # same instant, different spellings
        # fresh settlement for the mismatch case (the first batch committed)
        util.reset(self.client, batch_fixture())
        self.ada = login(self.client, "ada@example.com")
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [
                {"from_handle": "ada", "to_handle": "bob", "amount": 300},
                {"from_handle": "bob", "to_handle": "carol", "amount": 200}]},
            token=self.ada, key="st-2")
        m1 = settlement["payments"][0]["payment_id"]
        m2 = settlement["payments"][1]["payment_id"]
        status, payload, _ = batch(self.client, self.ada,
                                   [item(m1, 250, effective_at=T0),
                                    item(m2, 150, effective_at=T2)],
                                   key="b-inst2")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_combined_insufficient_funds(self):
        """R322: the batch's net per-user effect is checked collectively — each leg
        alone is affordable, the batch of both is not (409), nothing committed."""
        status, payload, _ = batch(self.client, self.ada, [item("p_1", 12000)],
                                   key="b-net1")
        self.assertEqual(status, 201, payload)  # one leg alone is affordable
        util.reset(self.client, batch_fixture())
        self.ada = login(self.client, "ada@example.com")
        before = self.export()
        status, payload, _ = batch(self.client, self.ada,
                                   [item("p_1", 12000), item("p_2", 12000)],
                                   key="b-net2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "insufficient_funds")
        self.assertEqual(self.export(), before)

    def test_combined_historical_overdraft_pops_every_tentative(self):
        """R320's final phase / R323: the combined sweep rejects with 409
        historical_overdraft and every tentative revision is popped — history,
        balances and idempotency records identical to the pre-attempt state."""
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
            "settlement_operator_ids": ["u_ada"],
            "authorizations": [
                {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
                 "amount": 5000, "status": "open", "created_at": T1,
                 "expires_at": "2099-01-01T00:00:00+00:00"},
            ],
        })
        self.ada = login(self.client, "ada@example.com")
        # p_2 -> 2100 (up) leaves the batch's combined net at -4900 against ada's
        # available 5000 (phase 5 passes on the combined effect); p_1 -> 8000 then
        # breaks the historical available at the hold-creation boundary
        before = self.export()
        status, payload, _ = batch(self.client, self.ada,
                                   [item("p_2", 2100, effective_at=T2),
                                    item("p_1", 8000, effective_at=T0)],
                                   key="b-hist")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "historical_overdraft")
        self.assertEqual(self.export(), before)

    def test_successful_batch_shape(self):
        """R324/R325: 201 with correction_batch_id, one shared recorded_at strictly
        later than every member's prior recorded_at, revisions in input order, each
        entry exposing correction_batch_id; money moved per item; the stored
        revisions carry the batch id."""
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [
                {"from_handle": "ada", "to_handle": "bob", "amount": 300},
                {"from_handle": "bob", "to_handle": "carol", "amount": 200}]},
            token=self.ada, key="st-1")
        m1 = settlement["payments"][0]["payment_id"]
        m2 = settlement["payments"][1]["payment_id"]
        prior = self.client.request("GET", "/payments/%s/revisions" % m1,
                                    token=self.ada)[1]["revisions"]
        prior_2 = self.client.request("GET", "/payments/%s/revisions" % m2,
                                      token=self.bob)[1]["revisions"]
        self.assertEqual([r["revision"] for r in prior], [1])
        status, payload, _ = batch(self.client, self.ada,
                                   [item(m1, 250, reason="smaller"),
                                    item(m2, 150, reason="smaller")],
                                   key="b-ok")
        self.assertEqual(status, 201, payload)
        self.assertTrue(payload["correction_batch_id"].startswith("cb_"))
        revisions = payload["revisions"]
        self.assertEqual([r["payment_id"] for r in revisions], [m1, m2])
        self.assertEqual([r["amount"] for r in revisions], [250, 150])
        self.assertTrue(all(r["correction_batch_id"] == payload["correction_batch_id"]
                            for r in revisions))
        self.assertTrue(all(r["recorded_at"] == payload["recorded_at"]
                            for r in revisions))
        # the shared recorded_at is strictly later than every member's prior one
        batch_at = state_mod.parse_rfc3339(payload["recorded_at"])
        self.assertGreater(batch_at, state_mod.parse_rfc3339(prior[0]["recorded_at"]))
        self.assertGreater(batch_at,
                           state_mod.parse_rfc3339(prior_2[0]["recorded_at"]))
        me_bob = self.client.request("GET", "/me", token=self.bob)[1]
        me_carol = self.client.request("GET", "/me", token=self.carol)[1]
        me_ada = self.client.request("GET", "/me", token=self.ada)[1]
        # m1 -50 (bob funds), m2 -50 (carol funds): conservation holds (R322 net)
        self.assertEqual(me_ada["total"] + me_bob["total"] + me_carol["total"],
                         33000)
        # batch revisions are visible through GET /payments/{id}/revisions
        after = self.client.request("GET", "/payments/%s/revisions" % m2,
                                    token=self.bob)[1]["revisions"]
        self.assertEqual([r["revision"] for r in after], [1, 2])

    def test_replay_returns_original_200_body(self):
        """R330: a same-key replay returns the original batch response with 200; a
        different body under the same key is 409 idempotency_key_reuse."""
        _, original, _ = batch(self.client, self.ada, [item("p_1", 6100)],
                               key="b-idem")
        batch(self.client, self.ada, [item("p_1", 6200)], key="b-second")
        status, replay, _ = batch(self.client, self.ada, [item("p_1", 6100)],
                                  key="b-idem")
        self.assertEqual(status, 200)
        self.assertEqual(replay, original)
        status, payload, _ = batch(self.client, self.ada, [item("p_1", 6300)],
                                   key="b-idem")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_unknown_fields_ignored(self):
        """R319: unknown fields in a batch item are ignored."""
        entry = item("p_1", 6100)
        entry["unexpected"] = {"a": 1}
        status, payload, _ = batch(self.client, self.ada, [entry], key="b-unk")
        self.assertEqual(status, 201, payload)

    def test_size_1_batch_matches_single_correction(self):
        """ADR-009: a size-1 batch behaves identically to the equivalent single
        correction — same money movement, same revision numbering, same response
        fields modulo the batch's own id/recorded_at."""
        _, single_response, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 6100, "reason": "fix",
             "effective_at": T2}, token=self.ada, key="c-single")
        self.assertEqual(single_response["revision"], 2)
        single_balances = (self.client.request("GET", "/me", token=self.ada)[1]["total"],
                           self.client.request("GET", "/me", token=self.bob)[1]["total"])
        util.reset(self.client, batch_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        status, batch_response, _ = batch(self.client, self.ada,
                                          [item("p_1", 6100)], key="b-single")
        self.assertEqual(status, 201)
        entry = batch_response["revisions"][0]
        batch_balances = (self.client.request("GET", "/me", token=self.ada)[1]["total"],
                          self.client.request("GET", "/me", token=self.bob)[1]["total"])
        self.assertEqual(single_balances, batch_balances)
        self.assertEqual(entry["payment_id"], single_response["payment_id"])
        self.assertEqual(entry["revision"], single_response["revision"])
        self.assertEqual(entry["amount"], single_response["amount"])
        self.assertEqual(entry["effective_at"], single_response["effective_at"])
        self.assertEqual(entry["reason"], single_response["reason"])
        self.assertNotIn("correction_batch_id", single_response)

    def test_rejected_batch_leaves_no_state(self):
        """R323: a rejected batch leaves history, balances and idempotency records
        unchanged, and the same key can then complete a valid batch."""
        before = self.export()
        status, payload, _ = batch(self.client, self.ada,
                                   [item("p_1", 6100), item("p_nope", 100)],
                                   key="b-retry")
        self.assertEqual(status, 404)
        self.assertEqual(self.export(), before)
        status, payload, _ = batch(self.client, self.ada, [item("p_1", 6100)],
                                   key="b-retry")
        self.assertEqual(status, 201, payload)


if __name__ == "__main__":
    unittest.main()