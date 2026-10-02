"""Pocketful stage-2 acceptance suite — shared black-box helpers.

Everything here talks to the service only over HTTP. No product code is read
or imported. Derived entirely from we-are-devs/pocketful spec stage-1.md and
stage-2.md and specs/stage-2/requirements.md (R1-R192).
"""

import json
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TESTS = []  # (name, reqs, fn) in registration order


class Check(Exception):
    """A failed assertion."""


class Skip(Exception):
    """Test cannot run in this environment."""


def test(name, reqs):
    def deco(fn):
        TESTS.append((name, reqs, fn))
        return fn
    return deco


def expect(cond, msg):
    if not cond:
        raise Check(msg)


def eq(actual, expected, what):
    expect(actual == expected, f"{what}: expected {expected!r}, got {actual!r}")


class Resp:
    def __init__(self, status, raw, headers):
        self.status = status
        self.raw = raw
        self.headers = headers
        self._json = None
        self._parsed = False

    @property
    def json(self):
        if not self._parsed:
            try:
                self._json = json.loads(self.raw.decode("utf-8"))
            except Exception:
                self._json = None
            self._parsed = True
        return self._json

    def err_code(self):
        j = self.json
        if isinstance(j, dict) and isinstance(j.get("error"), dict):
            return j["error"].get("code")
        return None

    def __repr__(self):
        return f"<Resp {self.status} {self.raw[:120]!r}>"


class API:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def req(self, method, path, body=None, token=None, key=None,
            headers=None, timeout=10, raw=None):
        h = dict(headers or {})
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        if token:
            h["Authorization"] = "Bearer " + token
        if key is not None:
            h["Idempotency-Key"] = key
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return Resp(resp.status, resp.read(), dict(resp.headers))
        except urllib.error.HTTPError as e:
            return Resp(e.code, e.read(), dict(e.headers))
        except urllib.error.URLError as e:
            raise Check(f"{method} {path}: connection error {e}")

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, **kw):
        return self.req("POST", path, **kw)

    def health(self):
        return self.get("/health")


# ---------------------------------------------------------------- fixtures

def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def fx_user(uid, handle, balance, email=None, password="hunter2hunter2",
            display_name=None):
    return {
        "id": uid,
        "email": email or f"{handle}@example.com",
        "password": password,
        "display_name": display_name or handle.capitalize(),
        "handle": handle,
        "balance": balance,
    }


def fx_auth(aid, frm, to, amount, status="open", offset_s=7200, note="",
            visibility="public"):
    """Seeded authorization. offset_s>0 future expiry, <0 past (>=1h from reset)."""
    return {
        "id": aid,
        "from_user_id": frm,
        "to_user_id": to,
        "amount": amount,
        "note": note,
        "visibility": visibility,
        "status": status,
        "expires_at": iso(datetime.now(timezone.utc) + timedelta(seconds=offset_s)),
        "created_at": iso(datetime.now(timezone.utc) - timedelta(hours=1)),
    }


def fixture(users, currency="EUR", minor_units=2, ttl=None, authorizations=None,
            payments=None, requests=None, operators=None):
    fx = {
        "currency": currency,
        "minor_units": minor_units,
        "users": users,
        "payments": payments if payments is not None else [],
        "requests": requests if requests is not None else [],
    }
    if ttl is not None:
        fx["authorization_ttl_seconds"] = ttl
    if authorizations is not None:
        fx["authorizations"] = authorizations
    if payments is not None:
        fx["payments"] = payments
    if requests is not None:
        fx["requests"] = requests
    if operators is not None:
        fx["settlement_operator_ids"] = operators
    return fx


# ---------------------------------------------------------------- people

class User:
    """A wallet persona. Fixture users log in; extra users sign up."""

    def __init__(self, api, handle, email=None, password="hunter2hunter2",
                 display_name=None):
        self.api = api
        self.handle = handle
        self.email = email or f"{handle}@example.com"
        self.password = password
        self.display_name = display_name or handle.capitalize()
        self.token = None
        self.user_id = None

    def login(self):
        r = self.api.post("/auth/login", body={"email": self.email,
                                               "password": self.password})
        expect(r.status == 200, f"login {self.handle}: {r}")
        self.token = r.json["token"]
        self.user_id = r.json["user_id"]
        return self

    def signup(self):
        r = self.api.post("/auth/signup", body={
            "email": self.email, "password": self.password,
            "display_name": self.display_name})
        expect(r.status == 201, f"signup {self.handle}: {r}")
        self.token = r.json["token"]
        self.user_id = r.json["user_id"]
        return self

    def me(self):
        r = self.api.get("/me", token=self.token)
        expect(r.status == 200, f"GET /me {self.handle}: {r}")
        return r.json

    def pay(self, to_handle, amount, note=None, visibility=None, key=None):
        body = {"to_handle": to_handle, "amount": amount}
        if note is not None:
            body["note"] = note
        if visibility is not None:
            body["visibility"] = visibility
        return self.api.post("/payments", body=body, token=self.token, key=key)

    def authorize(self, to_handle, amount, note=None, visibility=None, key=None):
        body = {"to_handle": to_handle, "amount": amount}
        if note is not None:
            body["note"] = note
        if visibility is not None:
            body["visibility"] = visibility
        return self.api.post("/authorizations", body=body, token=self.token, key=key)

    def capture(self, auth_id, body=None, key=None):
        return self.api.post(f"/authorizations/{auth_id}/capture",
                             body=body if body is not None else {},
                             token=self.token, key=key)

    def void(self, auth_id):
        return self.api.post(f"/authorizations/{auth_id}/void", body={},
                             token=self.token)

    def list_auths(self, query=""):
        out, offset = [], 0
        while True:
            sep = "&" if query else ""
            r = self.api.get(f"/authorizations?{query}{sep}limit=200&offset={offset}"
                             if query else
                             f"/authorizations?limit=200&offset={offset}",
                             token=self.token)
            expect(r.status == 200, f"GET /authorizations: {r}")
            page = r.json.get("authorizations", r.json.get("items"))
            expect(page is not None,
                   f"GET /authorizations: no list key, got {sorted(r.json)}")
            out.extend(page)
            if not r.json.get("has_more"):
                return out
            offset += len(page)

    def activity(self, query=""):
        r = self.api.get(f"/activity?{query}" if query else "/activity",
                         token=self.token)
        expect(r.status == 200, f"GET /activity: {r}")
        return r.json["payments"]


