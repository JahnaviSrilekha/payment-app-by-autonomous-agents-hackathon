"""T23: the payment-revision / balance_view foundation (R193-R197, R214-R218, R270,
R271 payment side). Unit tests prove select_revision/balance_view are pure functions of
stored state plus the two injected instants; reset-level tests prove seeded created_at,
revision-1 construction (incl. settlement members' committed_at on both axes, R271),
future-created_at rejection (R196) and the nonnegative-history check (R218)."""

import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import ledger  # noqa: E402
import state as state_mod  # noqa: E402

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T0_S = "2026-01-01T10:00:00+00:00"
T1_S = "2026-01-01T11:00:00+00:00"
T2_S = "2026-01-01T12:00:00+00:00"


def revision(n, amount, effective_at, recorded_at, reason=""):
    return {"revision": n, "amount": amount, "effective_at": effective_at,
            "recorded_at": recorded_at, "reason": reason}


def payment(pid, from_id, to_id, amount, created_at, revisions=None):
    return {"id": pid, "from_user_id": from_id, "to_user_id": to_id,
            "amount": amount, "currency": "EUR", "note": "", "visibility": "public",
            "request_id": None, "authorization_id": None, "settlement_id": None,
            "revisions": revisions if revisions is not None
            else [ledger.initial_revision(amount, created_at)],
            "created_at": created_at, "seq": 0}


def hand_built_service():
    """ada opens at 10000 (base_balance), pays 4000 (T0), receives 1500 (T1), pays
    2000 (T2) -> live 5500. bob opens at 0, live 4500. R216: base_balance is the
    balance minus the net effect of the original payments."""
    service = state_mod.new_service("EUR", 2)
    service["users"]["u_ada"] = {"id": "u_ada", "handle": "ada", "balance": 5500,
                                 "base_balance": 10000}
    service["users"]["u_bob"] = {"id": "u_bob", "handle": "bob", "balance": 4500,
                                 "base_balance": 0}
    service["payments"] = [
        payment("p_a", "u_ada", "u_bob", 4000, T0_S),
        payment("p_b", "u_bob", "u_ada", 1500, T1_S),
        payment("p_c", "u_ada", "u_bob", 2000, T2_S),
    ]
    return service


