"""T24: the authorization historical-hold model (R276-R280, R282, R286, R287, A15
holds-view precedence). The (as_of, known_at) views are the one implementation;
the stage-2 two-argument wrapper is exactly the view at (now, now), proven against
hand-worked examples and by the unchanged stage-2 suite (test_t11..t15) still passing."""

import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import authorizations  # noqa: E402
import state as state_mod  # noqa: E402

CR_S = "2026-01-01T10:00:00+00:00"       # creation
CAP1_S = "2026-01-01T10:30:00+00:00"     # nonfinal capture of 500
CAP2_S = "2026-01-01T11:30:00+00:00"     # final capture of the remaining 500
VOID_S = "2026-01-01T12:00:00+00:00"     # void
EXP_S = "2026-01-01T13:00:00+00:00"      # expiry deadline
NOW_S = "2026-01-01T14:00:00+00:00"      # real now in these examples

CR = state_mod.parse_rfc3339(CR_S)
CAP1 = state_mod.parse_rfc3339(CAP1_S)
CAP2 = state_mod.parse_rfc3339(CAP2_S)
VOID = state_mod.parse_rfc3339(VOID_S)
EXP = state_mod.parse_rfc3339(EXP_S)
NOW = state_mod.parse_rfc3339(NOW_S)
PRE = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)   # before everything
PRE_S = "2026-01-01T09:00:00+00:00"


def auth(status="open", amount=1000, captured_amount=0, expires_at=EXP_S,
         created_at=CR_S, captures=None, void=None, closed_at=None):
    return {"id": "a_x", "from_user_id": "u_ada", "to_user_id": "u_bob",
            "amount": amount, "captured_amount": captured_amount, "currency": "EUR",
            "note": "", "visibility": "public", "status": status,
            "expires_at": expires_at, "created_at": created_at,
            "captures": captures or [], "void": void, "closed_at": closed_at,
            "payment_ids": [], "seq": 1}


def evented_captures():
    return [{"payment_id": "p_1", "amount": 500, "final": False, "event_time": CAP1_S},
            {"payment_id": "p_2", "amount": 500, "final": True, "event_time": CAP2_S}]


def service_with(*auths):
    service = state_mod.new_service("EUR", 2)
    service["users"]["u_ada"] = {"id": "u_ada", "handle": "ada", "balance": 10000}
    for a in auths:
        service["authorizations"][a["id"]] = a
    return service


