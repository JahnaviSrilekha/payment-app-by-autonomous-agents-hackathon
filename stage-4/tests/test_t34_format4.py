"""T34: format_version 4 — export/import of the two new stored fields (R305's
import defaulting, R325's interface, A28, R334). Export emits format_version 4 with
refund_of on every payment and correction_batch_id on every revision; import accepts
1-4, defaulting both fields to null below 4 and referentially validating refund_of
at 4 (nothing imported on any failure)."""

import copy
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"
T2 = "2026-01-01T12:00:00+00:00"


def fixture():
    return {
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
        "settlement_operator_ids": ["u_ada"],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


class TestT34Format4(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        # a refund, a single correction and a batch correction: all three shapes
        _, _, _ = self.client.request("POST", "/payments/p_1/refunds",
                                      {"amount": 2000}, token=self.bob, key="r-1")
        _, _, _ = self.client.request(
            "POST", "/payments/p_1/corrections",
            {"expected_revision": 1, "amount": 5000, "reason": "single",
             "effective_at": T2}, token=self.ada, key="c-1")
        _, _, _ = self.client.request(
            "POST", "/correction-batches",
            {"corrections": [{"payment_id": "p_2", "expected_revision": 1,
                              "amount": 5500, "effective_at": T2,
                              "reason": "batch"}]},
            token=self.ada, key="b-1")
        _, self.export_v4, _ = self.client.request("GET", "/_test/export")
        state = self.export_v4["state"]
        self.refund_payment = [p for p in state["payments"]
                               if p.get("refund_of")][0]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def export(self):
        return self.client.request("GET", "/_test/export")[1]

    def set_version(self, export, version):
        export = copy.deepcopy(export)
        export["format_version"] = version
        return export

    def test_export_emits_v4_with_fields_everywhere(self):
        """A28: the export is format_version 4 and carries refund_of on every
        payment and correction_batch_id on every revision — null except where the
        field is genuinely set."""
        self.assertEqual(self.export_v4["format_version"], 4)
        for payment in self.export_v4["state"]["payments"]:
            self.assertIn("refund_of", payment)
            for revision in payment["revisions"]:
                self.assertIn("correction_batch_id", revision)
        self.assertEqual(self.refund_payment["refund_of"], "p_1")
        # the batch-created revision carries the real batch id, every other
        # revision null
        p_2 = [p for p in self.export_v4["state"]["payments"] if p["id"] == "p_2"][0]
        batch_id = p_2["revisions"][1]["correction_batch_id"]
        self.assertTrue(batch_id and batch_id.startswith("cb_"))
        p_1 = [p for p in self.export_v4["state"]["payments"] if p["id"] == "p_1"][0]
        self.assertEqual([r["correction_batch_id"] for r in p_1["revisions"]],
                         [None, None])

    def test_import_v1_v2_v3_defaults_null_everywhere(self):
        """A28/R334: importing an older export strips neither data nor behaviour —
        every payment's refund_of and every revision's correction_batch_id default
        to null for versions below 4."""
        for version, mutate in [
                (3, lambda s: [p.pop("refund_of", None) or
                               [r.pop("correction_batch_id", None)
                                for r in p["revisions"]] for p in s["payments"]]),
                (2, lambda s: [p.pop("revisions") for p in s["payments"]] and
                              [u.pop("base_balance", None) for u in
                               s["users"].values()]),
                (1, lambda s: (s.pop("authorizations"), s.pop("authorization_order")))]:
            payload = self.set_version(self.export_v4, version)
            if version >= 3:
                mutate(payload["state"])
            if version == 2:
                mutate(payload["state"])
            status, imported, _ = self.client.request("POST", "/_test/import", payload)
            self.assertEqual(status, 204, (version, imported))
            after = self.export()
            self.assertEqual(after["format_version"], 4)
            for payment in after["state"]["payments"]:
                self.assertIsNone(payment["refund_of"], version)
                for revision in payment["revisions"]:
                    self.assertIsNone(revision["correction_batch_id"], version)
            # the re-imported (formerly refund) payment is now an ordinary
            # payment; its receiver is ada, so bob is 403 — the shape survived
            status, payload, _ = self.client.request(
                "POST", "/payments/%s/refunds" % self.refund_payment["id"],
                {"amount": 100}, token=self.bob, key="r-after-%d" % version)
            self.assertEqual(status, 403)
            self.assertEqual(payload["error"]["code"], "forbidden")
        # reset back to the v4 world for the rest of the suite's isolation
        util.reset(self.client, fixture())

    def test_v4_round_trip_preserves_fields_exactly(self):
        """A28's round trip: export -> import -> export is byte-identical, and the
        refund rules behave identically against the re-imported state."""
        status, _, _ = self.client.request("POST", "/_test/import",
                                           copy.deepcopy(self.export_v4))
        self.assertEqual(status, 204)
        self.assertEqual(self.export(), self.export_v4)
        # refund_exceeds_payment: p_1's current corrected amount is 5000 with 2000
        # refunded, so only 3000 more fits
        status, payload, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 3001},
            token=self.bob, key="r-trip")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "refund_exceeds_payment")
        status, _, _ = self.client.request(
            "POST", "/payments/p_1/refunds", {"amount": 3000},
            token=self.bob, key="r-trip2")
        self.assertEqual(status, 201)
        # invalid_refund_target: the re-imported refund payment cannot be refunded
        status, payload, _ = self.client.request(
            "POST", "/payments/%s/refunds" % self.refund_payment["id"],
            {"amount": 100}, token=self.ada, key="r-trip3")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "invalid_refund_target")
        # the batch id survived: GET /revisions still shows it on the batch revision
        rows = self.client.request("GET", "/payments/p_2/revisions",
                                   token=self.ada)[1]["revisions"]
        self.assertTrue(rows[1]["correction_batch_id"].startswith("cb_"))

    def test_v4_refund_of_referential_integrity(self):
        """A28: a format-4 refund_of naming a payment absent from the export is 422
        validation_failed with nothing imported (self-reference included); a valid
        reference imports."""
        bad = copy.deepcopy(self.export_v4)
        bad["state"]["payments"][0]["refund_of"] = "p_nope"
        status, payload, _ = self.client.request("POST", "/_test/import", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), self.export_v4)  # nothing imported
        self_ref = copy.deepcopy(self.export_v4)
        self_ref["state"]["payments"][0]["refund_of"] = \
            self_ref["state"]["payments"][0]["id"]
        status, payload, _ = self.client.request("POST", "/_test/import", self_ref)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), self.export_v4)
        # a corrected reference imports cleanly
        good = copy.deepcopy(self.export_v4)
        good["state"]["payments"][0]["refund_of"] = good["state"]["payments"][1]["id"]
        status, _, _ = self.client.request("POST", "/_test/import", good)
        self.assertEqual(status, 204)

    def test_v4_correction_batch_id_structural(self):
        """A28: a revision's correction_batch_id must be a string or null at v4 —
        any other JSON type is 422 with nothing imported."""
        bad = copy.deepcopy(self.export_v4)
        bad["state"]["payments"][0]["revisions"][0]["correction_batch_id"] = 5
        status, payload, _ = self.client.request("POST", "/_test/import", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), self.export_v4)

    def test_unknown_format_version_is_422(self):
        """A28: only 1, 2, 3 and 4 import; any other version is 422."""
        status, payload, _ = self.client.request(
            "POST", "/_test/import", self.set_version(self.export_v4, 5))
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        status, payload, _ = self.client.request(
            "POST", "/_test/import", self.set_version(self.export_v4, 0))
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")


if __name__ == "__main__":
    unittest.main()