class TestLedgerPure(unittest.TestCase):
    """Pure functions: stored state plus the two injected instants, no wall clock."""

    def setUp(self):
        self.service = hand_built_service()
        state_mod.set_state(self.service)
        self.ada = self.service["users"]["u_ada"]
        self.bob = self.service["users"]["u_bob"]

    def tearDown(self):
        state_mod.set_state(None)

    def test_initial_revision_is_revision_one(self):
        """R214: revision 1 is the original amount, effective = recorded = created_at,
        reason \"\"."""
        rev = ledger.initial_revision(4000, T0_S)
        self.assertEqual(rev, {"revision": 1, "amount": 4000, "effective_at": T0_S,
                               "recorded_at": T0_S, "reason": ""})

    def test_select_revision_latest_recorded_at_or_before_known_at(self):
        """R247: latest revision recorded at or before known_at (boundary inclusive);
        R248: known_at None means everything known when the read begins."""
        p = payment("p_x", "u_ada", "u_bob", 4000, T0_S, [
            revision(1, 4000, T0_S, T0_S),
            revision(2, 3000, T2_S, T2_S, "oops"),
        ])
        self.assertEqual(ledger.select_revision(p, T0)["revision"], 1)
        self.assertEqual(ledger.select_revision(p, T1)["revision"], 1)
        self.assertEqual(ledger.select_revision(p, T2)["revision"], 2)
        self.assertEqual(ledger.select_revision(p, None)["revision"], 2)

    def test_select_revision_none_when_nothing_recorded_yet(self):
        """R247: if no revision was yet recorded the payment contributes nothing."""
        p = payment("p_x", "u_ada", "u_bob", 4000, T2_S)
        self.assertIsNone(ledger.select_revision(p, T0))
        self.assertIsNone(ledger.select_revision(p, T1))

    def test_signed_amount_receiver_positive_sender_negative(self):
        p = self.service["payments"][0]  # ada -> bob 4000
        rev = p["revisions"][0]
        self.assertEqual(ledger.signed_amount(p, "u_bob", rev), 4000)
        self.assertEqual(ledger.signed_amount(p, "u_ada", rev), -4000)

    def test_balance_view_now_now_equals_live_balance_for_every_user(self):
        """balance_view(user, +inf, +inf) equals the live User.balance by construction,
        for every user of the hand-built set (done-test)."""
        for user in (self.ada, self.bob):
            self.assertEqual(ledger.balance_view(user, None, None, service=self.service),
                             user["balance"])

    def test_balance_view_before_earliest_payment_is_base_balance(self):
        """balance_view(user, T, +inf) for T before the earliest payment equals
        base_balance (R203's opening)."""
        before = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            ledger.balance_view(self.ada, before, None, service=self.service), 10000)
        self.assertEqual(
            ledger.balance_view(self.bob, before, None, service=self.service), 0)

    def test_balance_view_payment_at_exactly_as_of_counts(self):
        """R201: a payment at exactly as_of counts as happened; R202: at/after the
        latest payment returns the current balance."""
        self.assertEqual(
            ledger.balance_view(self.ada, T1, None, service=self.service), 7500)
        self.assertEqual(
            ledger.balance_view(self.ada, T2, None, service=self.service), 5500)
        later = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            ledger.balance_view(self.ada, later, None, service=self.service), 5500)

    def test_balance_view_strict_before_excludes_the_boundary(self):
        """R210: 'immediately before from/to' is the strict variant of the same test."""
        self.assertEqual(
            ledger.balance_view(self.ada, T1, None, strict_before=True,
                                service=self.service), 6000)
        self.assertEqual(
            ledger.balance_view(self.ada, T0, None, strict_before=True,
                                service=self.service), 10000)

    def test_balance_view_uses_selected_revision_under_known_at(self):
        """R249/R256: the selected revision is applied by its effective time; R247:
        nothing recorded yet contributes nothing; R216: base_balance never moves."""
        corrected = payment("p_d", "u_ada", "u_bob", 4000, T0_S, [
            revision(1, 4000, T0_S, T0_S),
            revision(2, 1000, T2_S, T2_S, "correction"),
        ])
        self.service["payments"].append(corrected)
        # the correction moves the live balances (amount 4000 -> 1000: ada +3000)
        self.service["users"]["u_ada"]["balance"] = 4500
        self.service["users"]["u_bob"]["balance"] = 5500
        # fully known: the correction applies at its effective time T2
        self.assertEqual(ledger.balance_view(self.ada, None, None,
                                             service=self.service), 4500)
        self.assertEqual(ledger.balance_view(self.bob, None, None,
                                             service=self.service), 5500)
        # known at T1: p_d's revision 2 is not recorded yet (revision 1 applies) and
        # p_c (recorded at T2) is not yet recorded at all — contributes nothing (R247)
        self.assertEqual(ledger.balance_view(self.ada, None, T1,
                                             service=self.service), 3500)
        self.assertEqual(ledger.balance_view(self.bob, None, T1,
                                             service=self.service), 6500)
        # as_of T1 fully known: p_d's selected revision takes effect at T2, p_c too —
        # neither counts at as_of T1 (R249, R250)
        self.assertEqual(ledger.balance_view(self.ada, T1, None,
                                             service=self.service), 7500)

    def test_balance_view_zero_amount_revision_is_zero_delta(self):
        """R257 groundwork: a zero-amount revision selects cleanly and adds nothing."""
        p = payment("p_z", "u_ada", "u_bob", 500, T0_S, [
            revision(1, 500, T0_S, T0_S),
            revision(2, 0, T1_S, T1_S, "reversed"),
        ])
        self.service["payments"].append(p)
        # selected at T1 is revision 2 (amount 0): ada's view is unchanged by p_z
        self.assertEqual(ledger.balance_view(self.ada, T1, None,
                                             service=self.service), 7500)

    def test_ledger_functions_are_pure_no_wall_clock(self):
        """Done-test: no wall-clock read inside — patch the clock helpers to explode
        and call every function with fixed inputs; results are stable across calls."""
        import unittest.mock

        with unittest.mock.patch.object(state_mod, "now_utc",
                                        side_effect=AssertionError("wall clock read")):
            for _ in range(2):
                self.assertEqual(
                    ledger.balance_view(self.ada, T1, T1, service=self.service), 7500)
                self.assertEqual(
                    ledger.select_revision(self.service["payments"][0], T1)["revision"],
                    1)
                self.assertEqual(
                    ledger.signed_amount(self.service["payments"][0], "u_ada",
                                         self.service["payments"][0]["revisions"][0]),
                    -4000)