# ---------------------------------------------------------------- helpers

def parallel(n, fn, pool=40):
    """Run fn(i) for i in range(n) concurrently. Returns (results, errors)."""
    import concurrent.futures
    errs = []

    def wrap(i):
        try:
            return fn(i)
        except Exception as e:
            errs.append((i, e, traceback.format_exc(limit=4)))
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=pool) as ex:
        res = list(ex.map(wrap, range(n)))
    return res, errs


def check_wallet_shape(me_obj, what):
    for f in ("user_id", "display_name", "handle", "balance", "total",
              "available", "held", "currency", "minor_units"):
        expect(f in me_obj, f"{what}: GET /me missing field {f!r}: {me_obj}")
    eq(me_obj["balance"], me_obj["total"], f"{what}: balance == total")
    expect(isinstance(me_obj["total"], int), f"{what}: total not int")
    held, avail, total = me_obj["held"], me_obj["available"], me_obj["total"]
    expect(held >= 0, f"{what}: held negative: {me_obj}")
    expect(avail >= 0, f"{what}: available negative: {me_obj}")
    eq(avail, total - held, f"{what}: available == total - held")


def check_all_wallets(users, seeded_total, what):
    got = [u.me() for u in users]
    for m in got:
        check_wallet_shape(m, what)
    eq(sum(m["total"] for m in got), seeded_total, f"{what}: sum(total)")
    return got


def check_auth_invariants(auths, what):
    for a in auths:
        aid = a.get("authorization_id", a.get("id"))
        ca, amt = a.get("captured_amount", 0), a.get("amount")
        rem = a.get("remaining_amount")
        expect(isinstance(amt, int) and amt >= 1, f"{what} {aid}: bad amount {a}")
        expect(0 <= ca <= amt, f"{what} {aid}: captured {ca} outside [0,{amt}]")
        if rem is not None:
            expect(rem >= 0, f"{what} {aid}: negative remaining {a}")
            if a["status"] in ("captured", "voided", "expired"):
                eq(rem, 0, f"{what} {aid}: closed but remaining {rem}")
            else:
                eq(rem, amt - ca, f"{what} {aid}: remaining != amount-captured")
        pids = a.get("payment_ids")
        if pids is not None and a["status"] == "captured":
            expect(len(pids) >= 1, f"{what} {aid}: captured but no payment_ids")
        if pids is not None:
            expect(len(pids) == (1 if a.get("payment_id") else 0) or
                   (a.get("payment_id") in pids if pids else True),
                   f"{what} {aid}: payment_id not in payment_ids")


def money(amount, minor_units, currency):
    if minor_units == 0:
        return f"{amount} {currency}"
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    s = str(a).zfill(minor_units + 1)
    return f"{sign}{s[:-minor_units]}.{s[-minor_units:]} {currency}"


def parse_ts(s):
    return datetime.fromisoformat(s)


def fmt_dt(dt):
    return iso(dt)


def rfc3339_like(s, what):
    expect(isinstance(s, str) and
           re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$", s),
           f"{what}: not RFC 3339 with offset: {s!r}")
    parse_ts(s)


# ---------------------------------------------------------------- ctx

class Ctx:
    def __init__(self, base_url, stage1_url=None):
        self.api = API(base_url)
        self.stage1 = API(stage1_url) if stage1_url else None

    @property
    def base(self):
        return self.api.base

    def page(self):
        return self._ui.new_page()

    def reset(self, fx):
        r = self.api.post("/_test/reset", body=fx, timeout=15)
        expect(r.status == 204, f"reset: expected 204, got {r}")
        return r

    def person(self, handle, via_fixture=False):
        u = User(self.api, handle)
        return u.login() if via_fixture else u.signup()

    def export(self):
        r = self.api.get("/_test/export", timeout=15)
        expect(r.status == 200, f"export: {r}")
        return r.json