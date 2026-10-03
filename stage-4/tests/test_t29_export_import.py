"""T29: export/import at format_version 3 (R273, R274, A20; R275 regression owned by
T28). A stage-3-to-stage-3 round trip preserves correction history and base_balance
as stored data; stage-1/2 exports import with revision-1-only histories and freshly
computed base_balance; anything else is 422 with the destination unchanged."""

import copy
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import auth as auth_mod  # noqa: E402
import ledger  # noqa: E402
import state as state_mod  # noqa: E402

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"
T2 = "2026-01-01T12:00:00+00:00"

REAL_HASH = auth_mod.hash_password("correct horse")


def corrections_fixture():
    return {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 2500},
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 6000, "created_at": T0},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 6000, "created_at": T1},
        ],
        "requests": [],
        "settlement_operator_ids": ["u_ada"],
        "authorizations": [
            {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 2000, "status": "open", "created_at": T2,
             "expires_at": "2099-01-01T00:00:00+00:00"},
        ],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def correct(client, token, payment_id, body, key):
    return client.request("POST", "/payments/%s/corrections" % payment_id, body,
                          token=token, key=key)


def export(client):
    status, payload, _ = client.request("GET", "/_test/export")
    assert status == 200, payload
    return payload


def import_state(client, payload):
    return client.request("POST", "/_test/import", payload)