class TestEffectiveStatusView(unittest.TestCase):
    def test_past_as_of_past_known_at_open_hold(self):
        a = auth()
        self.assertEqual(authorizations.effective_status_view(a, CAP1, NOW), "open")

    def test_known_at_before_creation_is_unknown_and_holds_nothing(self):
        """Done-test: a known_at before the event gives "unknown", contributing
        nothing to held_view (R247's hold-side analogue)."""
        a = auth()
        self.assertEqual(authorizations.effective_status_view(a, NOW, PRE), "unknown")
        self.assertEqual(authorizations.remaining_amount_view(a, NOW, PRE), 0)
        self.assertEqual(authorizations.held_view("u_ada", service_with(a), NOW, PRE), 0)

    def test_nonfinal_capture_reduces_only_after_its_event_time(self):
        """R277: a nonfinal capture reduces the hold at capture time."""
        a = auth(captures=evented_captures()[:1], captured_amount=500)
        self.assertEqual(authorizations.effective_status_view(a, CAP1, NOW), "open")
        self.assertEqual(authorizations.remaining_amount_view(a, CAP1, NOW), 500)
        self.assertEqual(authorizations.remaining_amount_view(
            a, datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc), NOW), 1000)

    def test_final_capture_closes_at_its_event_time(self):
        a = auth(captures=evented_captures(), captured_amount=1000)
        self.assertEqual(authorizations.effective_status_view(a, CAP2, NOW), "captured")
        self.assertEqual(authorizations.effective_status_view(
            a, datetime(2026, 1, 1, 11, 29, tzinfo=timezone.utc), NOW), "open")
        self.assertEqual(authorizations.remaining_amount_view(a, CAP2, NOW), 0)

    def test_void_closes_at_its_event_time(self):
        a = auth(void={"event_time": VOID_S}, closed_at=VOID_S)
        self.assertEqual(authorizations.effective_status_view(a, VOID, NOW), "voided")
        self.assertEqual(authorizations.effective_status_view(
            a, datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc), NOW), "open")

    def test_clock_expired_not_yet_acted_on_is_expired(self):
        """Stored open, deadline passed, nothing acted: the stage-2 view says expired
        at (now, now); at an earlier as_of it was open (no wall-clock leak)."""
        a = auth()  # stored "open", expires 13:00 < now 14:00
        self.assertEqual(authorizations.effective_status_view(a, NOW, NOW), "expired")
        self.assertEqual(authorizations.effective_status_view(a, CAP1, NOW), "open")

    def test_future_as_of_beyond_now_expires_open_hold_at_deadline(self):
        """R280: for queries beyond now, an open hold expires at its deadline — a hold
        still open at real now (deadline in the future) is expired in a view past it."""
        later = datetime(2027, 1, 1, tzinfo=timezone.utc)
        a = auth(expires_at="2026-06-01T00:00:00+00:00")  # open at real now
        self.assertEqual(authorizations.effective_status_view(a, later, NOW), "expired")
        self.assertEqual(authorizations.remaining_amount_view(a, later, NOW), 0)
        self.assertEqual(authorizations.effective_status_view(a, NOW, NOW), "open")

    def test_seeded_closed_hold_reads_back_its_given_status(self):
        """R287: a seeded closed hold is stored and read back with its given
        status/closed_at, with no synthetic capture/void record required."""
        seeded = auth(status="captured", amount=1000, captured_amount=1000,
                      closed_at=PRE_S)
        self.assertEqual(authorizations.effective_status_view(seeded, NOW, NOW),
                         "captured")
        seeded_voided = auth(status="voided", closed_at=PRE_S)
        self.assertEqual(authorizations.effective_status_view(seeded_voided, NOW, NOW),
                         "voided")
        # R287: no reconstructed lifecycle — a seeded closed hold reads back closed
        # at every known instant, exactly as stage-2 read the stored status.
        before_close = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(authorizations.effective_status_view(seeded, before_close, NOW),
                         "captured")


class TestClosedAtView(unittest.TestCase):
    def test_null_while_open_including_expired_but_unacted_at_earlier_as_of(self):
        """R282: null while open at as_of — including a hold that is clock-expired at
        real now, queried at an as_of before its deadline (nothing had acted on it)."""
        a = auth()  # clock-expired at NOW (expires 13:00)
        self.assertIsNone(authorizations.closed_at_view(a, CAP1, NOW))
        self.assertEqual(authorizations.closed_at_view(a, NOW, NOW), EXP_S)

    def test_capture_and_void_event_times_once_closed(self):
        a = auth(captures=evented_captures(), captured_amount=1000)
        self.assertEqual(authorizations.closed_at_view(a, CAP2, NOW), CAP2_S)
        self.assertIsNone(authorizations.closed_at_view(a, CAP1, NOW))
        v = auth(void={"event_time": VOID_S}, closed_at=VOID_S)
        self.assertEqual(authorizations.closed_at_view(v, VOID, NOW), VOID_S)

    def test_computed_expires_at_once_lazily_expired(self):
        """R278/R280: expiry's closing time is the computed expires_at — for the live
        clock-expired case and for a query beyond now alike."""
        later = datetime(2026, 1, 2, tzinfo=timezone.utc)
        a = auth()
        self.assertEqual(authorizations.closed_at_view(a, NOW, NOW), EXP_S)
        self.assertEqual(authorizations.closed_at_view(a, later, NOW), EXP_S)

    def test_seeded_closed_hold_carries_its_given_closed_at(self):
        seeded = auth(status="captured", captured_amount=1000, closed_at=PRE_S)
        self.assertEqual(authorizations.closed_at_view(seeded, NOW, NOW), PRE_S)
        before_close = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        self.assertIsNone(authorizations.closed_at_view(seeded, before_close, NOW))

    def test_unknown_hold_has_no_closed_at(self):
        a = auth()
        self.assertIsNone(authorizations.closed_at_view(a, NOW, PRE))


