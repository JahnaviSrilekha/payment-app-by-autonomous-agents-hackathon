"""T10: the derived hold model as pure functions (R143-R145, R147, R153-R160, R192).

effective_status / remaining_amount / held / available are functions of stored state
plus an injected `now` — no wall-clock read inside the function (ADR-004), so expiry at
the deadline is tested deterministically.
"""

import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402  (puts src/ on sys.path)
import state as state_mod  # noqa: E402
import testctl  # noqa: E402

PAST = "2020-01-01T00:00:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"
T_PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
T_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def auth_fixture():
    return {
        "currency": "EUR",
        "minor_units": 2,
        "authorization_ttl_seconds": 600,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 2500},
        ],
        "payments": [],
        "requests": [],
        "authorizations": [
            {"id": "a_open", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 2000, "note": "deposit", "visibility": "public",
             "status": "open", "expires_at": FUTURE},
            {"id": "a_lapsed", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 700, "status": "open", "expires_at": PAST},
            {"id": "a_captured", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 500, "captured_amount": 500, "status": "captured",
             "expires_at": FUTURE},
            {"id": "a_voided", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 300, "status": "voided", "expires_at": FUTURE},
            {"id": "a_stored_expired", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 400, "status": "expired", "expires_at": FUTURE},
        ],
    }


def by_id(service, authorization_id):
    return service["authorizations"][authorization_id]


class TestEffectiveStatusPurity(unittest.TestCase):
    """Pure functions of stored state + injected now: no wall-clock read inside."""

    def test_open_before_deadline_is_open(self):
        authz = {"status": "open", "expires_at": FUTURE}
        self.assertEqual(state_mod.effective_status(authz, T_PAST), "open")

    def test_open_at_deadline_is_expired(self):
        authz = {"status": "open", "expires_at": FUTURE}
        at_deadline = state_mod.parse_rfc3339(FUTURE)
        self.assertEqual(state_mod.effective_status(authz, at_deadline), "expired")

    def test_open_after_deadline_is_expired(self):
        authz = {"status": "open", "expires_at": PAST}
        self.assertEqual(state_mod.effective_status(authz, T_FUTURE), "expired")

    def test_closed_statuses_never_expire_again(self):
        for status in ("captured", "voided", "expired"):
            authz = {"status": status, "expires_at": FUTURE}
            self.assertEqual(state_mod.effective_status(authz, T_FUTURE), status)
            self.assertEqual(state_mod.effective_status(authz, T_PAST), status)

    def test_same_inputs_same_answer_repeatedly(self):
        service = testctl.build_from_fixture(auth_fixture())
        authz = by_id(service, "a_open")
        for now in (T_PAST, T_FUTURE):
            first = state_mod.effective_status(authz, now)
            for _ in range(3):
                self.assertEqual(state_mod.effective_status(authz, now), first)

    def test_result_depends_only_on_injected_now(self):
        service = testctl.build_from_fixture(auth_fixture())
        # One stored row, two different injected nows: different answers, no internal
        # clock read (a wall-clock read could not produce both answers for one row).
        authz = by_id(service, "a_lapsed")
        self.assertEqual(state_mod.effective_status(authz, T_PAST), "expired")
        # a_lapsed's expires_at is PAST itself; use an open row whose deadline is far
        # away for the before/after contrast instead.
        authz = by_id(service, "a_open")
        self.assertEqual(state_mod.effective_status(authz, T_PAST), "open")
        self.assertEqual(state_mod.effective_status(authz, T_FUTURE), "expired")


class TestHeldAvailable(unittest.TestCase):
    def test_worked_example_by_hand(self):
        service = testctl.build_from_fixture(auth_fixture())
        now = T_PAST  # a_open (FUTURE deadline) is open; a_lapsed (PAST) is expired
        self.assertEqual(state_mod.held("u_ada", service, now), 2000)
        self.assertEqual(state_mod.available("u_ada", service, now), 8000)
        # bob's seeded authorizations are all closed: nothing held
        self.assertEqual(state_mod.held("u_bob", service, now), 0)
        self.assertEqual(state_mod.available("u_bob", service, now), 2500)

    def test_expiry_release_visible_through_derived_reads(self):
        service = testctl.build_from_fixture(auth_fixture())
        before = state_mod.available("u_ada", service, T_PAST)
        after = state_mod.available("u_ada", service, T_FUTURE)
        self.assertEqual(before, 8000)
        self.assertEqual(after, 10000)  # a_open expired: remainder released
        self.assertEqual(state_mod.held("u_ada", service, T_FUTURE), 0)

    def test_remaining_amount_open_partial(self):
        fixture = auth_fixture()
        fixture["authorizations"][0]["captured_amount"] = 300
        service = testctl.build_from_fixture(fixture)
        now = T_PAST
        self.assertEqual(state_mod.remaining_amount(by_id(service, "a_open"), now), 1700)
        self.assertEqual(state_mod.remaining_amount(by_id(service, "a_captured"), now), 0)
        self.assertEqual(state_mod.held("u_ada", service, now), 1700)

    def test_available_equals_total_minus_held_always(self):
        service = testctl.build_from_fixture(auth_fixture())
        for now in (T_PAST, T_FUTURE):
            for user_id in ("u_ada", "u_bob"):
                total = service["users"][user_id]["balance"]
                self.assertEqual(state_mod.available(user_id, service, now),
                                 total - state_mod.held(user_id, service, now))
                self.assertGreaterEqual(state_mod.available(user_id, service, now), 0)

    def test_holds_move_no_money(self):
        service = testctl.build_from_fixture(auth_fixture())
        self.assertEqual(service["users"]["u_ada"]["balance"], 10000)
        self.assertEqual(service["users"]["u_bob"]["balance"], 2500)

    def test_parse_rfc3339_accepts_z_and_offsets(self):
        self.assertEqual(state_mod.parse_rfc3339("2020-01-01T00:00:00Z"), T_PAST)
        self.assertEqual(state_mod.parse_rfc3339("2020-01-01T01:00:00+01:00"), T_PAST)
        with self.assertRaises(ValueError):
            state_mod.parse_rfc3339("2020-01-01T00:00:00")  # no offset
        with self.assertRaises(ValueError):
            state_mod.parse_rfc3339("not-a-time")
        with self.assertRaises(ValueError):
            state_mod.parse_rfc3339(None)


class TestNewServiceShape(unittest.TestCase):
    def test_new_service_defaults(self):
        service = state_mod.new_service("EUR", 2)
        self.assertEqual(service["authorization_ttl_seconds"], 600)
        self.assertEqual(service["authorizations"], {})
        self.assertEqual(service["authorization_order"], [])


if __name__ == "__main__":
    unittest.main()