"""T17: signup and login screens — reachable by URL, testids present, error shown
without a page reload (R113/R114), session area on every screen when signed in."""

import http.client
import json
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, __file__.rsplit("/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/../src")

import util  # noqa: E402  (starts the server, registers test stubs)

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


class T17Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = start()

    @classmethod
    def tearDownClass(cls):
        _SERVER[1].shutdown()
        _SERVER[1].server_close()

    def setUp(self):
        util.reset(util.Client(self.port))


class TestT17Screens(T17Base):
    def test_signup_page_reachable_by_url(self):
        status, ctype, body = request("GET", "/signup", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        for testid in ("signup-email", "signup-password", "signup-display-name",
                       "signup-submit", "login-email", "login-password",
                       "login-submit"):
            if testid.startswith("login"):
                continue
            self.assertIn('data-testid="%s"' % testid, body)
        self.assertIn('href="/static/app.css"', body)

    def test_login_page_reachable_by_url(self):
        status, ctype, body = request("GET", "/login", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        for testid in ("login-email", "login-password", "login-submit"):
            self.assertIn('data-testid="%s"' % testid, body)

    def test_no_error_element_until_there_is_one(self):
        _, _, body = request("GET", "/login", {"Accept": "text/html"})
        self.assertNotIn('data-testid="auth-error"', body)

    def test_api_clients_get_json_404(self):
        status, ctype, _ = request("GET", "/signup", {"Accept": "application/json"})
        self.assertEqual(status, 404)
        status, ctype, _ = request("GET", "/login")
        self.assertEqual(status, 404)

    def test_forms_submit_to_stage1_endpoints(self):
        """R114: the UI submits to the same stage-1 endpoints; the form posts natively
        so a lost session can never depend on a fetch surviving a navigation."""
        _, _, body = request("GET", "/login", {"Accept": "text/html"})
        self.assertIn('action="/auth/login"', body)
        _, _, body = request("GET", "/signup", {"Accept": "text/html"})
        self.assertIn('action="/auth/signup"', body)

    def test_app_boot_dispatches_by_screen(self):
        with open("src/static/app-boot.js", encoding="utf-8") as f:
            boot = f.read()
        self.assertIn("__PEBBLE_BOOT__", boot)
        # the logout button is wired on every signed-in screen (R113)
        self.assertIn("logout-button", boot)

    def test_session_area_when_signed_in(self):
        client = util.Client(self.port)
        token = util.signup(client, "session@example.com", display_name="Sess")
        status, ctype, body = request(
            "GET", "/login", {"Accept": "text/html",
                              "Cookie": "pebble_token=" + quote(token)})
        self.assertEqual(status, 200)
        self.assertIn('data-testid="current-user"', body)
        self.assertIn("Sess", body)  # display name in current-user
        self.assertIn('data-testid="current-handle"', body)
        self.assertIn(">session<", body)  # exactly the handle, no @, no extra words
        self.assertIn('data-testid="logout-button"', body)

    def test_session_area_signed_out_links(self):
        _, _, body = request("GET", "/login", {"Accept": "text/html"})
        self.assertNotIn('data-testid="current-user"', body)
        self.assertIn('href="/signup"', body)


if __name__ == "__main__":
    unittest.main()