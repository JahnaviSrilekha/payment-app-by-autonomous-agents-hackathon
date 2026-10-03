"""T20: the /split screen — testids (R127), JS-port splitShares pinned to the server's
Python split_shares over a sweep (R128, A11), formatted-amount twin (R120/R121)."""

import http.client
import json
import os
import shutil
import subprocess
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, __file__.rsplit("/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/../src")

import splits  # noqa: E402
import ui  # noqa: E402
import util  # noqa: E402

_SERVER = [None, None]


def start():
    srv = util.start_server()[1]
    _SERVER[0], _SERVER[1] = srv.server_address[1], srv
    return _SERVER[0]


def request(method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", _SERVER[0], timeout=15)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read().decode("utf-8")
    finally:
        conn.close()


class TestT20SplitScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = start()

    @classmethod
    def tearDownClass(cls):
        _SERVER[1].shutdown()
        _SERVER[1].server_close()

    def test_split_screen_reachable_and_complete(self):
        util.reset(util.Client(self.port))
        client = util.Client(self.port)
        token = util.signup(client, "split@example.com", display_name="Split")
        status, ctype, body = request("GET", "/split",
                                      {"Accept": "text/html",
                                       "Cookie": "pebble_token=" + quote(token)})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        for testid in ("split-amount", "split-handles", "split-note", "split-submit",
                       "split-preview"):
            self.assertIn('data-testid="%s"' % testid, body)
        self.assertNotIn('data-testid="split-error"', body)  # only when refused
        self.assertIn('value="30.00"', body)

    def test_signed_out_state(self):
        status, ctype, body = request("GET", "/split", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertNotIn('data-testid="split-amount"', body)
        self.assertIn('href="/login"', body)

    def test_wiring_posts_minor_units_and_navigates(self):
        with open("src/static/app.js", encoding="utf-8") as f:
            js = f.read()
        self.assertIn('apiFetch("POST", "/splits", body, key)', js)
        self.assertIn('window.location.href = "/requests"', js)


class TestT20SplitSharesCrossLanguage(unittest.TestCase):
    """A11: the preview is the server's rounding function, ported; the port is pinned
    to Python's splits.split_shares over a sweep so they can never silently diverge."""

    def test_js_split_shares_matches_python_over_sweep(self):
        vectors = []
        for amount in list(range(1, 401)) + [1000, 1001, 999, 12345, 1000000000]:
            for count in (1, 2, 3, 4, 5, 6, 7, 13):
                vectors.append([amount, count, splits.split_shares(amount, count)])
        payload = json.dumps(vectors)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "split_vectors.js")
        node = shutil.which("node")
        if node is None:
            self.fail("node is required for the cross-language sweep")
        result = subprocess.run([node, script, payload], capture_output=True,
                                text=True, timeout=60,
                                cwd=os.path.dirname(script))
        self.assertEqual(result.returncode, 0,
                         "JS splitShares diverged from the server:\n%s"
                         % (result.stdout + result.stderr)[-2000:])

    def test_js_format_amount_matches_python_twin(self):
        vectors = []
        for minor in (0, 1, 5, 99, 100, 1234, 10000, 999999999):
            for minor_units in (0, 2, 3):
                for currency in ("EUR", "JPY", "USD"):
                    vectors.append([minor, minor_units, currency,
                                    ui.format_amount(minor, minor_units, currency)])
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "split_vectors.js")
        node = shutil.which("node")
        if node is None:
            self.fail("node is required for the cross-language sweep")
        result = subprocess.run([node, script, json.dumps({"format": vectors})],
                                capture_output=True, text=True, timeout=60,
                                cwd=os.path.dirname(script))
        self.assertEqual(result.returncode, 0,
                         "JS formatAmount diverged from the server twin:\n%s"
                         % (result.stdout + result.stderr)[-2000:])


if __name__ == "__main__":
    unittest.main()