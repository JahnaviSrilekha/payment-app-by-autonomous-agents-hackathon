"""T19: the /requests screen — shared route negotiation, incoming/outgoing lists with
buttons gated on direction+pending (R125), actions via stage-1 endpoints (R126),
stale-pay-button error flow (R134), empty state."""

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


def request(method, path, headers=None, body=None, token=None):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = "Bearer " + token
    conn = http.client.HTTPConnection("127.0.0.1", _SERVER[0], timeout=15)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        ctype = resp.getheader("Content-Type")
        if ctype and ctype.startswith("application/json"):
            return resp.status, ctype, json.loads(raw) if raw else None
        return resp.status, ctype, raw.decode("utf-8")
    finally:
        conn.close()


def login(client, email):
    _, payload, _ = client.request("POST", "/auth/login",
                                   {"email": email, "password": "correct horse"})
    return payload["token"]


class T19Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = start()

    @classmethod
    def tearDownClass(cls):
        _SERVER[1].shutdown()
        _SERVER[1].server_close()


class TestT19RequestsScreen(T19Base):
    def setUp(self):
        util.reset(util.Client(self.port))
        client = util.Client(self.port)
        self.ada_token = login(client, "ada@example.com")
        self.ada_cookie = "pebble_token=" + quote(self.ada_token)

    def requests_page(self, fixture=None):
        if fixture is not None:
            util.reset(util.Client(self.port), fixture)
            self.ada_token = login(util.Client(self.port), "ada@example.com")
            self.ada_cookie = "pebble_token=" + quote(self.ada_token)
        status, ctype, body = request("GET", "/requests",
                                      {"Accept": "text/html",
                                       "Cookie": self.ada_cookie})
        self.assertEqual(status, 200)
        return body

    def test_html_for_accept_header_json_otherwise(self):
        status, ctype, payload = request("GET", "/requests",
                                         headers={"Accept": "application/json"},
                                         token=self.ada_token)
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertIn("requests", payload)  # JSON branch unchanged (stage-1 shape)
        status, ctype, body = request("GET", "/requests",
                                      {"Accept": "text/html",
                                       "Cookie": self.ada_cookie})
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertIn('data-testid="incoming-list"', body)
        # unauthenticated JSON request still 401s exactly as stage-1
        status, ctype, payload = request("GET", "/requests",
                                         headers={"Accept": "application/json"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthenticated")

    def test_lists_render_with_status_and_gated_buttons(self):
        fixture = util.spec_fixture()
        fixture["payments"] = []
        fixture["requests"] = [
            {"id": "rq_in", "requester_id": "u_bob", "payer_id": "u_ada",
             "amount": 1200, "note": "taxi", "status": "pending"},
            {"id": "rq_out", "requester_id": "u_ada", "payer_id": "u_bob",
             "amount": 300, "note": "share", "status": "pending"},
            {"id": "rq_paid", "requester_id": "u_bob", "payer_id": "u_ada",
             "amount": 100, "note": "old", "status": "paid"},
        ]
        body = self.requests_page(fixture)
        for testid in ("incoming-list", "outgoing-list", "request-item-rq_in",
                       "request-item-rq_out", "request-amount-rq_in",
                       "request-pay-rq_in", "request-decline-rq_in",
                       "request-cancel-rq_out"):
            self.assertIn('data-testid="%s"' % testid, body)
        # request-error is created on demand by app.js, never pre-rendered
        self.assertNotIn('data-testid="request-error"', body)
        self.assertIn('data-testid="request-item-rq_in" data-status="pending"', body)
        self.assertIn('data-testid="request-item-rq_paid" data-status="paid"', body)
        self.assertIn("12.00 EUR", body)
        self.assertIn("3.00 EUR", body)
        # no pay button on a non-pending request, no cancel on incoming
        self.assertNotIn("request-pay-rq_paid", body)
        self.assertNotIn("request-cancel-rq_in", body)

    def test_empty_state_when_both_lists_empty(self):
        fixture = util.spec_fixture()
        fixture["payments"] = []
        fixture["requests"] = []
        body = self.requests_page(fixture)
        self.assertIn('data-testid="empty-requests"', body)
        self.assertNotIn('data-testid="request-item-', body)

    def test_signed_out_state(self):
        status, ctype, body = request("GET", "/requests", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertNotIn('data-testid="incoming-list"', body)
        self.assertIn('href="/login"', body)

    def test_wiring_uses_stage1_action_endpoints(self):
        with open("src/static/app.js", encoding="utf-8") as f:
            js = f.read()
        self.assertIn('"/requests/" + rid + "/pay"', js)
        self.assertIn('"/requests/" + rid + "/" + action', js)
        self.assertIn('showMessage("request-error"', js)


if __name__ == "__main__":
    unittest.main()