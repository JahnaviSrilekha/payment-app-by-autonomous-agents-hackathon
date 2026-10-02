"""T4: the shared idempotency pipeline (R56-R63), HTTP-level with stub idempotent
routes plus direct unit tests of body-value equality."""

import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import idempotency  # noqa: E402


class TestBodyEquality(unittest.TestCase):
    def test_key_order_and_whitespace_irrelevant(self):
        self.assertTrue(idempotency.bodies_equal({"a": 1, "b": 2}, {"b": 2, "a": 1}))
        self.assertTrue(idempotency.bodies_equal({"note": "x"}, {"note": "x"}))

    def test_numeric_forms_equal(self):
        # JSON 1000, 1000.0 and 1e3 are the same minor-unit amount (spec section 4)
        self.assertTrue(idempotency.bodies_equal({"amount": 1000}, {"amount": 1000.0}))
        self.assertTrue(idempotency.bodies_equal({"amount": 1000}, {"amount": 1e3}))

    def test_bool_never_equals_number(self):
        self.assertFalse(idempotency.bodies_equal({"amount": True}, {"amount": 1}))
        self.assertFalse(idempotency.bodies_equal({"amount": 1}, {"amount": True}))

    def test_structural_differences(self):
        self.assertFalse(idempotency.bodies_equal({"a": [1, 2]}, {"a": [2, 1]}))
        self.assertFalse(idempotency.bodies_equal({"a": 1}, {"a": 1, "b": 2}))
        self.assertFalse(idempotency.bodies_equal({"a": "1"}, {"a": 1}))
        self.assertFalse(idempotency.bodies_equal({}, {"visibility": "public"}))


class TestT4PipelineHttp(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)
        util.reset(self.client)
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": "ada@example.com", "password": "correct horse"})
        self.ada = payload["token"]
        _, payload, _ = self.client.request("POST", "/auth/login",
                                            {"email": "bob@example.com", "password": "correct horse"})
        self.bob = payload["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_first_use_201_then_replay_200_identical_body(self):
        status, first, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                               token=self.ada, key="k1")
        self.assertEqual(status, 201)
        status, replay, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                token=self.ada, key="k1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)

    def test_replay_body_key_order_irrelevant(self):
        status, first, _ = self.client.request("POST", "/_test/idem/echo", {"a": 1, "b": 2},
                                               token=self.ada, key="kord")
        self.assertEqual(status, 201)
        status, replay, _ = self.client.request("POST", "/_test/idem/echo", {"b": 2, "a": 1},
                                                token=self.ada, key="kord")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)

    def test_same_key_different_body_409(self):
        self.client.request("POST", "/_test/idem/echo", {"x": 1}, token=self.ada, key="k2")
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 2},
                                                 token=self.ada, key="k2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_same_key_different_path_is_new_request(self):
        status, _, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                           token=self.ada, key="k3")
        self.assertEqual(status, 201)
        status, payload, _ = self.client.request("POST", "/_test/idem/counter", {"x": 1},
                                                 token=self.ada, key="k3")
        self.assertEqual(status, 201)  # different path: not a replay, succeeds normally

    def test_failed_attempt_frees_key(self):
        status, payload, _ = self.client.request("POST", "/_test/idem/flaky", {"fail": True},
                                                 token=self.ada, key="k4")
        self.assertEqual(status, 404)
        status, payload, _ = self.client.request("POST", "/_test/idem/flaky", {"fail": False},
                                                 token=self.ada, key="k4")
        self.assertEqual(status, 201)  # treated as a first use

    def test_invalid_body_with_claimed_key_is_reuse(self):
        # claimed key resolved before endpoint field validation / current-resource checks
        self.client.request("POST", "/_test/idem/echo", {"x": 1}, token=self.ada, key="k5")
        status, payload, _ = self.client.request("POST", "/_test/idem/echo",
                                                 {"x": 1, "junk": "new value"},
                                                 token=self.ada, key="k5")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_key_reuse")

    def test_key_scoped_per_user(self):
        status, ada_resp, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                  token=self.ada, key="shared")
        self.assertEqual(status, 201)
        status, bob_resp, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                  token=self.bob, key="shared")
        self.assertEqual(status, 201)  # different user, same key string: independent

    def test_replay_returns_original_even_after_state_changes(self):
        status, first, _ = self.client.request("POST", "/_test/idem/counter", {},
                                               token=self.ada, key="c1")
        self.assertEqual(status, 201)
        self.client.request("POST", "/_test/idem/counter", {}, token=self.ada, key="c2")
        status, replay, _ = self.client.request("POST", "/_test/idem/counter", {},
                                                token=self.ada, key="c1")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)  # original receipt, not a new counter value

    def test_missing_or_empty_or_long_key(self):
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                 token=self.ada)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                 token=self.ada, with_key_header_empty=True)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "missing_idempotency_key")
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                 token=self.ada, key="k" * 256)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1},
                                                 token=self.ada, key="k" * 255)
        self.assertEqual(status, 201)

    def test_auth_before_key_check(self):
        # unauthenticated + missing key -> 401 wins (design section 5 order)
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", {"x": 1})
        self.assertEqual(status, 401)

    def test_unparseable_body_400_before_auth(self):
        status, payload, _ = self.client.request("POST", "/_test/idem/echo", raw_body=b"{bad")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_concurrent_identical_requests_exactly_one_201(self):
        results = util.concurrent(16, lambda i: self.client.request(
            "POST", "/_test/idem/counter", {}, token=self.ada, key="race-1"))
        created = [r for r in results if r[0] == 201]
        replayed = [r for r in results if r[0] == 200]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(replayed), 15)
        bodies = {str(r[1]) for r in replayed}
        self.assertEqual(bodies, {str(created[0][1])})
        # the operation took effect only once
        status, export, _ = self.client.request("GET", "/_test/export")
        counter_before = created[0][1]["n"]
        status, second, _ = self.client.request("POST", "/_test/idem/counter", {},
                                                token=self.ada, key="race-after")
        self.assertEqual(second["n"], created[0][1]["n"] + 1)

    def test_concurrent_identical_payment_shaped_requests_two_users(self):
        # two users racing with the same key are independent (R57)
        results = util.concurrent(8, lambda i: self.client.request(
            "POST", "/_test/idem/echo", {"who": "ada"}, token=self.ada, key="duo"))
        self.assertEqual([r[0] for r in results].count(201), 1)
        results = util.concurrent(16, lambda i: self.client.request(
            "POST", "/_test/idem/echo", {"who": "bob"}, token=self.bob, key="duo"))
        self.assertEqual([r[0] for r in results].count(201), 1)


if __name__ == "__main__":
    unittest.main()