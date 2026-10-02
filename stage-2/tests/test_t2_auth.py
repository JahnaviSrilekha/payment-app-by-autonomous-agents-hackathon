"""T2: signup, login, derived handles, GET /me, scrypt outside STATE_LOCK (R19, R21, R22,
R46-R55, R64)."""

import hashlib
import re
import sys
import threading
import time
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import util  # noqa: E402
import auth  # noqa: E402
import state as state_mod  # noqa: E402


class TestT2Auth(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_signup_example(self):
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "a@example.com",
                                                  "password": "correct horse",
                                                  "display_name": "Ada"})
        self.assertEqual(status, 201)
        self.assertEqual(set(payload.keys()), {"user_id", "display_name", "token"})
        self.assertEqual(payload["display_name"], "Ada")
        self.assertTrue(payload["user_id"])
        self.assertTrue(payload["token"])

    def test_login_example(self):
        util.signup(self.client, "a@example.com")
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "a@example.com",
                                                  "password": "correct horse"})
        self.assertEqual(status, 200)
        self.assertEqual(set(payload.keys()), {"user_id", "display_name", "token"})

    def test_me_schema(self):
        token = util.signup(self.client, "ada@example.com", display_name="Ada")
        status, payload, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(set(payload.keys()),
                         {"user_id", "display_name", "handle", "balance", "currency", "minor_units"})
        self.assertEqual(payload["display_name"], "Ada")
        self.assertEqual(payload["handle"], "ada")
        self.assertEqual(payload["balance"], 0)

    def test_signup_defaults_and_new_user_balance_zero(self):
        token = util.signup(self.client, "zed@example.com", display_name="Zed")
        status, payload, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(payload["balance"], 0)
        self.assertEqual(payload["handle"], "zed")

    def test_derived_handle_rules(self):
        token = util.signup(self.client, "Ada.O'Brien+tax@Example.COM", display_name="A")
        status, payload, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(payload["handle"], "ada_o_brien_tax")

    def test_derived_handle_truncated_to_20(self):
        token = util.signup(self.client, "averyveryverylongemailname@example.com", display_name="A")
        status, payload, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual(payload["handle"], "averyveryverylongema")
        self.assertLessEqual(len(payload["handle"]), 20)

    def test_email_taken_409(self):
        util.signup(self.client, "ada@example.com")
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "ada@example.com",
                                                  "password": "correct horse",
                                                  "display_name": "Ada"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "email_taken")

    def test_handle_taken_409_no_account(self):
        util.signup(self.client, "ada@example.com")
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "ada@otherdomain.com",
                                                  "password": "correct horse",
                                                  "display_name": "X"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "handle_taken")
        # no account created: that email cannot log in
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "ada@otherdomain.com",
                                                  "password": "correct horse"})
        self.assertEqual(status, 401)

    def test_short_password_422(self):
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "a@example.com", "password": "7chars!",
                                                  "display_name": "A"})
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_seven_char_password_is_exactly_the_boundary(self):
        status, _, _ = self.client.request("POST", "/auth/signup",
                                           {"email": "a@example.com", "password": "12345678",
                                            "display_name": "A"})
        self.assertEqual(status, 201)

    def test_bad_email_shape_422(self):
        for email in ("not-an-email", "@domain.com", "local@", "a@@b.com", "a@b@c.com", ""):
            status, payload, _ = self.client.request("POST", "/auth/signup",
                                                     {"email": email, "password": "correct horse",
                                                      "display_name": "A"})
            self.assertEqual(status, 422, email)
            self.assertEqual(payload["error"]["code"], "validation_failed", email)

    def test_login_wrong_password_or_unknown_email_401(self):
        util.signup(self.client, "ada@example.com", password="correct horse")
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "ada@example.com", "password": "wrong horse"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthenticated")
        status, payload, _ = self.client.request("POST", "/auth/login",
                                                 {"email": "nobody@example.com",
                                                  "password": "correct horse"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthenticated")

    def test_missing_display_name_422(self):
        status, payload, _ = self.client.request("POST", "/auth/signup",
                                                 {"email": "a@example.com",
                                                  "password": "correct horse"})
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "validation_failed")

    def test_multiple_tokens_concurrent_sessions(self):
        util.signup(self.client, "ada@example.com")
        _, first, _ = self.client.request("POST", "/auth/login",
                                          {"email": "ada@example.com", "password": "correct horse"})
        _, second, _ = self.client.request("POST", "/auth/login",
                                           {"email": "ada@example.com", "password": "correct horse"})
        self.assertNotEqual(first["token"], second["token"])
        for token in (first["token"], second["token"]):
            status, _, _ = self.client.request("GET", "/me", token=token)
            self.assertEqual(status, 200)

    def test_concurrent_same_email_signup_one_winner(self):
        results = util.concurrent(8, lambda i: self.client.request(
            "POST", "/auth/signup",
            {"email": "race@example.com", "password": "correct horse", "display_name": "R"}))
        created = [r for r in results if r[0] == 201]
        taken = [r for r in results if r[0] == 409 and r[1]["error"]["code"] == "email_taken"]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(taken), 7)

    def test_scrypt_never_called_under_state_lock(self):
        violations = []
        original = hashlib.scrypt

        def spy(*args, **kwargs):
            if state_mod.STATE_LOCK.locked():
                violations.append("scrypt called while STATE_LOCK held")
            return original(*args, **kwargs)

        auth.hashlib.scrypt = spy
        try:
            util.signup(self.client, "ada@example.com")
            self.client.request("POST", "/auth/login",
                                {"email": "ada@example.com", "password": "correct horse"})
        finally:
            auth.hashlib.scrypt = original
        self.assertEqual(violations, [])

    def test_concurrent_signups_do_not_serialize_on_hashing(self):
        one_hash_start = time.monotonic()
        auth.hash_password("timing probe")
        one_hash = time.monotonic() - one_hash_start

        def signup_one(i):
            status, _, _ = self.client.request("POST", "/auth/signup",
                                               {"email": "user%d@example.com" % i,
                                                "password": "correct horse", "display_name": "U%d" % i})
            return status

        start = time.monotonic()
        results = util.concurrent(8, signup_one)
        wall = time.monotonic() - start
        self.assertEqual(results, [201] * 8)
        # if hashing serialized under the lock this would take ~8 * one_hash
        self.assertLess(wall, one_hash * 6, "signups appear to serialize on password hashing")


if __name__ == "__main__":
    unittest.main()