class TestWrapperEqualsStage2(unittest.TestCase):
    """The generalization is exact: every two-argument result is the view at
    (now, now), for open, clock-expired, captured, voided and seeded rows alike."""

    def cases(self):
        return [
            auth(),
            auth(expires_at=CR_S),                                   # clock-expired
            auth(expires_at="2099-01-01T00:00:00+00:00"),            # well open
            auth(captures=evented_captures(), captured_amount=1000),
            auth(captures=evented_captures()[:1], captured_amount=500),
            auth(void={"event_time": VOID_S}, closed_at=VOID_S),
            auth(status="captured", captured_amount=1000, closed_at=PRE_S),
            auth(status="voided", closed_at=PRE_S),
            auth(status="expired", closed_at=EXP_S),
            auth(status="captured", captured_amount=400),            # seeded, no closed_at
        ]

    def test_effective_status_wrapper_matches_view_now_now(self):
        for a in self.cases():
            self.assertEqual(authorizations.effective_status(a, NOW),
                             authorizations.effective_status_view(a, NOW, NOW),
                             a["status"])

    def test_held_wrapper_matches_view_now_now(self):
        for a in self.cases():
            service = service_with(a)
            self.assertEqual(state_mod.held("u_ada", service, NOW),
                             authorizations.held_view("u_ada", service, NOW, NOW))

    def test_remaining_amount_wrapper_matches_view_now_now(self):
        for a in self.cases():
            self.assertEqual(state_mod.remaining_amount(a, NOW),
                             authorizations.remaining_amount_view(a, NOW, NOW))

    def test_seeded_partial_capture_counts_at_now_now(self):
        """Stage-2's held() counts a seeded open hold's stored captured_amount; the
        view must reproduce it exactly at (now, now) (unrecorded captures, R287)."""
        a = auth(amount=10000, captured_amount=4000,
                 expires_at="2099-01-01T00:00:00+00:00")  # seeded open, no records
        self.assertEqual(authorizations.remaining_amount_view(a, NOW, NOW), 6000)
        self.assertEqual(state_mod.remaining_amount(a, NOW), 6000)


