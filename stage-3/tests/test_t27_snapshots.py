"""T27: selected-revision ordering under known_at and stable snapshot pagination
(R254-R269, R288-R290, A19). Corrections land as directly constructed revisions
(T28's endpoint does not exist yet — the same shortcut T25's done-test blesses);
the snapshot freeze is driven live: payments and corrections land BETWEEN calls."""

import sys
import threading
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import state as state_mod  # noqa: E402

T9 = "2026-01-01T09:00:00+00:00"
T930 = "2026-01-01T09:30:00+00:00"
T10 = "2026-01-01T10:00:00+00:00"
T11 = "2026-01-01T11:00:00+00:00"
T12 = "2026-01-01T12:00:00+00:00"

# p_2 @09:00 bob->ada 200; p_1 @10:00 ada->bob 500; p_3 @11:00 ada->bob 300
# ada: base 10600, live 10000. bob: base 1900, live 2500.


def statement_fixture():
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
    }


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def append_revision(payment_id, new_amount, effective_at, recorded_at,
                    reason="oops"):
    """Land a correction without T28's endpoint: append the revision and move the
    live balances exactly as the correction handler will (R234/R235: the difference
    from the previous amount moves between the same two wallets — a decrease credits
    the original sender back)."""
    with state_mod.STATE_LOCK:
        service = state_mod.get()
        payment = next(p for p in service["payments"] if p["id"] == payment_id)
        delta = new_amount - payment["revisions"][-1]["amount"]
        payment["revisions"].append({
            "revision": len(payment["revisions"]) + 1, "amount": new_amount,
            "effective_at": effective_at, "recorded_at": recorded_at,
            "reason": reason,
        })
        service["users"][payment["from_user_id"]]["balance"] -= delta
        service["users"][payment["to_user_id"]]["balance"] += delta
        return payment


def q(instant):
    """URL-encode an instant for a query string (the + offset must be %2B)."""
    return instant.replace("+", "%2B")