class TestT29ExportImport(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def live_snapshot_of(self, token):
        """What must survive the round trip. /statement and /me's as_of wiring are
        batch 3/4 scope (not built on here), so the temporal views are checked
        against ledger.balance_view on the in-process state instead."""
        return {
            "revisions": self.client.request(
                "GET", "/payments/p_1/revisions", token=token)[1],
            "me": self.client.request("GET", "/me", token=token)[1],
        }

    def historical_views(self):
        """The (as_of, known_at) views the round trip must reproduce, computed from
        the in-process state with the one shared balance function."""
        with state_mod.STATE_LOCK:
            service = state_mod.get()
            ada = service["users"]["u_ada"]
            views = {
                "boundary_known": ledger.balance_view(
                    ada, datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                    datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc),
                    service=service),
                "opening": ledger.balance_view(
                    ada, datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
                    None, service=service),
                "live": ledger.balance_view(ada, None, None, service=service),
            }
        return views

    def test_export_is_always_format_version_4(self):
        """A28: export always emits format_version 4, never an earlier version."""
        util.reset(self.client, corrections_fixture())
        self.assertEqual(export(self.client)["format_version"], 4)
        util.reset(self.client, util.spec_fixture())
        self.assertEqual(export(self.client)["format_version"], 4)

    def test_round_trip_preserves_corrections_holds_and_views(self):
        """Done-test: a stage-3 service with live corrections and historical holds
        round-trips: /revisions, /me?as_of&known_at and /statement are identical
        before and after; base_balance is carried, not re-derived (A20)."""
        util.reset(self.client, corrections_fixture())
        ada = login(self.client, "ada@example.com")
        correct(self.client, ada, "p_1",
                {"expected_revision": 1, "amount": 5400, "reason": "overcharge",
                 "effective_at": T1}, key="t29-1")
        correct(self.client, ada, "p_1",
                {"expected_revision": 2, "amount": 5400, "reason": "restate",
                 "effective_at": T0}, key="t29-2")
        before = self.live_snapshot_of(ada)
        before_view = self.historical_views()
        exported = export(self.client)
        state = exported["state"]
        self.assertEqual(state["users"]["u_ada"]["base_balance"], 10000)
        self.assertEqual(state["users"]["u_ada"]["balance"], 10600)
        self.assertEqual(len(state["payments"][0]["revisions"]), 3)
        self.assertEqual(import_state(self.client, exported), (204, None, None))
        ada_after = login(self.client, "ada@example.com")
        after = self.live_snapshot_of(ada_after)
        self.assertEqual(after["revisions"], before["revisions"])
        self.assertEqual(after["me"], before["me"])
        self.assertEqual(self.historical_views(), before_view)
        # the imported revisions are the one source of truth: at known_at 11:30 no
        # correction is recorded yet (corrections are recorded at real-now), so
        # revision 1 (amount 6000) applies at T0 and the historical total is 4000
        with state_mod.STATE_LOCK:
            service = state_mod.get()
            ada_user = service["users"]["u_ada"]
            self.assertEqual(ada_user["base_balance"], 10000)
            self.assertEqual(
                ledger.balance_view(
                    ada_user, datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                    datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc),
                    service=service), 4000)
            self.assertEqual(
                ledger.balance_view(
                    ada_user, datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc),
                    None, service=service), 10600)  # live view == live balance

    def test_stage1_and_stage2_exports_import_cleanly(self):
        """R273/R274: a genuine stage-1-shaped export (no authorizations, no
        revisions/base_balance) and a stage-2-shaped export (holds and a capture)
        both import: revision-1-only histories, freshly computed base_balance, and
        holds accounted for in held/available immediately."""
        stage1 = {
            "track": "pocketful",
            "format_version": 1,
            "state": {
                "currency": "EUR", "minor_units": 2,
                "users": {
                    "u_ada": {"id": "u_ada", "email": "ada@example.com",
                              "password_hash": dict(REAL_HASH), "display_name": "Ada",
                              "handle": "ada", "balance": 9500},
                    "u_bob": {"id": "u_bob", "email": "bob@example.com",
                              "password_hash": dict(REAL_HASH), "display_name": "Bob",
                              "handle": "bob", "balance": 3000},
                },
                "handles": {"ada": "u_ada", "bob": "u_bob"},
                "emails": {"ada@example.com": "u_ada", "bob@example.com": "u_bob"},
                "tokens": {},
                "settlement_operator_ids": [],
                "payments": [
                    {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                     "amount": 500, "currency": "EUR", "note": "coffee",
                     "visibility": "public", "request_id": None,
                     "settlement_id": None, "created_at": T0, "seq": 1},
                ],
                "requests": {}, "request_order": [], "idempotency": [],
                "next_seq": 2,
            },
        }
        self.assertEqual(import_state(self.client, stage1), (204, None, None))
        state = export(self.client)["state"]
        self.assertEqual(state["users"]["u_ada"]["base_balance"], 10000)  # 9500 +500
        self.assertEqual(state["payments"][0]["revisions"], [{
            "revision": 1, "amount": 500, "effective_at": T0, "recorded_at": T0,
            "reason": "", "correction_batch_id": None,
        }])
        stage2 = {
            "track": "pocketful",
            "format_version": 2,
            "state": {
                "currency": "EUR", "minor_units": 2,
                "users": {
                    "u_ada": {"id": "u_ada", "email": "ada@example.com",
                              "password_hash": dict(REAL_HASH), "display_name": "Ada",
                              "handle": "ada", "balance": 8000},
                    "u_bob": {"id": "u_bob", "email": "bob@example.com",
                              "password_hash": dict(REAL_HASH), "display_name": "Bob",
                              "handle": "bob", "balance": 4500},
                },
                "handles": {"ada": "u_ada", "bob": "u_bob"},
                "emails": {"ada@example.com": "u_ada", "bob@example.com": "u_bob"},
                "tokens": {},
                "settlement_operator_ids": [],
                "payments": [
                    {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                     "amount": 500, "currency": "EUR", "note": "coffee",
                     "visibility": "public", "request_id": None,
                     "settlement_id": None, "created_at": T0, "seq": 1},
                    {"id": "p_cap", "from_user_id": "u_ada", "to_user_id": "u_bob",
                     "amount": 1500, "currency": "EUR", "note": "capture",
                     "visibility": "public", "request_id": None,
                     "settlement_id": None, "authorization_id": "a_1",
                     "created_at": T2, "seq": 3},
                ],
                "requests": {}, "request_order": [],
                "authorization_ttl_seconds": 600,
                "authorizations": {
                    "a_1": {"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                            "amount": 1500, "captured_amount": 1500,
                            "currency": "EUR", "note": "capture",
                            "visibility": "public", "status": "captured",
                            "expires_at": "2026-01-01T10:30:00+00:00",
                            "created_at": T2, "seq": 2, "payment_ids": ["p_cap"]},
                    "a_open": {"id": "a_open", "from_user_id": "u_ada",
                               "to_user_id": "u_bob", "amount": 700,
                               "captured_amount": 0, "currency": "EUR",
                               "note": "hold", "visibility": "public",
                               "status": "open",
                               "expires_at": "2099-01-01T00:00:00+00:00",
                               "created_at": T2, "seq": 4, "payment_ids": []},
                },
                "authorization_order": ["a_1", "a_open"],
                "idempotency": [],
                "next_seq": 5,
            },
        }
        self.assertEqual(import_state(self.client, stage2), (204, None, None))
        ada_token = login(self.client, "ada@example.com")
        me = self.client.request("GET", "/me", token=ada_token)[1]
        # held 700 immediately after import (R274): the capture is spent money
        self.assertEqual((me["total"], me["held"], me["available"]),
                         (8000, 700, 7300))
        state = export(self.client)["state"]
        self.assertEqual(state["users"]["u_ada"]["base_balance"], 10000)  # 8000 +2000
        # a correction of the imported capture is 422 linked_payment_immutable
        status, payload, _ = correct(self.client, ada_token, "p_cap",
                                     {"expected_revision": 1, "amount": 100,
                                      "reason": "fix", "effective_at": T2},
                                     key="t29-cap")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "linked_payment_immutable")

    def test_unknown_versions_and_invalid_states_are_422(self):
        """R273's limit: any other format_version, or a structurally invalid
        format-3 state, is 422 with the destination unchanged."""
        util.reset(self.client, corrections_fixture())
        ada = login(self.client, "ada@example.com")
        correct(self.client, ada, "p_1",
                {"expected_revision": 1, "amount": 5400, "reason": "overcharge",
                 "effective_at": T1}, key="t29-bad")
        good = export(self.client)
        for version in (0, 5, "3"):
            broken = copy.deepcopy(good)
            broken["format_version"] = version
            self.assertEqual(import_state(self.client, broken)[0], 422, version)

        def mangled(mutate):
            broken = copy.deepcopy(good)
            mutate(broken["state"])
            return broken

        revisions = "payments[0]'s revisions"
        cases = [
            ("missing revisions", lambda s: s["payments"][0].pop("revisions")),
            ("empty revisions", lambda s: s["payments"][0]["revisions"].clear()),
            ("missing base_balance", lambda s: s["users"]["u_ada"].pop("base_balance")),
            ("negative base_balance", lambda s: s["users"]["u_ada"]
             .__setitem__("base_balance", -5)),
            ("negative revision amount", lambda s: s["payments"][0]["revisions"][-1]
             .__setitem__("amount", -1)),
            ("non-increasing recorded_at", lambda s: s["payments"][0]["revisions"][1]
             .__setitem__("recorded_at", s["payments"][0]["revisions"][0]
                          ["recorded_at"])),
        ]
        for label, mutate in cases:
            self.assertEqual(import_state(self.client, mangled(mutate))[0], 422,
                             label)
        # R239-style: the destination is unchanged after every rejected import
        self.assertEqual(export(self.client), good)
        del revisions

    def test_snapshots_never_exported_and_state_still_imports(self):
        """R265/design section 23: statement_snapshots are never exported, even when
        one exists in memory. /statement itself is batch 4's scope (not built on
        here), so the snapshot record is planted directly in the shape batch 4
        stores; the export must drop it and the import must still succeed."""
        util.reset(self.client, corrections_fixture())
        ada = login(self.client, "ada@example.com")
        with state_mod.STATE_LOCK:
            state_mod.get()["statement_snapshots"] = {
                "snap_planted": {
                    "user_id": "u_ada", "entries": [], "opening_balance": 10000,
                    "closing_balance": 10000, "from_used": None, "to_used": T2,
                    "known_at_used": T2,
                }
            }
        exported = export(self.client)
        self.assertNotIn("statement_snapshots", exported["state"])
        self.assertEqual(import_state(self.client, exported), (204, None, None))
        self.client.request("GET", "/me", token=login(self.client, "ada@example.com"))


if __name__ == "__main__":
    unittest.main()