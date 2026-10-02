"""T14: GET /authorizations — party exclusivity, direction/status filters incl.
clock-expired rows, pagination parity with GET /requests, newest first (R145 read
side, R180-R183)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402

FAR_FUTURE = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(timespec="seconds")
NEAR_PAST = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")


def mixed_fixture():
    fixture = util.spec_fixture()
    fixture["payments"] = []
    fixture["requests"] = []
    fixture["authorizations"] = [
        {"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 100, "status": "open", "expires_at": FAR_FUTURE},
        {"id": "a_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
         "amount": 200, "status": "captured", "captured_amount": 200,
         "expires_at": FAR_FUTURE},
        {"id": "a_3", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 300, "status": "voided", "expires_at": FAR_FUTURE},
        {"id": "a_4", "from_user_id": "u_bob", "to_user_id": "u_ada",
         "amount": 400, "status": "expired", "expires_at": FAR_FUTURE},
        {"id": "a_5", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 500, "status": "open", "expires_at": NEAR_PAST},  # clock-expired
    ]
    return fixture


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    assert payload and "token" in payload, payload
    return payload["token"]


class TestT14List(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client, mixed_fixture())
        self.ada = login(self.client, "ada@example.com")
        self.bob = login(self.client, "bob@example.com")
        _, charlie, _ = self.client.request("POST", "/auth/signup", {
            "email": "charlie@example.com", "password": "correct horse",
            "display_name": "Charlie"})
        self.charlie = charlie["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def list(self, token, query=""):
        return self.client.request("GET", "/authorizations%s" % query, token=token)

    def test_only_parties_are_listed_exclusion_not_refusal(self):
        status, payload, _ = self.list(self.charlie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["authorizations"], [])
        self.assertFalse(payload["has_more"])
        status, ada_list, _ = self.list(self.ada)
        self.assertEqual(status, 200)
        self.assertEqual({a["authorization_id"] for a in ada_list["authorizations"]},
                         {"a_1", "a_2", "a_3", "a_4", "a_5"})

    def test_direction_filters(self):
        _, outgoing, _ = self.list(self.ada, "?direction=outgoing")
        self.assertEqual({a["authorization_id"] for a in outgoing["authorizations"]},
                         {"a_1", "a_3", "a_5"})
        _, incoming, _ = self.list(self.ada, "?direction=incoming")
        self.assertEqual({a["authorization_id"] for a in incoming["authorizations"]},
                         {"a_2", "a_4"})
        _, bob_in, _ = self.list(self.bob, "?direction=incoming")
        self.assertEqual({a["authorization_id"] for a in bob_in["authorizations"]},
                         {"a_1", "a_3", "a_5"})
        status, payload, _ = self.list(self.ada, "?direction=sideways")
        self.assertEqual(status, 422)

    def test_status_filter_uses_effective_status(self):
        """R182: a clock-expired row (a_5, stored open) matches expired, never open;
        the seeded stored-expired row (a_4) matches too."""
        _, open_rows, _ = self.list(self.ada, "?status=open")
        self.assertEqual({a["authorization_id"] for a in open_rows["authorizations"]},
                         {"a_1"})
        _, expired_rows, _ = self.list(self.ada, "?status=expired")
        self.assertEqual({a["authorization_id"] for a in expired_rows["authorizations"]},
                         {"a_4", "a_5"})
        _, captured_rows, _ = self.list(self.ada, "?status=captured")
        self.assertEqual({a["authorization_id"] for a in captured_rows["authorizations"]},
                         {"a_2"})
        _, voided_rows, _ = self.list(self.ada, "?status=voided")
        self.assertEqual({a["authorization_id"] for a in voided_rows["authorizations"]},
                         {"a_3"})
        status, payload, _ = self.list(self.ada, "?status=frozen")
        self.assertEqual(status, 422)

    def test_combined_filters(self):
        _, rows, _ = self.list(self.ada, "?direction=outgoing&status=open")
        self.assertEqual({a["authorization_id"] for a in rows["authorizations"]},
                         {"a_1"})

    def test_newest_first_by_insertion(self):
        _, rows, _ = self.list(self.ada)
        ids_ = [a["authorization_id"] for a in rows["authorizations"]]
        self.assertEqual(ids_, ["a_5", "a_4", "a_3", "a_2", "a_1"])

    def test_newly_created_appear_first(self):
        _, ada, _ = self.list(self.ada)
        self.client.request("POST", "/authorizations", {"to_handle": "bob",
                                                        "amount": 50},
                            token=self.ada, key="new-1")
        _, rows, _ = self.list(self.ada)
        self.assertEqual(rows["authorizations"][0]["amount"], 50)
        self.assertEqual(len(rows["authorizations"]), len(ada["authorizations"]) + 1)

    def test_pagination_matches_get_requests(self):
        """limit/offset/has_more behave exactly as on GET /requests (R183, A10):
        identical slicing on identical inputs."""
        fixture = mixed_fixture()
        fixture["requests"] = [
            {"id": "rq_%d" % i, "requester_id": "u_bob", "payer_id": "u_ada",
             "amount": 1, "status": "pending"} for i in range(5)]
        util.reset(self.client, fixture)
        ada = login(self.client, "ada@example.com")
        for query in ["", "?limit=2", "?limit=2&offset=2", "?limit=2&offset=4",
                      "?offset=3", "?limit=0", "?limit=500"]:
            authz_status, authz_page, _ = self.list(ada, query)
            request_status, request_page, _ = self.client.request(
                "GET", "/requests%s" % query, token=ada)
            self.assertEqual(authz_status, request_status,
                             "status differs for %r" % query)
            if authz_status != 200:
                continue  # both endpoints refuse identically (limit=0 → 422)
            self.assertEqual(authz_page["has_more"], request_page["has_more"],
                             "has_more differs for %r" % query)
        _, page, _ = self.list(ada, "?limit=2&offset=2")
        self.assertEqual([a["authorization_id"] for a in page["authorizations"]],
                         ["a_3", "a_2"])
        self.assertTrue(page["has_more"])
        _, page, _ = self.list(ada, "?limit=2&offset=4")
        self.assertEqual([a["authorization_id"] for a in page["authorizations"]],
                         ["a_1"])
        self.assertFalse(page["has_more"])

    def test_listed_rows_carry_full_shape(self):
        _, rows, _ = self.list(self.bob, "?status=captured")
        row = rows["authorizations"][0]
        for field in ("authorization_id", "from_handle", "to_handle", "amount",
                      "captured_amount", "remaining_amount", "currency", "note",
                      "visibility", "status", "expires_at", "payment_id",
                      "payment_ids", "created_at"):
            self.assertIn(field, row)
        self.assertEqual(row["status"], "captured")  # effective, computed at read


if __name__ == "__main__":
    unittest.main()