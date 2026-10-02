"""T18: the / screen — wallet numbers (R108/R115/R185-R187), pay and request forms
(R115-R119), activity feed (R122/R124), wallet-refresh (R130), server-rendered initial
state, signed-out welcome."""

import http.client
import json
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, __file__.rsplit("/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/../src")

import util  # noqa: E402

_SERVER = [None, None]


def start():
    srv = util.start_server()[1]
    _SERVER[0], _SERVER[1] = srv.server_address[1], srv
    return _SERVER[0]


def request(method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", _SERVER[0], timeout=15)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read().decode("utf-8")
    finally:
        conn.close()


def ada_cookie(client):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": "ada@example.com",
                                    "password": "correct horse"})
    return "pebble_token=" + quote(payload["token"])


class T18Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = start()

    @classmethod
    def tearDownClass(cls):
        _SERVER[1].shutdown()
        _SERVER[1].server_close()


class TestT18HomeSignedOut(T18Base):
    def setUp(self):
        util.reset(util.Client(self.port))

    def test_welcome_state_when_not_signed_in(self):
        status, ctype, body = request("GET", "/", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertNotIn('data-testid="wallet-balance"', body)
        self.assertIn('href="/login"', body)

    def test_api_clients_get_404(self):
        status, ctype, _ = request("GET", "/", {"Accept": "application/json"})
        self.assertEqual(status, 404)


class TestT18HomeSignedIn(T18Base):
    def setUp(self):
        util.reset(util.Client(self.port))
        self.cookie = ada_cookie(util.Client(self.port))

    def home(self, fixture=None):
        if fixture:
            util.reset(util.Client(self.port), fixture)
            self.cookie = ada_cookie(util.Client(self.port))
        status, ctype, body = request("GET", "/",
                                      {"Accept": "text/html",
                                       "Cookie": self.cookie})
        self.assertEqual(status, 200)
        return body

    def test_wallet_numbers_present_with_data_amount(self):
        body = self.home()
        self.assertIn('data-testid="wallet-balance"', body)
        self.assertIn('data-amount="10000"', body)
        self.assertIn("100.00 EUR", body)  # formatted total (R120)
        self.assertIn('data-testid="wallet-available"', body)
        self.assertIn("100.00 EUR", body)
        self.assertNotIn('data-testid="wallet-held"', body)  # absent at held == 0 (R187)

    def test_available_is_headline_once_holds_exist(self):
        fixture = util.spec_fixture()
        fixture["payments"] = []
        fixture["requests"] = []
        fixture["authorizations"] = [
            {"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 2000, "status": "open",
             "expires_at": "2099-01-01T00:00:00+00:00"}]
        body = self.home(fixture)
        self.assertIn('data-testid="wallet-held"', body)
        self.assertIn('data-amount="2000"', body)
        self.assertIn("20.00 EUR", body)
        self.assertIn('data-testid="wallet-available"', body)
        self.assertIn('data-amount="8000"', body)
        self.assertIn("80.00 EUR", body)
        # available renders as the headline (the wallet-headline class)
        available_pos = body.index('data-testid="wallet-available"')
        self.assertIn('class="wallet-headline"', body[available_pos - 60:available_pos])

    def test_pay_and_request_forms(self):
        body = self.home()
        for testid in ("pay-handle", "pay-amount", "pay-note", "pay-visibility",
                       "pay-submit", "request-handle", "request-amount",
                       "request-note", "request-submit", "wallet-refresh"):
            self.assertIn('data-testid="%s"' % testid, body)
        # pay-visibility options are exactly public/private (R115)
        self.assertIn('<option value="public">', body)
        self.assertIn('<option value="private">', body)
        # pay-amount is a decimal string as a person types it (R115)
        self.assertIn('value="15.00"', body)
        # inputs have visible labels
        self.assertIn('<label class="label" for="pay-amount">', body)

    def test_no_error_elements_until_there_is_one(self):
        body = self.home()
        for testid in ("pay-error", "pay-uncertain", "request-error"):
            self.assertNotIn('data-testid="%s"' % testid, body)

    def test_feed_server_rendered_newest_first(self):
        fixture = util.spec_fixture()
        fixture["payments"] = [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 500, "note": "coffee", "visibility": "public"},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
             "amount": 250, "note": "", "visibility": "private"},
        ]
        fixture["requests"] = []
        body = self.home(fixture)
        self.assertIn('data-testid="activity-list"', body)
        # newest first in the DOM (p_2 seeded second)
        self.assertLess(body.index("activity-item-p_2"), body.index("activity-item-p_1"))
        self.assertIn('data-testid="activity-item-p_1" data-visibility="public"', body)
        self.assertIn('data-testid="activity-item-p_2" data-visibility="private"', body)
        self.assertIn('data-testid="activity-parties-p_1"', body)
        self.assertIn("ada", body)  # both handles appear in parties
        self.assertIn("bob", body)
        self.assertIn('data-testid="activity-amount-p_1"', body)
        self.assertIn("5.00 EUR", body)
        self.assertIn('data-testid="activity-note-p_1"', body)
        # note element present even when empty (R122)
        self.assertIn('data-testid="activity-note-p_2"></span>', body)
        self.assertNotIn('data-testid="empty-activity"', body)

    def test_empty_activity_state(self):
        fixture = util.spec_fixture()
        fixture["payments"] = []
        fixture["requests"] = []
        body = self.home(fixture)
        self.assertIn('data-testid="empty-activity"', body)
        self.assertNotIn('data-testid="activity-list"', body)

    def test_private_payment_hidden_from_non_party(self):
        fixture = util.spec_fixture()
        fixture["payments"] = [
            {"id": "p_x", "from_user_id": "u_ada", "to_user_id": "u_bob",
             "amount": 100, "note": "secret", "visibility": "private"}]
        fixture["requests"] = []
        body = self.home(fixture)
        self.assertIn("activity-item-p_x", body)  # ada is a party
        # a third user sees nothing
        client = util.Client(self.port)
        charlie = util.signup(client, "charlie@example.com", display_name="Charlie")
        _, _, body = request("GET", "/",
                             {"Accept": "text/html",
                              "Cookie": "pebble_token=" + quote(charlie)})
        self.assertNotIn("activity-item-p_x", body)
        self.assertIn('data-testid="empty-activity"', body)

    def test_wiring_lifecycle_uses_derived_keys(self):
        with open("src/static/app.js", encoding="utf-8") as f:
            js = f.read()
        self.assertIn('deriveKey("pay-form"', js)
        self.assertIn('apiFetch("POST", "/payments", body, key)', js)
        self.assertIn('showMessage("pay-uncertain"', js)
        self.assertIn('createRefreshGuard()', js)


if __name__ == "__main__":
    unittest.main()