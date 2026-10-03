"""T1: HTTP scaffold, routing, error envelope, ids, timestamps (R5-R16, R38-R45)."""

import re
import sys
import time
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import errors  # noqa: E402
import ids  # noqa: E402
import state as state_mod  # noqa: E402

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")


class TestT1Server(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_health_200_schema(self):
        status, payload, ctype = self.client.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})
        self.assertEqual(ctype, "application/json; charset=utf-8")

    def test_request_queue_size(self):
        self.assertGreaterEqual(self.srv.request_queue_size, 256)

    def test_unknown_route_404_envelope(self):
        status, payload, _ = self.client.request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        self.assertIsInstance(payload["error"]["message"], str)

    def test_unknown_method_405_envelope(self):
        for method, path in (("POST", "/health"), ("DELETE", "/me"), ("GET", "/_test/reset"),
                             ("PUT", "/_test/export")):
            status, payload, _ = self.client.request(method, path)
            self.assertEqual(status, 405, (method, path))
            self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def test_unknown_post_route_404(self):
        status, payload, _ = self.client.request("POST", "/unknown", {"a": 1})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_unparseable_body_400(self):
        status, payload, _ = self.client.request("POST", "/auth/signup", raw_body=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_non_object_body_400(self):
        status, payload, _ = self.client.request("POST", "/auth/signup", raw_body=b"[1,2]")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_wrong_json_type_400(self):
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": 123, "password": "correct horse",
                                                  "display_name": "A"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_unknown_body_fields_ignored(self):
        token = util.signup(self.client, "extra@example.com")
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "other@example.com",
                                                  "password": "correct horse",
                                                  "display_name": "O", "unknown_field": 7,
                                                  "another": {"nested": True}})
        self.assertEqual(status, 201)
        self.assertTrue(token)

    def test_unknown_query_params_ignored(self):
        status, _, _ = self.client.request("GET", "/health?foo=bar&baz=1")
        self.assertEqual(status, 200)

    def test_protected_endpoint_requires_token(self):
        for headers in ({}, {"Authorization": "bearer-no-space"}, {"Authorization": "Basic abc"},
                        {"Authorization": "Bearer "}):
            status, payload, _ = self.client.request("GET", "/me", headers=headers)
            self.assertEqual(status, 401, headers)
            self.assertEqual(payload["error"]["code"], "unauthenticated")

    def test_unknown_token_401(self):
        status, payload, _ = self.client.request("GET", "/me", token="not-a-token")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthenticated")

    def test_error_envelope_shape(self):
        status, payload, _ = self.client.request("GET", "/me", token="junk")
        self.assertEqual(set(payload.keys()), {"error"})
        self.assertEqual(set(payload["error"].keys()), {"code", "message"})


class TestT1ErrorMapping(unittest.TestCase):
    def test_status_code_mapping(self):
        cases = [
            (errors.malformed_request(), 400, "malformed_request"),
            (errors.missing_idempotency_key(), 400, "missing_idempotency_key"),
            (errors.unauthenticated(), 401, "unauthenticated"),
            (errors.forbidden(), 403, "forbidden"),
            (errors.not_found(), 404, "not_found"),
            (errors.idempotency_key_reuse(), 409, "idempotency_key_reuse"),
            (errors.validation_failed(), 422, "validation_failed"),
            (errors.self_payment(), 422, "self_payment"),
            (errors.self_request(), 422, "self_request"),
            (errors.insufficient_funds(), 409, "insufficient_funds"),
            (errors.request_not_pending(), 409, "request_not_pending"),
            (errors.email_taken(), 409, "email_taken"),
            (errors.handle_taken(), 409, "handle_taken"),
        ]
        for exc, status, code in cases:
            self.assertEqual(exc.status, status)
            self.assertEqual(exc.code, code)
            body = exc.body()
            self.assertEqual(body["error"]["code"], code)
            self.assertIn("message", body["error"])


class TestT1Ids(unittest.TestCase):
    def test_id_format_and_length(self):
        for prefix in ("u", "p", "rq", "sp", "st"):
            value = ids.new_id(prefix)
            self.assertTrue(re.fullmatch(prefix + r"_[0-9a-f]{16}", value), value)
            self.assertLessEqual(len(value), 64)

    def test_token_format(self):
        token = ids.new_token()
        self.assertLessEqual(len(token), 64)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", token))

    def test_ids_unique(self):
        seen = {ids.new_id("p") for _ in range(1000)}
        self.assertEqual(len(seen), 1000)


class TestT1Timestamps(unittest.TestCase):
    def test_rfc3339_with_offset(self):
        self.assertTrue(RFC3339.fullmatch(state_mod.now_rfc3339()))

    def test_query_int_rules(self):
        good = {"limit": ["1"], "offset": ["0"]}
        self.assertEqual(state_mod.parse_limit(good), 1)
        self.assertEqual(state_mod.parse_offset(good), 0)
        self.assertEqual(state_mod.parse_limit({}), 50)
        self.assertEqual(state_mod.parse_offset({}), 0)
        for bad in ("1e9", "4.0", "+4", "-1", "0", "201", "", "1_0"):
            with self.assertRaises(errors.ApiError) as ctx:
                state_mod.parse_limit({"limit": [bad]})
            self.assertEqual(ctx.exception.status, 422, bad)
            self.assertEqual(ctx.exception.code, "validation_failed", bad)
        for bad in ("1e9", "4.0", "+4", "-1", ""):
            with self.assertRaises(errors.ApiError) as ctx:
                state_mod.parse_offset({"offset": [bad]})
            self.assertEqual(ctx.exception.status, 422, bad)


if __name__ == "__main__":
    unittest.main()