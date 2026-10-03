"""T21: the /authorizations screen — negotiation (R105/R184), wallet numbers with
seeded holds immediately after reset (R191), the list's exact interface (R189/R190),
and the authorize form (R188)."""

import http.client
import json
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, __file__.rsplit("/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/../src")

import util  # noqa: E402

FAR = "2099-01-01T00:00:00+00:00"


def seeded_fixture():
    fx = util.spec_fixture()
    fx["payments"] = []
    fx["requests"] = []
    fx["authorizations"] = [
        {"id": "a_in", "from_user_id": "u_bob", "to_user_id": "u_ada",
         "amount": 1200, "note": "deposit", "status": "open", "expires_at": FAR},
        {"id": "a_out", "from_user_id": "u_ada", "to_user_id": "u_bob",
         "amount": 2000, "note": "", "status": "open", "expires_at": FAR},
        {"id": "a_cap", "from_user_id": "u_bob", "to_user_id": "u_ada",
         "amount": 500, "captured_amount": 300, "status": "captured",
         "expires_at": FAR},
    ]
    return fx


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


def raw_request(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read().decode("utf-8")
    finally:
        conn.close()


class T21Base(unittest.TestCase):
    def setUp(self):
        self.port, self.srv = util.start_server()
        self.client = util.Client(self.port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def html(self, token, headers=None):
        hdrs = {"Accept": "text/html", "Cookie": "pebble_token=" + quote(token)}
        hdrs.update(headers or {})
        return raw_request(self.port, "GET", "/authorizations", hdrs)


class TestT21Screen(T21Base):
    def test_negotiation_cookie_and_bearer(self):
        util.reset(self.client, seeded_fixture())
        token = login(self.client, "ada@example.com")
        status, ctype, body = self.html(token)
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        # bearer-token API callers asking for HTML still get the page
        status, ctype, body = raw_request(
            self.port, "GET", "/authorizations",
            {"Accept": "text/html", "Authorization": "Bearer " + token})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertIn('data-testid="authorization-item-a_in"', body)
        # JSON branch unchanged from batch 3 (bearer, no Accept header)
        status, payload, _ = self.client.request("GET", "/authorizations",
                                                 token=token)
        self.assertEqual(status, 200)
        self.assertEqual({a["authorization_id"] for a in payload["authorizations"]},
                         {"a_in", "a_out", "a_cap"})
        # unauthenticated JSON still 401s exactly like stage-1
        status, payload, _ = self.client.request("GET", "/authorizations")
        self.assertEqual(status, 401)

    def test_wallet_numbers_reflect_seeded_holds_immediately(self):
        """R191: with no action taken, available/held/the list all show the seeded
        holds. Ada holds 2000 out; nothing is held against her (only outgoing holds
        count)."""
        util.reset(self.client, seeded_fixture())
        token = login(self.client, "ada@example.com")
        _, me, _ = self.client.request("GET", "/me", token=token)
        self.assertEqual((me["total"], me["held"], me["available"]),
                         (10000, 2000, 8000))
        status, ctype, body = self.html(token)
        self.assertIn('data-testid="wallet-balance" data-amount="10000">100.00 EUR<', body)
        self.assertIn('data-testid="wallet-available" data-amount="8000">80.00 EUR<', body)
        self.assertIn('data-testid="wallet-held" data-amount="2000">20.00 EUR<', body)

    def test_list_interface_gating(self):
        util.reset(self.client, seeded_fixture())
        token = login(self.client, "ada@example.com")
        _, _, body = self.html(token)
        self.assertIn('data-testid="authorization-list"', body)
        self.assertIn('data-testid="authorization-item-a_out" data-status="open"', body)
        self.assertLess(body.index("authorization-item-a_cap"),
                        body.index("authorization-item-a_in"),
                        "a_cap seeded last: newest first in the DOM")
        self.assertIn('data-testid="authorization-amount-a_out" data-amount="2000">'
                      "20.00 EUR<", body)
        self.assertIn('data-testid="authorization-expires-a_out">%s<' % FAR, body)
        # incoming open: capture input pre-filled with the live remaining amount
        self.assertIn('data-testid="authorization-capture-amount-a_in" '
                      'inputmode="decimal" value="12.00"', body)
        self.assertIn('data-testid="authorization-capture-a_in"', body)
        # outgoing open: void
        self.assertIn('data-testid="authorization-void-a_out"', body)
        # no capture on an outgoing row, no void on an incoming row
        self.assertNotIn("authorization-capture-a_out", body)
        self.assertNotIn("authorization-void-a_in", body)
        # captured row: captured amount shown; no buttons
        self.assertIn('data-testid="authorization-captured-a_cap" data-amount="300">'
                      "3.00 EUR<", body)
        self.assertNotIn("authorization-capture-a_cap", body)
        self.assertNotIn("authorization-void-a_cap", body)

    def test_partial_capture_open_row_prefills_remainder(self):
        util.reset(self.client, seeded_fixture())
        ada = login(self.client, "ada@example.com")  # ada is the receiver of a_in
        status, _, _ = self.client.request(
            "POST", "/authorizations/a_in/capture", {"amount": 400, "final": False},
            token=ada, key="t21-cap")
        self.assertEqual(status, 201)
        _, _, body = self.html(login(self.client, "ada@example.com"))
        self.assertIn('data-testid="authorization-capture-amount-a_in" '
                      'inputmode="decimal" value="8.00"', body)

    def test_empty_state_and_authorize_form(self):
        util.reset(self.client)
        token = login(self.client, "ada@example.com")
        _, _, body = self.html(token)
        self.assertIn('data-testid="empty-authorizations"', body)
        self.assertNotIn('data-testid="authorization-list"', body)
        for testid in ("authorize-handle", "authorize-amount", "authorize-note",
                       "authorize-visibility", "authorize-submit",
                       "wallet-balance", "wallet-available"):
            self.assertIn('data-testid="%s"' % testid, body)
        self.assertNotIn('data-testid="authorize-error"', body)  # only on refusal
        self.assertNotIn('data-testid="authorization-error"', body)

    def test_signed_out_state(self):
        status, ctype, body = raw_request(
            self.port, "GET", "/authorizations", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertNotIn('data-testid="authorize-handle"', body)
        self.assertIn('href="/login"', body)

    def test_wiring_uses_derived_keys_and_refreshes(self):
        with open("src/static/app.js", encoding="utf-8") as f:
            js = f.read()
        self.assertIn('deriveKey("authorize-form"', js)
        self.assertIn('apiFetch("POST", "/authorizations", body, key)', js)
        self.assertIn('"/authorizations/" + aid + "/capture"', js)
        self.assertIn('"/authorizations/" + aid + "/void"', js)


if __name__ == "__main__":
    unittest.main()