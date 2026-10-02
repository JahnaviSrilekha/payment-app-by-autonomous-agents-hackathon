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
import authorizations
import errors
import idempotency
import payments
import requests as requests_endpoints
import settlements
import splits
import state as state_mod
import ui
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


class Raw:
    """A non-JSON response body (e.g. the static CSS/JS of the UI, design.md section
    14). Handlers return (status, Raw(content_type, bytes))."""

    def __init__(self, content_type, body):
        self.content_type = content_type
        self.body = body


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


# --- browser session (HTML screens) ----------------------------------------------

COOKIE_NAME = "pebble_token"


def cookie_token(headers):
    """The session token from the browser's cookie, if any (UI-only; the JSON API
    stays bearer-only per stage-1)."""
    header = headers.get("Cookie")
    if not header:
        return None
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME and value:
            return unquote(value)
    return None


def cookie_user(headers):
    """Resolve the cookie token to a user row. Call while holding STATE_LOCK."""
    token = cookie_token(headers)
    if not token:
        return None
    service = state_mod.get()
    user_id = service["tokens"].get(token)
    if user_id is None or user_id not in service["users"]:
        return None
    return service["users"][user_id]


def html_response(text):
    return Raw("text/html; charset=utf-8", text.encode("utf-8"))

@route("GET", r"/me")
def ep_me(ctx, user, service):
    payload = auth.me_response(user)
    # balance == total always; available/held are derived at read time from the open
    # holds (design.md section 11). Runs while holding STATE_LOCK, like every read.
    now = state_mod.now_utc()
    payload["total"] = user["balance"]
    payload["held"] = state_mod.held(user["id"], service, now)
    payload["available"] = state_mod.available(user["id"], service, now)
    return 200, payload


# --- payments, activity, requests (batches 2+) ---------------------------------

route("POST", r"/payments", idempotent=True)(payments.create_payment)
route("GET", r"/activity")(payments.activity)
route("POST", r"/requests", idempotent=True)(requests_endpoints.create_request)
route("POST", r"/requests/(?P<id>[^/]+)/pay", idempotent=True)(requests_endpoints.pay_request)
route("POST", r"/requests/(?P<id>[^/]+)/decline")(requests_endpoints.decline_request)
route("POST", r"/requests/(?P<id>[^/]+)/cancel")(requests_endpoints.cancel_request)
@route("GET", r"/requests", public=True)
def ep_requests_shared(ctx):
    """R104/R105: the browser and the API share /requests. HTML for Accept:
    text/html (cookie session), the stage-1 JSON list otherwise (bearer token,
    identical behaviour to the pre-UI route)."""
    if not ui.wants_html(ctx.headers):
        with state_mod.STATE_LOCK:
            user = auth.authenticate(ctx.headers)
            return requests_endpoints.list_requests(user=user, service=state_mod.get(),
                                                    ctx=ctx)
    with state_mod.STATE_LOCK:
        service = state_mod.get()
        user = cookie_user(ctx.headers)
        if user is None:
            return 200, html_response(ui.requests_page(None, [], [],
                                                       service["minor_units"],
                                                       service["currency"]))
        incoming = [requests_endpoints.request_response(
                        service, service["requests"][rid])
                    for rid in reversed(service["request_order"])
                    if service["requests"][rid]["payer_id"] == user["id"]][:50]
        outgoing = [requests_endpoints.request_response(
                        service, service["requests"][rid])
                    for rid in reversed(service["request_order"])
                    if service["requests"][rid]["requester_id"] == user["id"]][:50]
        return 200, html_response(ui.requests_page(user, incoming, outgoing,
                                                   service["minor_units"],
                                                   service["currency"]))
route("POST", r"/splits", idempotent=True)(splits.create_split)
route("POST", r"/settlements", idempotent=True)(settlements.create_settlement)
route("POST", r"/authorizations", idempotent=True)(authorizations.create_authorization)
route("POST", r"/authorizations/(?P<id>[^/]+)/capture", idempotent=True)(
    authorizations.capture_authorization)
route("POST", r"/authorizations/(?P<id>[^/]+)/void")(authorizations.void_authorization)
route("GET", r"/authorizations")(authorizations.list_authorizations)


# --- browser screens (design.md section 14; HTML only when Accept: text/html) -----

@route("GET", r"/", public=True)
def ep_home(ctx):
    if not ui.wants_html(ctx.headers):
        raise errors.not_found("no such resource")
    with state_mod.STATE_LOCK:
        service = state_mod.get()
        user = cookie_user(ctx.headers)
        if user is None:
            return 200, html_response(ui.home_page(None, None, []))
        me = auth.me_response(user)
        now = state_mod.now_utc()
        me["total"] = user["balance"]
        me["held"] = state_mod.held(user["id"], service, now)
        me["available"] = state_mod.available(user["id"], service, now)
        feed = [payments.payment_response(service, p)
                for p in reversed(service["payments"])
                if payments.visible_to(p, user["id"])][:50]
        return 200, html_response(ui.home_page(user, me, feed))


@route("GET", r"/signup", public=True)
def ep_signup_screen(ctx):
    if not ui.wants_html(ctx.headers):
        raise errors.not_found("no such resource")
    with state_mod.STATE_LOCK:
        return 200, html_response(ui.signup_page(cookie_user(ctx.headers)))


@route("GET", r"/login", public=True)
def ep_login_screen(ctx):
    if not ui.wants_html(ctx.headers):
        raise errors.not_found("no such resource")
    with state_mod.STATE_LOCK:
        return 200, html_response(ui.login_page(cookie_user(ctx.headers)))


@route("GET", r"/split", public=True)
def ep_split_screen(ctx):
    if not ui.wants_html(ctx.headers):
        raise errors.not_found("no such resource")
    with state_mod.STATE_LOCK:
        service = state_mod.get()
        return 200, html_response(ui.split_page(cookie_user(ctx.headers),
                                                service["minor_units"],
                                                service["currency"]))




# --- browser UI assets (design.md section 14: bundled, no CDN) ------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_STATIC_CACHE = {}


@route("GET", r"/static/(?P<name>app\.css|app\.js|app-boot\.js)", public=True)
def ep_static(ctx):
    name = ctx.params["name"]
    if name not in _STATIC_CACHE:
        path = os.path.join(STATIC_DIR, name)
        with open(path, "rb") as f:
            _STATIC_CACHE[name] = f.read()
    content_type = ("text/css; charset=utf-8" if name.endswith(".css")
                    else "application/javascript; charset=utf-8")
    return 200, Raw(content_type, _STATIC_CACHE[name])


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
            elif isinstance(payload, Raw):
                self._send_raw(status, payload)
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

    def _send_raw(self, status, raw):
        self.send_response(status)
        self.send_header("Content-Type", raw.content_type)
        self.send_header("Content-Length", str(len(raw.body)))
        self.end_headers()
        self.wfile.write(raw.body)

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