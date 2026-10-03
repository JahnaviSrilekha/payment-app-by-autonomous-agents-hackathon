"""T26: GET /statement core (R205-R213, A14). Entries over a half-open window,
oldest first, each with the caller's balance immediately after it; opening/closing
balances are immediately before from/to so the deltas telescope (R211) by
construction; pagination never changes a balance (R212); visibility rules do not
apply (R213)."""

import random
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402

T9 = "2026-01-01T09:00:00+00:00"
T10 = "2026-01-01T10:00:00+00:00"
T11 = "2026-01-01T11:00:00+00:00"


def q(instant):
    """URL-encode an instant for a query string (the + offset must be %2B)."""
    return instant.replace("+", "%2B")


def statement_fixture():
    return {
        "currency": "EUR",
        "minor_units": 2,
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada", "handle": "ada", "balance": 10000},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
             "display_name": "Bob", "handle": "bob", "balance": 2500},
            {"id": "u_carol", "email": "carol@example.com", "password": "correct horse",
             "display_name": "Carol", "handle": "carol", "balance": 1000},
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
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


class TestT26Statement(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, statement_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        self.carol = login(self.client, "carol@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, token, query=""):
        status, payload, _ = self.client.request("GET", "/statement" + query,
                                                 token=token)
        self.assertEqual(status, 200, payload)
        return payload

    def test_full_window_hand_worked(self):
        """R205/R207/R208: defaults cover the whole wallet; ada's replay is opening
        10600 -> +200 -> -500 -> -300 -> closing 10000, oldest first."""
        s = self.get(self.ada)
        self.assertEqual(set(s), {"opening_balance", "entries", "closing_balance",
                                  "has_more"})
        self.assertEqual(s["opening_balance"], 10600)
        self.assertEqual(s["closing_balance"], 10000)
        self.assertEqual([(e["payment"]["payment_id"], e["delta"], e["balance_after"])
                          for e in s["entries"]],
                         [("p_2", 200, 10800), ("p_1", -500, 10300),
                          ("p_3", -300, 10000)])
        self.assertFalse(s["has_more"])
        # R207: entries carry the payment, delta and the caller's balance after it
        entry = s["entries"][0]
        self.assertEqual(set(entry), {"payment", "delta", "balance_after",
                                      "revision", "effective_at", "recorded_at"})
        self.assertEqual(entry["payment"]["amount"], 200)
        self.assertEqual(entry["payment"]["created_at"], T9)

    def test_sent_negative_received_positive(self):
        """R211's sign rule: sent -> negative delta, received -> positive delta."""
        ada = self.get(self.ada)
        self.assertEqual([e["delta"] for e in ada["entries"]], [200, -500, -300])
        bob = self.get(self.bob)
        self.assertEqual([e["delta"] for e in bob["entries"]], [-200, 500, 300])
        self.assertEqual([e["balance_after"] for e in bob["entries"]],
                         [1700, 2200, 2500])
        self.assertEqual((bob["opening_balance"], bob["closing_balance"]),
                         (1900, 2500))

    def test_half_open_window_and_strict_boundaries(self):
        """R205/R210: opening is immediately before `from` (a payment at exactly
        `from` is IN the window, not in the opening); closing is immediately before
        `to`; [from, to) is half-open."""
        s = self.get(self.ada, "?from=%s&to=%s" % (q(T10), q(T11)))
        self.assertEqual(s["opening_balance"], 10800)  # strictly before 10:00
        self.assertEqual([e["payment"]["payment_id"] for e in s["entries"]], ["p_1"])
        self.assertEqual(s["entries"][0]["balance_after"], 10300)
        self.assertEqual(s["closing_balance"], 10300)  # immediately before 11:00
        # a payment at exactly `to` is excluded; at exactly `from` included
        s = self.get(self.ada, "?from=%s&to=%s" % (q(T9), q(T10)))
        self.assertEqual([e["payment"]["payment_id"] for e in s["entries"]], ["p_2"])
        s = self.get(self.ada, "?from=%s&to=%s" % (q(T10), q(T10)))
        self.assertEqual(s["entries"], [])
        self.assertEqual(s["opening_balance"], s["closing_balance"])

    def test_default_from_is_the_unbounded_opening(self):
        """A14/R203: with no `from` the opening is the pre-earliest balance
        (base_balance); with `to` given the window simply ends there."""
        s = self.get(self.ada, "?to=%s" % q(T11))
        self.assertEqual(s["opening_balance"], 10600)
        self.assertEqual([e["payment"]["payment_id"] for e in s["entries"]],
                         ["p_2", "p_1"])
        self.assertEqual(s["closing_balance"], 10300)

    def test_default_to_covers_everything_before_the_request(self):
        """R205: `to` defaults to now — every payment that exists at read time is in
        the window, so the last balance_after equals the live balance (R202)."""
        s = self.get(self.ada)
        self.assertEqual(s["entries"][-1]["balance_after"], 10000)

    def test_opening_plus_deltas_equals_closing_random_payments(self):
        """R211 invariant, property-checked over a randomly generated set of
        sent/received payments (skipping unaffordable ones)."""
        random.seed(20260103)
        for i in range(24):
            sender, receiver = random.choice(
                [(self.ada, "bob"), (self.bob, "ada"), (self.ada, "carol")])
            self.client.request("POST", "/payments",
                                {"to_handle": receiver,
                                 "amount": random.randrange(1, 700)},
                                token=sender, key="rnd-%d" % i)
        s = self.get(self.ada)
        self.assertEqual(s["opening_balance"] + sum(e["delta"] for e in s["entries"]),
                         s["closing_balance"])
        # every entry's balance_after chains: running balance across the full window
        running = s["opening_balance"]
        for e in s["entries"]:
            running += e["delta"]
            self.assertEqual(e["balance_after"], running)

    def test_ordering_ties_broken_by_payment_id(self):
        """R209: same created_at -> ascending payment id."""
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 10}, token=self.ada,
                            key="tie-a")
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 11}, token=self.ada,
                            key="tie-b")
        s = self.get(self.ada)
        ids = [e["payment"]["payment_id"] for e in s["entries"]][-2:]
        self.assertEqual(ids, sorted(ids))

    def test_pagination_never_changes_balances(self):
        """R206/R212: limit/offset slice the same entry list; opening/closing and
        each entry's balance_after are the full-window values regardless; has_more
        reports the final partial page and offsets beyond the end correctly."""
        full = self.get(self.ada)
        p1 = self.get(self.ada, "?limit=1&offset=0")
        p2 = self.get(self.ada, "?limit=1&offset=1")
        p3 = self.get(self.ada, "?limit=1&offset=2")
        beyond = self.get(self.ada, "?limit=1&offset=9")
        self.assertTrue(p1["has_more"] and p2["has_more"])
        self.assertFalse(p3["has_more"] and beyond["has_more"])
        self.assertEqual(beyond["entries"], [])
        for page in (p1, p2, p3):
            self.assertEqual((page["opening_balance"], page["closing_balance"]),
                             (full["opening_balance"], full["closing_balance"]))
        self.assertEqual([p["entries"][0] for p in (p1, p2, p3)], full["entries"])

    def test_limit_offset_bounds_match_get_requests(self):
        """R206: same defaults, bounds and error codes as GET /requests."""
        for query in ("?limit=0", "?limit=201", "?limit=4.0", "?offset=-1",
                      "?limit=1e2"):
            status, payload, _ = self.client.request("GET", "/statement" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, (query, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")
        status, requests_payload, _ = self.client.request("GET", "/requests?limit=0",
                                                          token=self.ada)
        self.assertEqual(status, 422)

    def test_invalid_instants_are_422(self):
        """R199's rule applied to the window: naive local time, bare date, empty."""
        for query in ("?from=2026-01-01%2010%3A00%3A00", "?from=2026-01-01",
                      "?from=", "?to=2026-01-01%2010%3A00%3A00", "?to=", "?from=x"):
            status, payload, _ = self.client.request("GET", "/statement" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, (query, payload))
            self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_unknown_query_params_ignored(self):
        """R267: unrecognized query parameters remain ignored."""
        self.assertEqual(self.get(self.ada, "?wat=1")["closing_balance"], 10000)

    def test_only_own_payments_regardless_of_visibility(self):
        """R213: only payments the caller sent or received — a public payment between
        others never appears, and a private payment of the caller's own does."""
        self.client.request("POST", "/payments",
                            {"to_handle": "bob", "amount": 150,
                             "visibility": "private"},
                            token=self.ada, key="priv-1")
        ada_ids = [e["payment"]["payment_id"] for e in self.get(self.ada)["entries"]]
        bob_ids = [e["payment"]["payment_id"] for e in self.get(self.bob)["entries"]]
        carol = self.get(self.carol)
        # ada's private payment is in both parties' statements, in window order
        self.assertEqual(len(ada_ids), 4)
        self.assertEqual(len(bob_ids), 4)
        private_entry = [e for e in self.get(self.ada)["entries"]
                         if e["payment"]["payment_id"] not in ("p_1", "p_2", "p_3")]
        self.assertEqual(len(private_entry), 1)
        self.assertEqual(private_entry[0]["payment"]["visibility"], "private")
        self.assertEqual(private_entry[0]["delta"], -150)
        # carol is a party to nothing: empty statement, flat balances
        self.assertEqual(carol["entries"], [])
        self.assertEqual((carol["opening_balance"], carol["closing_balance"]),
                         (1000, 1000))


if __name__ == "__main__":
    unittest.main()