class TestResetSeeding(unittest.TestCase):
    """Reset-level behaviour: R193/R195/R196/R197/R215/R218, R271 payment side."""

    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def seeded_fixture(self):
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
                 "amount": 500, "created_at": "2026-01-01T10:00:00+00:00",
                 "settlement_id": "s_1"},
                {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 200},
            ],
            "requests": [],
            "settlement_operator_ids": ["u_ada"],
        }

    def export(self):
        status, payload, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(status, 200)
        return payload["state"]

    def login(self, email):
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": email, "password": "correct horse"})
        return payload["token"]

    def test_seeded_created_at_and_revision_one_reproduced_exactly(self):
        """Done-test: one explicit created_at, one omitted; revision 1 reproduces
        created_at / effective_at / recorded_at exactly (R193, R195, R214, R215)."""
        util.reset(self.client, self.seeded_fixture())
        state = self.export()
        by_id = {p["id"]: p for p in state["payments"]}
        self.assertEqual(by_id["p_1"]["created_at"], "2026-01-01T10:00:00+00:00")
        self.assertEqual(by_id["p_1"]["revisions"], [{
            "revision": 1, "amount": 500,
            "effective_at": "2026-01-01T10:00:00+00:00",
            "recorded_at": "2026-01-01T10:00:00+00:00", "reason": "",
        }])
        # the response itself carries the instant with its offset (R193)
        ada = self.login("ada@example.com")
        _, feed, _ = self.client.request("GET", "/activity", token=ada)
        seeded = [p for p in feed["payments"] if p["payment_id"] == "p_1"][0]
        self.assertEqual(seeded["created_at"], "2026-01-01T10:00:00+00:00")
        # omitted -> reset time; revision 1 carries it on both axes
        self.assertEqual(by_id["p_2"]["revisions"][0]["effective_at"],
                         by_id["p_2"]["created_at"])
        self.assertEqual(by_id["p_2"]["revisions"][0]["recorded_at"],
                         by_id["p_2"]["created_at"])

    def test_omitted_created_at_is_before_api_created_payments(self):
        """R195: reset time comes before subsequent API-created payments."""
        util.reset(self.client, self.seeded_fixture())
        ada = self.login("ada@example.com")
        self.client.request("POST", "/payments", {"to_handle": "bob", "amount": 100},
                            token=ada, key="api-1")
        state = self.export()
        by_id = {p["id"]: p for p in state["payments"]}
        api_id = [p["id"] for p in state["payments"]
                  if p["id"] not in ("p_1", "p_2")][0]
        self.assertLessEqual(state_mod.parse_rfc3339(by_id["p_2"]["created_at"]),
                             state_mod.parse_rfc3339(by_id[api_id]["created_at"]))

    def test_seeded_settlement_member_revision_uses_committed_at(self):
        """R271: a settlement member's original revision uses its committed_at as both
        effective_at and recorded_at — for a seed, its supplied created_at."""
        util.reset(self.client, self.seeded_fixture())
        state = self.export()
        seeded_member = [p for p in state["payments"] if p["id"] == "p_1"][0]
        self.assertEqual(seeded_member["settlement_id"], "s_1")
        self.assertEqual(seeded_member["revisions"][0]["effective_at"],
                         seeded_member["created_at"])
        self.assertEqual(seeded_member["revisions"][0]["recorded_at"],
                         seeded_member["created_at"])
        # an API-created settlement member: committed_at on both axes (R271)
        _, settlement, _ = self.client.request(
            "POST", "/settlements",
            {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 300}]},
            token=self.login("ada@example.com"), key="st-1")
        state = self.export()
        member = [p for p in state["payments"]
                  if p["settlement_id"] == settlement["settlement_id"]][0]
        self.assertEqual(member["revisions"][0]["effective_at"], member["created_at"])
        self.assertEqual(member["revisions"][0]["recorded_at"], member["created_at"])

    def test_future_created_at_is_422_and_changes_nothing(self):
        """R196: a seeded created_at in the future gives 422 validation_failed from
        POST /_test/reset, with no state change."""
        util.reset(self.client, self.seeded_fixture())
        before = self.export()
        bad = self.seeded_fixture()
        bad["payments"][0]["created_at"] = "2999-01-01T00:00:00+00:00"
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), before)

    def test_naive_created_at_is_422(self):
        """R193: an offset is required — a naive local time is 422, nothing changes."""
        util.reset(self.client, self.seeded_fixture())
        before = self.export()
        bad = self.seeded_fixture()
        bad["payments"][0]["created_at"] = "2026-01-01T10:00:00"
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), before)

    def test_overdrawing_seed_history_is_422_and_changes_nothing(self):
        """R218: a seeded history that would go negative at some point is 422 with no
        state change. ada's replay: opening 10500 -> -500 after p_1, while both seeded
        ending balances stay nonnegative, so only the sweep catches it."""
        util.reset(self.client, self.seeded_fixture())
        before = self.export()
        bad = self.seeded_fixture()
        bad["payments"] = [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 11000, "created_at": "2026-01-01T10:00:00+00:00"},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 11000, "created_at": "2026-01-01T10:00:01+00:00"},
            {"id": "p_3", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 11500, "created_at": "2026-01-01T10:00:02+00:00"},
            {"id": "p_4", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 11000, "created_at": "2026-01-01T10:00:03+00:00"},
        ]
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), before)

    def test_negative_opening_balance_is_422_even_if_replayed_nonnegative(self):
        """R218: the opening balance is iteration 0 of the replay — a user whose only
        seeded payment is one they RECEIVE computes a negative base_balance, and the
        single receiving payment alone must not hide it (reviewer reproduction)."""
        util.reset(self.client, self.seeded_fixture())
        before = self.export()
        bad = self.seeded_fixture()
        # bob (balance 2500) receives 5000 and sent nothing: base_balance = -2500,
        # yet the single receiving payment replays to a nonnegative 2500 — only the
        # opening check catches it.
        bad["payments"] = [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 5000, "created_at": "2020-01-01T00:00:00+00:00"},
        ]
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.export(), before)

    def test_seed_history_touching_zero_is_accepted(self):
        """R218's boundary: replaying down to exactly 0 is nonnegative, so valid
        (ada opens at 12000, pays 12000 -> touches 0, is paid back 10000)."""
        fixture = self.seeded_fixture()
        fixture["payments"] = [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 12000, "created_at": "2026-01-01T10:00:00+00:00"},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 10000, "created_at": "2026-01-01T11:00:00+00:00"},
        ]
        status, _, _ = self.client.request("POST", "/_test/reset", fixture)
        self.assertEqual(status, 204)
        state = self.export()
        # R216: base_balance = seeded ending balance minus the net of originals
        self.assertEqual(state["users"]["u_ada"]["balance"], 10000)
        self.assertEqual(state["users"]["u_ada"]["base_balance"], 12000)
        self.assertEqual(state["users"]["u_bob"]["base_balance"], 500)

    def test_seeded_balance_is_the_ending_balance_and_base_balance_reconciles(self):
        """R197: loading seeded payments must not change the given balance; R216:
        base_balance equals balance minus the net effect of the original payments."""
        util.reset(self.client, self.seeded_fixture())
        ada = self.login("ada@example.com")
        me = self.client.request("GET", "/me", token=ada)[1]
        self.assertEqual(me["balance"], 10000)
        state = self.export()
        self.assertEqual(state["users"]["u_ada"]["balance"], 10000)
        self.assertEqual(state["users"]["u_ada"]["base_balance"], 10300)  # 10000 +500 -200
        self.assertEqual(state["users"]["u_bob"]["base_balance"], 2200)  # +500 -200


if __name__ == "__main__":
    unittest.main()