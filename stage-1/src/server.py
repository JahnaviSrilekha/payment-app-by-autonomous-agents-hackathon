"""Pocketful stage 1 HTTP service (design.md section 1, ADR-001).

Single Python process, stdlib only. ThreadingHTTPServer with request_queue_size=256.
One global STATE_LOCK serializes all state access; password hashing (signup, login and
fixture seeding) never runs while the lock is held.
"""

import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth
import errors
import idempotency
import payments
import requests as requests_endpoints
import settlements
import state as state_mod
import testctl

DEFAULT_PORT = 8080


def _reject_constants(name):
    raise ValueError("NaN and Infinity are not valid JSON values")


class Ctx:
    """Per-request context handed to endpoint functions."""

    def __init__(self, method, path, parsed, headers, query):
        self.method = method
        self.path = path
        self.parsed = parsed
        self.headers = headers
        self.query = query
        self.params = {}


class Route:
    def __init__(self, method, pattern, fn, public=False, idempotent=False):
        self.method = method
        self.pattern = re.compile(pattern)
        self.fn = fn
        self.public = public
        self.idempotent = idempotent

    def match(self, path):
        return self.pattern.fullmatch(path)


ROUTES = []


def route(method, pattern, public=False, idempotent=False):
    def register(fn):
        ROUTES.append(Route(method, pattern, fn, public=public, idempotent=idempotent))
        return fn
    return register


def _find_route(method, path):
    found = None
    for candidate in ROUTES:
        match = candidate.match(path)
        if match:
            if candidate.method == method:
                return candidate, match
            found = candidate
    if found is not None:
        raise errors.method_not_allowed()
    raise errors.not_found()


# --- public endpoints ---------------------------------------------------------


@route("GET", r"/health", public=True)
def ep_health(ctx):
    return 200, {"status": "ok"}


@route("POST", r"/auth/signup", public=True)
def ep_signup(ctx):
    body = ctx.parsed
    email = state_mod.get_string(body, "email")
    password = state_mod.get_string(body, "password")
    display_name = state_mod.get_string(body, "display_name")
    if not state_mod.valid_email(email):
        raise errors.validation_failed("email must be of the form local@domain")
    if len(password) < 8:
        raise errors.validation_failed("password must be at least 8 characters")
    handle = state_mod.derive_handle(email)
    record = auth.hash_password(password)
    with state_mod.STATE_LOCK:
        payload = auth.signup_commit(email, record, display_name, handle)
    return 201, payload


@route("POST", r"/auth/login", public=True)
def ep_login(ctx):
    body = ctx.parsed
    email = state_mod.get_string(body, "email")
    password = state_mod.get_string(body, "password")
    with state_mod.STATE_LOCK:
        user_id, record = auth.login_copy_hash(email)
    if record is None or not auth.verify_password(password, record):
        raise errors.unauthenticated("wrong password or unknown email")
    with state_mod.STATE_LOCK:
        payload = auth.login_finish(user_id)
    return 200, payload


@route("POST", r"/_test/reset", public=True)
def ep_reset(ctx):
    service = testctl.build_from_fixture(ctx.parsed)
    with state_mod.STATE_LOCK:
        state_mod.set_state(service)
    return 204, None


@route("GET", r"/_test/export", public=True)
def ep_export(ctx):
    with state_mod.STATE_LOCK:
        payload = testctl.export_snapshot()
    return 200, payload


@route("POST", r"/_test/import", public=True)
def ep_import(ctx):
    service = testctl.validate_import(ctx.parsed)
    with state_mod.STATE_LOCK:
        state_mod.set_state(service)
    return 204, None


# --- authenticated endpoints ---------------------------------------------------


@route("GET", r"/me")
def ep_me(ctx, user, service):
    return 200, auth.me_response(user)


# --- payments, activity, requests (batches 2+) ---------------------------------

route("POST", r"/payments", idempotent=True)(payments.create_payment)
route("GET", r"/activity")(payments.activity)
route("POST", r"/requests", idempotent=True)(requests_endpoints.create_request)
route("POST", r"/requests/(?P<id>[^/]+)/pay", idempotent=True)(requests_endpoints.pay_request)
route("POST", r"/requests/(?P<id>[^/]+)/decline")(requests_endpoints.decline_request)
route("POST", r"/requests/(?P<id>[^/]+)/cancel")(requests_endpoints.cancel_request)
route("GET", r"/requests")(requests_endpoints.list_requests)
route("POST", r"/settlements", idempotent=True)(settlements.create_settlement)


def run_idempotent(ctx, user, fn):
    """The shared idempotent-write pipeline (design.md section 5). Called while holding
    STATE_LOCK, with authentication already done; fn validates, commits and returns
    (status, response_body). A 4xx raised by fn leaves no record: the key stays free."""
    service = state_mod.get()
    key = idempotency.check_key_header(ctx.headers)
    outcome, stored = idempotency.resolve(service, user["id"], ctx.method, ctx.path, key, ctx.parsed)
    if outcome == "replay":
        return 200, stored
    status, response = fn(ctx, user, service)
    idempotency.store(service, user["id"], ctx.method, ctx.path, key, ctx.parsed, status, response)
    return status, response


def register_test_only_routes():
    """Hook for unit tests: append stub routes exercising the shared pipelines."""
    pass


# --- HTTP plumbing --------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Pocketful/1"
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def __getattr__(self, name):
        """Any HTTP method other than GET/POST is routed through the same pipeline and
        answered with the standard error envelope (405 for known paths) — never the
        base class's plain-text 501, which would violate the no-5xx rule."""
        if name.startswith("do_"):
            method = name[3:]
            def generic():
                self._dispatch(method)
            return generic
        raise AttributeError(name)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            size = int(length)
        except ValueError:
            raise errors.malformed_request("invalid Content-Length")
        if size <= 0:
            return b""
        return self.rfile.read(size)

    def _dispatch(self, method):
        try:
            split = urlsplit(self.path)
            path = unquote(split.path)
            query = parse_qs(split.query, keep_blank_values=True)
            raw = self._read_body()
            if method == "POST":
                if raw.strip() == b"":
                    parsed = {}
                else:
                    try:
                        parsed = json.loads(raw.decode("utf-8"),
                                            parse_constant=_reject_constants)
                    except (ValueError, UnicodeDecodeError):
                        raise errors.malformed_request("body is not valid JSON")
                state_mod.require_object(parsed)
            else:
                parsed = None
            route, match = _find_route(method, path)
            ctx = Ctx(method, path, parsed, self.headers, query)
            ctx.params = match.groupdict() if match else {}
            status, payload = self._run(route, ctx)
            if payload is None:
                self._send_empty(status)
            else:
                self._send_json(status, payload)
        except errors.ApiError as exc:
            self._send_json(exc.status, exc.body())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": {"code": "internal_error", "message": "internal error"}})

    def _run(self, route, ctx):
        if route.public:
            return route.fn(ctx)
        with state_mod.STATE_LOCK:
            user = auth.authenticate(ctx.headers)
            if route.idempotent:
                if not isinstance(ctx.parsed, dict):
                    raise errors.malformed_request("body must be a JSON object")
                return run_idempotent(ctx, user, route.fn)
            return route.fn(ctx, user, state_mod.get())

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256


def make_server(port=0):
    register_test_only_routes()
    state_mod.set_state(state_mod.new_service("EUR", 2))
    return Server(("0.0.0.0", port), Handler)


def main():
    port = int(os.environ.get("PORT") or DEFAULT_PORT)
    server = make_server(port)
    server.serve_forever()


if __name__ == "__main__":
    main()