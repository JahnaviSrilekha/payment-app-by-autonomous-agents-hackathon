"""T25: GET /me with as_of/known_at (R198-R204, R246-R253, R276 payment-side wiring,
R281). The no-params response stays byte-for-byte stage-2 (R200); any supplied pair
drives all four money fields through the same view (R276). Corrections land as
directly constructed revisions (T28's endpoint does not exist yet)."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T8 = "2026-01-01T08:00:00+00:00"
T9 = "2026-01-01T09:00:00+00:00"
T10 = "2026-01-01T10:00:00+00:00"
T11 = "2026-01-01T11:00:00+00:00"
T12 = "2026-01-01T12:00:00+00:00"
T930 = "2026-01-01T09:30:00+00:00"
FUTURE = "2027-01-01T00:00:00+00:00"

# p_2 @09:00 bob->ada 200; p_1 @10:00 ada->bob 500 (corrected to 400 @09:30,
# recorded 12:00); p_3 @11:00 ada->bob 300. ada: base 10600, live 10100.
# Hold a_hold: ada->bob 3000, open, created 09:30, expires 2099.


def me_fixture():
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
             "amount": 500, "created_at": T10},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 200, "created_at": T9},
            {"id": "p_3", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 300, "created_at": T11},
        ],
        "requests": [],
        "authorizations": [
            {"id": "a_hold", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 3000, "status": "open", "created_at": T930,
             "expires_at": "2099-01-01T00:00:00+00:00"},
        ],
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def append_correction():
    """Land the correction without T28's endpoint (T25's done-test blesses this):
    p_1 500 -> 400, effective 09:30, recorded 12:00; live balances move R235-style."""
    with state_mod.STATE_LOCK:
        service = state_mod.get()
        payment = next(p for p in service["payments"] if p["id"] == "p_1")
        payment["revisions"].append({
            "revision": 2, "amount": 400, "effective_at": T930,
            "recorded_at": T12, "reason": "overcharge",
        })
        service["users"]["u_ada"]["balance"] += 100
        service["users"]["u_bob"]["balance"] -= 100


def q(instant):
    return instant.replace("+", "%2B")


class TestT25MeAsOf(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, me_fixture())
        self.ada = login(self.client, "ada@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def me(self, query="", token=None):
        status, payload, _ = self.client.request("GET", "/me" + query,
                                                 token=token or self.ada)
        self.assertEqual(status, 200, payload)
        return payload

    def test_no_params_is_byte_for_byte_stage2(self):
        """R200: the live fast path, unchanged — current corrected values, no
        temporal fields in the response."""
        append_correction()
        me = self.me()
        self.assertEqual(set(me), {"user_id", "display_name", "handle", "balance",
                                   "currency", "minor_units", "total", "held",
                                   "available"})
        self.assertEqual((me["balance"], me["total"], me["held"], me["available"]),
                         (10100, 10100, 3000, 7100))

    def test_as_of_before_earliest_payment_is_the_opening(self):
        """R203: the opening balance — what the wallet held before anything moved
        (base_balance); the hold had not started yet (R277)."""
        append_correction()
        me = self.me("?as_of=%s" % q(T8))
        self.assertEqual((me["balance"], me["total"], me["held"], me["available"]),
                         (10600, 10600, 0, 10600))
        self.assertEqual(me["as_of"], T8)

    def test_as_of_at_the_latest_payment_is_current(self):
        """R202/R201: as_of at or after the latest payment returns the current
        balance; a payment at exactly as_of counts as happened."""
        append_correction()
        me = self.me("?as_of=%s" % q(T11))
        self.assertEqual((me["balance"], me["total"]), (10100, 10100))
        # exactly the corrected payment's original effective time, fully known
        me = self.me("?as_of=%s" % q(T10))
        self.assertEqual(me["total"], 10400)  # 10600 +200 -400: the correction is
        # fully known (recorded 12:00 <= now) and effective 09:30 <= 10:00

    def test_as_of_and_known_at_drive_one_consistent_view(self):
        """R276: total/balance/available/held all describe the same (as_of,
        known_at) view — correction and hold combined, hand-worked."""
        append_correction()
        me = self.me("?as_of=%s&known_at=%s" % (q(T10), q(T11)))
        # total: base 10600, p_2 +200 (recorded 09:00), p_1 revision 1 -500 (the
        # correction is recorded at 12:00, not yet known); p_3 not yet effective.
        self.assertEqual((me["total"], me["balance"]), (10300, 10300))
        self.assertEqual(me["held"], 3000)  # hold open at 10:00, deadline 2099
        self.assertEqual(me["available"], 7300)

    def test_known_at_before_a_correction_selects_revision_one(self):
        """R247/R249: the correction recorded at 12:00 is invisible at known_at
        11:00, so the original amount applies by its effective time."""
        append_correction()
        me = self.me("?as_of=%s&known_at=%s" % (q("2026-01-01T23:00:00+00:00"), q(T11)))
        self.assertEqual(me["total"], 10000)  # revision 1 still selected: +200 -500 -300
        me = self.me("?as_of=%s&known_at=%s" % (q("2026-01-01T23:00:00+00:00"), q(T12)))
        self.assertEqual(me["total"], 10100)  # 10600 +200 -400 -300, revision 2

    def test_known_at_before_the_hold_creation_releases_everything(self):
        """R247 hold-side: a hold not yet known at known_at contributes nothing;
        R276 keeps the four fields consistent."""
        me = self.me("?known_at=%s" % q(T9))
        # only p_2 was recorded by 09:00, so the fully-known total is 10600 + 200
        self.assertEqual((me["total"], me["held"], me["available"], me["balance"]),
                         (10800, 0, 10800, 10800))
        me = self.me("?as_of=%s&known_at=%s" % (q(T10), q(T9)))
        self.assertEqual((me["total"], me["held"], me["available"], me["balance"]),
                         (10800, 0, 10800, 10800))

    def test_known_at_alone_uses_the_request_start_instant(self):
        """R281/R248: without as_of the view's as_of is the request-start instant —
        the total is the live corrected total; only the holds view changes."""
        append_correction()
        me = self.me("?known_at=%s" % q(T9))
        self.assertEqual(me["total"], 10800)  # only p_2 recorded by 09:00
        self.assertEqual(me["held"], 0)       # hold not yet known at 09:00
        self.assertEqual(me["available"], 10800)
        self.assertEqual(me["known_at"], T9)

    def test_future_instants_allowed(self):
        """R251: both query instants may be in the future — an open hold expires at
        its deadline (2099), so it still holds at as_of 2027 (R280)."""
        append_correction()
        me = self.me("?as_of=%s&known_at=%s" % (q(FUTURE), q(FUTURE)))
        self.assertEqual((me["total"], me["held"], me["available"], me["balance"]),
                         (10100, 3000, 7100, 10100))
        self.assertEqual(me["as_of"], FUTURE)
        self.assertEqual(me["known_at"], FUTURE)

    def test_instants_echoed_exactly_as_given(self):
        """R204/R253: verbatim, including a non-UTC offset."""
        as_of = "2026-01-01T11:00:00+02:00"
        known = "2026-01-01T12:00:00-05:00"
        me = self.me("?as_of=%s&known_at=%s"
                     % (as_of.replace("+", "%2B"), known.replace("+", "%2B")))
        self.assertEqual(me["as_of"], as_of)
        self.assertEqual(me["known_at"], known)

    def test_invalid_instants_are_422(self):
        """R199/R252: naive local time, bare date, empty value, garbage — for both
        parameters, alone and combined with a valid one."""
        bad = ["?as_of=2026-01-01%2010%3A00%3A00", "?as_of=2026-01-01",
               "?as_of=", "?as_of=x",
               "?known_at=2026-01-01%2010%3A00%3A00", "?known_at=2026-01-01",
               "?known_at=", "?known_at=x",
               "?as_of=%s&known_at=2026-01-01" % q(T10)]
        for query in bad:
            status, payload, _ = self.client.request("GET", "/me" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, (query, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_bob_side_of_the_same_view(self):
        """R276 from the receiver's side: base 1900, -200 (p_2), +400 (p_1 rev2),
        +300 (p_3) -> 2400, and the hold is ada's so bob holds nothing."""
        append_correction()
        bob = login(self.client, "bob@example.com")
        me = self.me("?as_of=%s&known_at=%s" % (q(T11), q(T12)), token=bob)
        self.assertEqual((me["total"], me["balance"]), (2400, 2400))
        self.assertEqual(me["held"], 0)  # the hold is ada's


if __name__ == "__main__":
    unittest.main()