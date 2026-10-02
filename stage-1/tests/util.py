"""Shared test harness: start the real HTTP server in-process on an ephemeral port and
drive it with concurrent HTTP clients."""

import http.client
import json
import os
import sys
import threading

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import server  # noqa: E402
import errors  # noqa: E402


def spec_fixture():
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
             "amount": 500, "note": "coffee", "visibility": "public"},
        ],
        "requests": [
            {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
             "amount": 1200, "note": "taxi", "status": "pending"},
        ],
    }


def register_stub_routes():
    """Test-only routes exercising the shared pipelines (idempotent + plain authed)."""

    def idem_echo(ctx, user, service):
        return 201, {"echo": ctx.parsed}

    def idem_counter(ctx, user, service):
        service["next_seq"] += 1
        return 201, {"n": service["next_seq"]}

    def idem_flaky(ctx, user, service):
        if ctx.parsed.get("fail"):
            raise errors.not_found("forced failure")
        return 201, {"ok": True}

    def plain_ping(ctx, user, service):
        return 200, {"pong": user["id"]}

    server.ROUTES.append(server.Route("POST", r"/_test/idem/echo", idem_echo, idempotent=True))
    server.ROUTES.append(server.Route("POST", r"/_test/idem/counter", idem_counter, idempotent=True))
    server.ROUTES.append(server.Route("POST", r"/_test/idem/flaky", idem_flaky, idempotent=True))
    server.ROUTES.append(server.Route("POST", r"/_test/plain/ping", plain_ping))


def start_server():
    """Start a fresh server on an ephemeral port. Returns (port, server)."""
    register_stub_routes()
    srv = server.make_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv.server_address[1], srv


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, token=None, key=None, headers=None,
                raw_body=None, with_key_header_empty=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = dict(headers or {})
        payload = raw_body
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        if token:
            hdrs["Authorization"] = "Bearer " + token
        if key is not None:
            hdrs["Idempotency-Key"] = key
        if with_key_header_empty:
            hdrs["Idempotency-Key"] = ""
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            status = resp.status
            ctype = resp.getheader("Content-Type")
            data = resp.read()
        finally:
            conn.close()
        parsed = json.loads(data.decode("utf-8")) if data else None
        return status, parsed, ctype


def signup(client, email, password="correct horse", display_name="Ada"):
    status, payload, _ = client.request("POST", "/auth/signup",
                                        {"email": email, "password": password,
                                         "display_name": display_name})
    assert status == 201, (status, payload)
    return payload["token"]


def reset(client, fixture=None):
    status, payload, _ = client.request("POST", "/_test/reset", fixture or spec_fixture())
    assert status == 204, (status, payload)


def reset_clean(client):
    """Same seeded users as the spec fixture, but no seeded payments/requests."""
    fixture = spec_fixture()
    fixture["payments"] = []
    fixture["requests"] = []
    reset(client, fixture)


def concurrent(count, target):
    """Run target(i) in `count` threads; return list of results in index order."""
    results = [None] * count
    start = threading.Barrier(count)

    def run(i):
        start.wait()
        results[i] = target(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results