class TestSeededHolds(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def fixture(self):
        return {
            "currency": "EUR",
            "minor_units": 2,
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
                 "amount": 3000, "status": "open",
                 "expires_at": "2099-01-01T00:00:00+00:00"},
                {"id": "a_closed", "from_user_id": "u_bob", "to_user_id": "u_ada",
                 "amount": 1500, "status": "captured",
                 "expires_at": "2026-01-01T08:00:00+00:00",
                 "closed_at": "2026-01-01T07:30:00+00:00"},
            ],
        }

    def login(self, email):
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": email, "password": "correct horse"})
        return payload["token"]

    def test_seeded_open_hold_defaults_created_at_to_reset_time(self):
        """R286: created_at omitted -> reset time; the hold is held immediately."""
        util.reset(self.client, self.fixture())
        ada = self.login("ada@example.com")
        me = self.client.request("GET", "/me", token=ada)[1]
        self.assertEqual((me["total"], me["held"], me["available"]), (10000, 3000, 7000))
        _, created, _ = self.client.request("POST", "/payments",
                                            {"to_handle": "bob", "amount": 100},
                                            token=ada, key="p-1")
        state = self.client.request("GET", "/_test/export")[1]["state"]
        hold = state["authorizations"]["a_open"]
        self.assertIsNone(hold["closed_at"])
        self.assertLessEqual(state_mod.parse_rfc3339(hold["created_at"]),
                             state_mod.parse_rfc3339(created["created_at"]))

    def test_seeded_closed_hold_stored_and_read_back_unchanged(self):
        """R287: given status/closed_at round-trip through reset and GET
        /authorizations with no synthetic capture/void records."""
        util.reset(self.client, self.fixture())
        bob = self.login("bob@example.com")
        _, payload, _ = self.client.request("GET", "/authorizations", token=bob)
        by_id = {a["authorization_id"]: a for a in payload["authorizations"]}
        self.assertEqual(by_id["a_closed"]["status"], "captured")
        self.assertEqual(by_id["a_closed"]["closed_at"], "2026-01-01T07:30:00+00:00")
        self.assertEqual(by_id["a_closed"]["payment_ids"], [])
        self.assertEqual(by_id["a_closed"]["remaining_amount"], 0)
        self.assertIsNone(by_id["a_open"]["closed_at"])
        me = self.client.request("GET", "/me", token=bob)[1]
        self.assertEqual((me["total"], me["held"], me["available"]), (2500, 0, 2500))

    def test_future_created_at_on_seeded_hold_is_422(self):
        """A hold created in the future would drop out of the (now, now) held view —
        rejected at reset, nothing changes."""
        util.reset(self.client, self.fixture())
        before = self.client.request("GET", "/_test/export")[1]
        bad = self.fixture()
        bad["authorizations"][0]["created_at"] = "2999-01-01T00:00:00+00:00"
        status, payload, _ = self.client.request("POST", "/_test/reset", bad)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(self.client.request("GET", "/_test/export")[1], before)

    def test_live_capture_and_void_set_event_time_and_closed_at(self):
        """R279/R282 live: a capture's event time is the payment's created_at;
        closed_at is null while open, the closing event's time once closed."""
        util.reset(self.client, self.fixture())
        bob = self.login("bob@example.com")
        status, capture, _ = self.client.request(
            "POST", "/authorizations/a_open/capture", {"amount": 1000, "final": False},
            token=bob, key="cap-1")
        self.assertEqual(status, 201)
        _, listing, _ = self.client.request("GET", "/authorizations", token=bob)
        hold = [a for a in listing["authorizations"]
                if a["authorization_id"] == "a_open"][0]
        self.assertIsNone(hold["closed_at"])
        self.assertEqual(hold["status"], "open")
        self.assertEqual(hold["captured_amount"], 1000)
        status, capture2, _ = self.client.request(
            "POST", "/authorizations/a_open/capture", {"final": True},
            token=bob, key="cap-2")
        self.assertEqual(status, 201)
        _, listing, _ = self.client.request("GET", "/authorizations", token=bob)
        hold = [a for a in listing["authorizations"]
                if a["authorization_id"] == "a_open"][0]
        self.assertEqual(hold["status"], "captured")
        self.assertEqual(hold["closed_at"], capture2["created_at"])
        state = self.client.request("GET", "/_test/export")[1]["state"]
        events = state["authorizations"]["a_open"]["captures"]
        self.assertEqual([e["event_time"] for e in events],
                         [capture["created_at"], capture2["created_at"]])
        self.assertEqual([e["amount"] for e in events], [1000, 2000])
        # void path on a fresh hold
        status, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "ada", "amount": 500},
            token=bob, key="auth-2")
        status, voided, _ = self.client.request(
            "POST", "/authorizations/%s/void" % authz["authorization_id"], {},
            token=bob)
        self.assertEqual((status, voided["status"], voided["closed_at"]),
                         (200, "voided", voided["closed_at"]))
        self.assertIsNotNone(voided["closed_at"])
        state = self.client.request("GET", "/_test/export")[1]["state"]
        self.assertEqual(state["authorizations"][authz["authorization_id"]]["void"],
                         {"event_time": voided["closed_at"]})

    def test_held_view_pure_no_wall_clock(self):
        """Purity: held_view/effective_status_view read no wall clock."""
        import unittest.mock

        util.reset(self.client, self.fixture())
        with unittest.mock.patch.object(state_mod, "now_utc",
                                        side_effect=AssertionError("wall clock")):
            a = auth(captures=evented_captures(), captured_amount=1000)
            for _ in range(2):
                self.assertEqual(authorizations.effective_status_view(a, CAP2, NOW),
                                 "captured")
                self.assertEqual(authorizations.held_view("u_ada", service_with(a),
                                                         CAP1, NOW), 500)


if __name__ == "__main__":
    unittest.main()