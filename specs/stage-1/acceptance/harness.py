"""Black-box acceptance harness for Pocketful stage 1.

Talks to the running service over HTTP only. Stdlib only, no external deps.
"""
import json
import re
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

MISSING_ROUTE = (404, 405)


class Resp:
    def __init__(self, status, headers, text):
        self.status = status
        self.headers = headers or {}
        self.text = text
        self._json = None
        self.parsed = False

    @property
    def json(self):
        if not self.parsed:
            try:
                self._json = json.loads(self.text) if self.text else None
            except ValueError:
                self._json = None
            self.parsed = True
        return self._json

    @property
    def code(self):
        e = self.json or {}
        err = e.get("error")
        return err.get("code") if isinstance(err, dict) else None

    def lower_headers(self):
        return {k.lower(): v for k, v in self.headers.items()}


class Http:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def request(self, method, path, body=None, token=None, headers=None, raw=None,
                send_ctype=True, timeout=20):
        url = self.base + path
        data = None
        if raw is not None:
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = urllib.request.Request(url, data=data, method=method)
        if data is not None and send_ctype:
            r.add_header("Content-Type", "application/json; charset=utf-8")
        if token:
            r.add_header("Authorization", "Bearer " + token)
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return Resp(resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:
                text = ""
            return Resp(e.code, dict(e.headers), text)
        except Exception as e:  # noqa: BLE001 - connection level failure
            return Resp(0, {}, "connection error: %r" % (e,))


class Check:
    def __init__(self):
        self.failures = []

    def true(self, cond, what, detail=""):
        if not cond:
            self.failures.append("%s%s" % (what, (" — " + str(detail)[:400]) if detail else ""))
        return cond

    def eq(self, got, want, what):
        return self.true(got == want, what, "got %r, want %r" % (got, want))

    def noteq(self, got, bad, what):
        return self.true(got != bad, what, "got %r (must not be %r)" % (got, bad))

    def is_err(self, resp, status, code, what):
        ok = True
        ok &= self.true(resp.status == status, what + ": status", "got %s body %.200s" % (resp.status, resp.text))
        ok &= self.eq(resp.code, code, what + ": error.code")
        return ok

    def envelope(self, resp, what):
        j = resp.json
        e = j.get("error") if isinstance(j, dict) else None
        ok = isinstance(e, dict)
        self.true(ok, what + ": error envelope object", resp.text[:200])
        if ok:
            self.true(isinstance(e.get("code"), str), what + ": error.code is string", resp.text[:200])
            self.true(isinstance(e.get("message"), str), what + ": error.message is string", resp.text[:200])
        return ok


def is_int_amount(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v.is_integer()
    return False


# ---------------------------------------------------------------- fixtures

def fu(uid, email, handle, balance, password="correct horse", display_name=None):
    return {"id": uid, "email": email, "password": password,
            "display_name": display_name or handle.capitalize(),
            "handle": handle, "balance": balance}


def std_fixture(operators=(), currency="EUR", minor_units=2, balances=None, payments=(), requests=()):
    b = balances or {"ada": 10000, "bob": 2500, "cy": 0, "dave": 0}
    users = [
        fu("u_ada", "ada@example.com", "ada", b.get("ada", 0)),
        fu("u_bob", "bob@example.com", "bob", b.get("bob", 0)),
        fu("u_cy", "cy@example.com", "cy", b.get("cy", 0)),
        fu("u_dave", "dave@example.com", "dave", b.get("dave", 0)),
        fu("u_frank", "frank@example.com", "frank", b.get("frank", 0)),
    ]
    f = {"currency": currency, "minor_units": minor_units, "users": users,
         "payments": list(payments), "requests": list(requests)}
    if operators:
        f["settlement_operator_ids"] = list(operators)
    return f


SEEDED_TOTAL = sum(u["balance"] for u in std_fixture()["users"])


def login(h, email, password="correct horse", ck=None, what="login"):
    r = h.request("POST", "/auth/login", body={"email": email, "password": password})
    if ck is not None:
        ck.true(r.status == 200, what + " status", "got %s %s" % (r.status, r.text[:200]))
    if r.status == 200 and r.json:
        return r.json.get("token")
    return None


class Users:
    """Login tokens for the standard fixture users."""

    def __init__(self, h, ck, handles=("ada", "bob", "cy", "dave")):
        self.tokens = {}
        for hd in handles:
            self.tokens[hd] = login(h, hd + "@example.com", ck=ck, what="login " + hd)

    def t(self, hd):
        return self.tokens.get(hd)


# ---------------------------------------------------------------- registry

TESTS = []


def test(name, *reqs, needs=()):
    def deco(fn):
        TESTS.append({"name": name, "fn": fn, "reqs": list(reqs), "needs": list(needs)})
        return fn
    return deco


CAPS = {}


def _probe(h, name, fn):
    if name not in CAPS:
        r = fn()
        CAPS[name] = r.status not in MISSING_ROUTE and r.status != 0
    return CAPS[name]


def probe_caps(h, ck):
    """Discover which endpoint groups exist. Each probe uses a fresh reset."""
    CAPS.clear()
    f = std_fixture(operators=["u_ada"], balances={"ada": 10000, "bob": 2500, "cy": 500, "dave": 0})
    r = h.request("POST", "/_test/reset", body=f)
    CAPS["reset"] = r.status == 204
    if not CAPS["reset"]:
        return
    u = Users(h, ck)
    t_ada, t_bob, t_cy = u.t("ada"), u.t("bob"), u.t("cy")
    if not t_ada:
        CAPS["auth"] = False
        return
    CAPS["auth"] = True
    CAPS["me"] = h.request("GET", "/me", token=t_ada).status not in MISSING_ROUTE
    CAPS["payments"] = h.request("POST", "/payments", token=t_ada,
                                 headers={"Idempotency-Key": "probe-pay"},
                                 body={"to_handle": "cy", "amount": 1}).status not in MISSING_ROUTE
    rq = h.request("POST", "/requests", token=t_cy, headers={"Idempotency-Key": "probe-rq"},
                   body={"payer_handle": "ada", "amount": 1})
    CAPS["requests"] = rq.status not in MISSING_ROUTE
    if rq.status == 201 and rq.json:
        CAPS["pay"] = h.request("POST", "/requests/%s/pay" % rq.json["request_id"], token=t_ada,
                                headers={"Idempotency-Key": "probe-pay2"},
                                body={"visibility": "public"}).status not in MISSING_ROUTE
        CAPS["decline"] = _probe_decline(h, t_cy, t_ada)
        CAPS["cancel"] = _probe_cancel(h, t_cy, t_ada)
    else:
        CAPS["pay"] = CAPS["decline"] = CAPS["cancel"] = False
    CAPS["splits"] = h.request("POST", "/splits", token=t_ada, headers={"Idempotency-Key": "probe-sp"},
                               body={"amount": 3, "participant_handles": ["ada", "bob"]}).status not in MISSING_ROUTE
    CAPS["settlements"] = h.request("POST", "/settlements", token=t_ada,
                                    headers={"Idempotency-Key": "probe-st"},
                                    body={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}
                                    ).status not in MISSING_ROUTE
    CAPS["activity"] = h.request("GET", "/activity", token=t_ada).status not in MISSING_ROUTE
    CAPS["list_requests"] = h.request("GET", "/requests", token=t_ada).status not in MISSING_ROUTE
    ex = h.request("GET", "/_test/export")
    CAPS["export"] = ex.status not in MISSING_ROUTE
    imp = h.request("POST", "/_test/import", raw=b"{" )
    CAPS["import"] = imp.status not in MISSING_ROUTE and imp.status in (400, 422)


def _probe_decline(h, t_requester, _t_payer):
    rq = h.request("POST", "/requests", token=t_requester, headers={"Idempotency-Key": "probe-dec"},
                   body={"payer_handle": "ada", "amount": 1})
    if rq.status != 201 or not rq.json:
        return False
    return h.request("POST", "/requests/%s/decline" % rq.json["request_id"],
                     token=t_requester).status not in MISSING_ROUTE


def _probe_cancel(h, t_requester, _t_payer):
    rq = h.request("POST", "/requests", token=t_requester, headers={"Idempotency-Key": "probe-canc"},
                   body={"payer_handle": "ada", "amount": 1})
    if rq.status != 201 or not rq.json:
        return False
    return h.request("POST", "/requests/%s/cancel" % rq.json["request_id"],
                     token=t_requester).status not in MISSING_ROUTE


def need(*caps):
    return list(caps)


# ---------------------------------------------------------------- concurrency

def run_concurrently(fns):
    n = len(fns)
    barrier = threading.Barrier(n)
    results = [None] * n
    errors = []

    def wrap(i, fn):
        try:
            barrier.wait(timeout=30)
            results[i] = fn()
        except Exception as e:  # noqa: BLE001
            errors.append(traceback.format_exc())
            results[i] = Resp(0, {}, "thread error %r" % (e,))

    threads = [threading.Thread(target=wrap, args=(i, f), daemon=True) for i, f in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    return results, errors


def check_all_responses(h, ck, responses, what, seeded_total=None, tokens=None):
    for i, r in enumerate(responses):
        ck.true(isinstance(r, Resp), what + " response %d is a response" % i, repr(r)[:200])
        if isinstance(r, Resp) and r.status:
            ck.true(r.status < 500, what + " response %d not 5xx" % i,
                    "%s %.200s" % (r.status, r.text))
            if 400 <= r.status < 500:
                ck.envelope(r, what + " response %d" % i)
    if seeded_total is not None and tokens:
        assert_conservation(h, ck, seeded_total, tokens)


def assert_conservation(h, ck, seeded_total, tokens):
    got = 0
    for hd, tk in tokens.items():
        r = h.request("GET", "/me", token=tk)
        ck.true(r.status == 200, "GET /me %s" % hd, "%s %.200s" % (r.status, r.text))
        if r.status == 200 and r.json:
            bal = r.json.get("balance")
            ck.true(isinstance(bal, int), "balance %s is int" % hd, repr(bal))
            ck.true(bal >= 0, "balance %s non-negative" % hd, repr(bal))
            got += bal if isinstance(bal, int) else 0
    ck.eq(got, seeded_total, "conservation: sum of balances")


def wait_health(h, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = h.request("GET", "/health", timeout=5)
        if r.status == 200:
            return True
        time.sleep(0.3)
    return False


def unique_key(prefix="k"):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


# ---------------------------------------------------------------- runner

NA_NOTES = {
    "R5": "Dockerfile/RUN.md presence checked statically by reviewer; suite verifies runtime behaviour only",
    "R7": "2 vCPU / 2 GiB and per-request 5s timeout not observable black-box; concurrency + startup covered by r7_r8, r45, docker mode",
    "R55": "password-hashing storage not observable over HTTP; guaranteed by design + ADR-002",
    "R89": "10s test-control timeout not observable black-box against a fast service",
}


def run_suite(h, only=None):
    ck_probe = Check()
    probe_caps(h, ck_probe)
    results = []
    for t in TESTS:
        if only and only not in t["name"]:
            continue
        missing = [c for c in t["needs"] if not CAPS.get(c)]
        if not CAPS.get("reset"):
            results.append({"name": t["name"], "reqs": t["reqs"], "status": "skip",
                            "msgs": ["service has no working POST /_test/reset; cannot isolate tests"]})
            continue
        if missing:
            results.append({"name": t["name"], "reqs": t["reqs"], "status": "skip",
                            "msgs": ["endpoint group(s) not implemented yet: %s" % ",".join(missing)]})
            continue
        ck = Check()
        try:
            t["fn"](h, ck)
            status = "pass" if not ck.failures else "fail"
        except Exception:
            ck.failures.append("unhandled exception:\n" + traceback.format_exc())
            status = "fail"
        results.append({"name": t["name"], "reqs": t["reqs"], "status": status, "msgs": ck.failures})
    return results, dict(ck_probe.failures)


def render_report(results, probe_failures):
    lines = []
    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    lines.append("RESULTS: %d passed, %d failed, %d skipped (total %d)" % (n_pass, n_fail, n_skip, len(results)))
    if probe_failures:
        lines.append("CAPABILITY PROBE ISSUES:")
        lines += ["  - " + m for m in probe_failures]
    for r in results:
        if r["status"] != "pass":
            lines.append("%s %s" % (r["status"].upper(), r["name"]))
            for m in r["msgs"]:
                lines.append("    - " + m)
    lines.append("")
    lines.append("COVERAGE (requirement -> tests [status]):")
    for rid in ["R%d" % i for i in range(1, 102)]:
        names = [(r["name"], r["status"]) for r in results if rid in r["reqs"]]
        if not names:
            if rid in NA_NOTES:
                lines.append("  %-5s N/A black-box — %s" % (rid, NA_NOTES[rid]))
            continue
        cell = ", ".join("%s[%s]" % (n, s[0].upper()) for n, s in names)
        lines.append("  %-5s %s" % (rid, cell))
    lacking = sorted({rid for r in results for rid in r["reqs"]
                      if all(x["status"] != "pass" for x in results if rid in x["reqs"])})
    pending = [rid for rid in lacking if rid not in NA_NOTES]
    if pending:
        lines.append("")
        lines.append("REQUIREMENTS WITHOUT A PASSING TEST YET: %s" % ", ".join(pending))
    return "\n".join(lines)