class TestT27Selection(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, statement_fixture())
        self.ada = login(self.client, "ada@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, token, query=""):
        status, payload, _ = self.client.request("GET", "/statement" + query,
                                                 token=token)
        self.assertEqual(status, 200, payload)
        return payload

    def test_uncorrected_matches_t26(self):
        """R259/R214: no corrections and no known_at — the entries are T26's, with
        revision 1 selected (effective_at == recorded_at == created_at)."""
        s = self.get(self.ada)
        self.assertEqual((s["opening_balance"], s["closing_balance"]),
                         (10600, 10000))
        self.assertEqual([(e["payment"]["payment_id"], e["delta"])
                          for e in s["entries"]],
                         [("p_2", 200), ("p_1", -500), ("p_3", -300)])
        for e in s["entries"]:
            self.assertEqual(e["revision"], 1)
            self.assertEqual(e["effective_at"], e["payment"]["created_at"])
            self.assertEqual(e["recorded_at"], e["payment"]["created_at"])

    def test_known_at_selects_latest_recorded_revision(self):
        """R247/R248/R249: the latest revision recorded at or before known_at is the
        selected one; a revision recorded later is invisible."""
        append_revision("p_1", 400, T930, T12)  # recorded at 12:00
        before = self.get(self.ada, "?known_at=%s" % q("2026-01-01T11:30:00+00:00"))
        self.assertEqual([(e["payment"]["payment_id"], e["delta"])
                          for e in before["entries"]],
                         [("p_2", 200), ("p_1", -500), ("p_3", -300)])
        after = self.get(self.ada, "?known_at=%s" % q(T12))
        self.assertEqual([(e["payment"]["payment_id"], e["delta"])
                          for e in after["entries"]],
                         [("p_2", 200), ("p_1", -400), ("p_3", -300)])
        self.assertEqual(after["closing_balance"], 10100)  # live 10000 + 100

    def test_ordering_by_selected_effective_at(self):
        """R254/A17: entries order by the SELECTED effective_at — a corrected
        payment whose new effective time is earlier moves to its new slot."""
        append_revision("p_1", 400, T930, T12)
        s = self.get(self.ada)
        self.assertEqual([(e["payment"]["payment_id"], e["delta"])
                          for e in s["entries"]],
                         [("p_2", 200), ("p_1", -400), ("p_3", -300)])
        self.assertEqual(s["entries"][1]["effective_at"], T930)
        self.assertEqual(s["entries"][1]["revision"], 2)
        self.assertEqual((s["opening_balance"], s["closing_balance"]),
                         (10600, 10100))

    def test_zero_amount_revision_is_a_zero_delta_entry(self):
        """R257: a zero-amount revision still appears, with delta 0."""
        append_revision("p_3", 0, T11, T12)
        s = self.get(self.ada)
        entry = [e for e in s["entries"]
                 if e["payment"]["payment_id"] == "p_3"][0]
        self.assertEqual(entry["delta"], 0)
        self.assertEqual(entry["payment"]["amount"], 0)
        self.assertEqual(entry["balance_after"], 10300)  # 10600 +200 -500 +0
        self.assertEqual(s["closing_balance"], 10300)

    def test_no_correction_counted_alongside_its_revision(self):
        """R258: exactly one revision per payment contributes."""
        append_revision("p_1", 400, T930, T12)
        s = self.get(self.ada)
        p1_entries = [e for e in s["entries"]
                      if e["payment"]["payment_id"] == "p_1"]
        self.assertEqual(len(p1_entries), 1)
        self.assertEqual(p1_entries[0]["delta"], -400)

    def test_payment_recorded_later_contributes_nothing(self):
        """R247: a payment none of whose revisions was recorded by known_at
        contributes nothing at all."""
        s = self.get(self.ada, "?known_at=%s" % q(T930))
        self.assertEqual([e["payment"]["payment_id"] for e in s["entries"]], ["p_2"])
        self.assertEqual((s["opening_balance"], s["closing_balance"]),
                         (10600, 10800))

    def test_known_at_validation(self):
        """R252: invalid or empty known_at is 422."""
        for query in ("?known_at=2026-01-01", "?known_at=", "?known_at=x",
                      "?known_at=2026-01-01%2010%3A00%3A00"):
            status, payload, _ = self.client.request("GET", "/statement" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, (query, payload))


class TestT27Snapshots(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, statement_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, token, query=""):
        status, payload, _ = self.client.request("GET", "/statement" + query,
                                                 token=token)
        self.assertEqual(status, 200, payload)
        return payload

    def test_first_call_returns_opaque_fresh_token(self):
        """R260/A19: every non-snapshot response carries a fresh opaque token."""
        first = self.get(self.ada)
        second = self.get(self.ada)
        self.assertTrue(first["snapshot"] and second["snapshot"])
        self.assertNotEqual(first["snapshot"], second["snapshot"])

    def test_paging_freezes_entries_and_balances(self):
        """R262/R266: the snapshot pages the exact frozen result with correct
        has_more on the final partial page and beyond the end."""
        first = self.get(self.ada)
        token = first["snapshot"]
        pages = [self.get(self.ada, "?snapshot=%s&limit=1&offset=%d" % (token, i))
                 for i in range(4)]
        self.assertEqual([p["has_more"] for p in pages], [True, True, False, False])
        self.assertEqual(pages[3]["entries"], [])
        self.assertEqual([p["entries"][0] for p in pages[:3]], first["entries"])
        for page in pages:
            self.assertEqual((page["opening_balance"], page["closing_balance"]),
                             (first["opening_balance"], first["closing_balance"]))

    def test_snapshot_survives_payments_and_corrections_landing(self):
        """R262/R268/R290: new payments and a correction land in between — the paged
        snapshot is unchanged, a fresh statement reflects the changes."""
        first = self.get(self.ada)
        token = first["snapshot"]
        self.client.request("POST", "/payments", {"to_handle": "bob", "amount": 700},
                            token=self.ada, key="after-snap")
        append_revision("p_3", 50, T11, T12)
        for offset in range(3):
            page = self.get(self.ada,
                            "?snapshot=%s&limit=2&offset=%d" % (token, offset))
            self.assertEqual(page["entries"], first["entries"][offset:offset + 2])
            self.assertEqual((page["opening_balance"], page["closing_balance"]),
                             (first["opening_balance"], first["closing_balance"]))
        fresh = self.get(self.ada)
        self.assertEqual(fresh["closing_balance"], 9550)  # 10000 -700 +250
        self.assertEqual(len(fresh["entries"]), 4)

    def test_snapshot_combination_rules(self):
        """R263 (A15): from/to/known_at with a snapshot is 422 — checked before the
        token resolves, so a bogus token still gets 422, not 404."""
        token = self.get(self.ada)["snapshot"]
        for query in ("?snapshot=%s&from=%s" % (token, q(T10)),
                      "?snapshot=%s&to=%s" % (token, q(T11)),
                      "?snapshot=%s&known_at=%s" % (token, q(T12)),
                      "?snapshot=snap_bogus&from=%s" % q(T10)):
            status, payload, _ = self.client.request("GET", "/statement" + query,
                                                     token=self.ada)
            self.assertEqual(status, 422, (query, payload))

    def test_unknown_foreign_and_pre_reset_tokens(self):
        """R264/R265: unknown, another user's and pre-reset tokens are all 404
        not_found; tokens last until reset clears them wholesale."""
        token = self.get(self.ada)["snapshot"]
        status, payload, _ = self.client.request(
            "GET", "/statement?snapshot=snap_nope", token=self.ada)
        self.assertEqual(status, 404)
        status, payload, _ = self.client.request(
            "GET", "/statement?snapshot=%s" % token, token=self.bob)
        self.assertEqual(status, 404)
        util.reset(self.client, statement_fixture())
        status, payload, _ = self.client.request(
            "GET", "/statement?snapshot=%s" % token, token=login(self.client, "ada@example.com"))
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_holds_are_not_statement_entries_captures_appear_once(self):
        """R288/R289: authorization/release/expiry never appear; a capture appears
        exactly once, linked by its authorization_id."""
        _, authz, _ = self.client.request(
            "POST", "/authorizations", {"to_handle": "bob", "amount": 400},
            token=self.ada, key="auth-1")
        _, capture, _ = self.client.request(
            "POST", "/authorizations/%s/capture" % authz["authorization_id"], {},
            token=self.bob, key="cap-1")
        s = self.get(self.ada)
        linked = [e for e in s["entries"]
                  if e["payment"].get("authorization_id") == authz["authorization_id"]]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["delta"], -400)
        self.assertEqual(linked[0]["payment"]["payment_id"], capture["payment_id"])
        self.assertEqual(len(s["entries"]), 4)  # three seeded payments + one capture

    def test_export_works_after_a_snapshot_exists(self):
        """Design section 23 (R265): statement_snapshots are never exported. The
        regression the reviewer's rejection pinned: once any snapshot exists, GET
        /_test/export must still return 200 — the snapshots are silently omitted,
        and the export round-trips."""
        self.client.request("POST", "/payments", {"to_handle": "bob", "amount": 100},
                            token=self.ada, key="export-1")
        self.get(self.ada)  # creates a snapshot
        status, export, _ = self.client.request("GET", "/_test/export")
        self.assertEqual(status, 200)
        self.assertNotIn("statement_snapshots", export["state"])
        status, payload, _ = self.client.request("POST", "/_test/import", export)
        self.assertEqual(status, 204, payload)
        # after import the snapshots are gone but statements work normally again
        self.assertTrue(self.get(self.ada)["snapshot"])

    def test_snapshot_stable_under_concurrent_writes(self):
        """R268/R290 under load: readers paging a frozen snapshot while concurrent
        payments land all see the identical frozen page; a fresh statement sees the
        writes (R268's fresh-window clause)."""
        first = self.get(self.ada)
        token = first["snapshot"]
        start = threading.Barrier(50)

        def worker(i):
            start.wait()
            if i % 5 == 0:
                return self.client.request(
                    "POST", "/payments", {"to_handle": "bob", "amount": 1},
                    token=self.ada, key="storm-%d" % i)
            return self.client.request(
                "GET", "/statement?snapshot=%s&limit=3&offset=0" % token,
                token=self.ada)

        results = util.concurrent(50, worker)
        pages = [r for s, r, _ in results
                 if isinstance(r, dict) and "opening_balance" in r]
        self.assertEqual(len(pages), 40)
        for page in pages:
            self.assertEqual(page["entries"], first["entries"][:3])
            self.assertEqual((page["opening_balance"], page["closing_balance"]),
                             (first["opening_balance"], first["closing_balance"]))
        moved = sum(1 for s, r, _ in results if s == 201)
        fresh = self.get(self.ada)
        self.assertEqual(fresh["closing_balance"], 10000 - moved)
        self.assertEqual(len(fresh["entries"]), 3 + moved)


if __name__ == "__main__":
    unittest.main()