"""Pocketful stage-2 acceptance — browser UI tests (Playwright).

Drives the real rendered pages through the spec's data-testid attributes at
375px and desktop widths. Skips (visibly) when playwright is not installed.
"""

import json
import re
import time

from core import Check, Skip, eq, expect, fixture, fx_auth, fx_user, money, test

try:
    from playwright.sync_api import sync_playwright
    HAS_PW = True
except Exception:
    HAS_PW = False

PW_PW = "hunter2hunter2"


def need_pw():
    if not HAS_PW:
        raise Skip("playwright not installed (pip install playwright)")


# ----------------------------------------------------------------- helpers

def tid(page, name):
    return page.locator(f'[data-testid="{name}"]')


def txt(page, name):
    loc = tid(page, name)
    loc.wait_for(state="attached", timeout=8000)
    return (loc.text_content() or "").strip()


def login_ui(page, ctx, handle):
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill(f"{handle}@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)


def no_horizontal_scroll(page, width):
    sw = page.evaluate(
        "Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0)")
    expect(sw <= width + 1,
           f"horizontal scroll at {width}px: scrollWidth={sw}")


def label_ok(page, name):
    return tid(page, name).evaluate("""e => {
        const al = e.getAttribute('aria-label');
        if (al && al.trim()) return true;
        const lb = e.getAttribute('aria-labelledby');
        if (lb) { const t = document.getElementById(lb);
            if (t && t.textContent.trim()) return true; }
        if (e.id) { const l = document.querySelector('label[for="' +
            (window.CSS && CSS.escape ? CSS.escape(e.id) : e.id) + '"]');
            if (l && l.textContent.trim() && l.offsetParent !== null) return true; }
        const p = e.parentElement;
        if (p && p.tagName === 'LABEL' && p.textContent.trim()) return true;
        return false;
    }""")


FOCUS_SNIPPET = """e => { const s = getComputedStyle(e);
    return [s.outlineWidth + '|' + s.outlineStyle, s.boxShadow,
            s.backgroundColor, s.borderColor, s.textDecorationLine]; }"""


def tab_to(page, name, max_tabs=40):
    for _ in range(max_tabs):
        if page.evaluate(
                "document.activeElement && document.activeElement.dataset"
                " ? document.activeElement.dataset.testid : null") == name:
            return True
        page.keyboard.press("Tab")
    return False


def focus_visible(page, name):
    el = tid(page, name)
    el.evaluate("e => e.blur()")
    before = el.evaluate(FOCUS_SNIPPET)
    expect(tab_to(page, name), f"keyboard focus never reaches {name}")
    after = el.evaluate(FOCUS_SNIPPET)
    expect(before != after,
           f"no visible focus indicator on {name}: {before} == {after}")


def rgb(s):
    s = s.replace("rgba(", "").replace("rgb(", "").replace(")", "")
    parts = [float(x) for x in s.split(",")]
    return parts


def luminance(c):
    def chan(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = chan(c[0]), chan(c[1]), chan(c[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(page, name, minimum=4.5):
    el = tid(page, name)
    fg = rgb(el.evaluate("e => getComputedStyle(e).color"))
    bg = el.evaluate("""e => { let n = e;
        while (n && n !== document.documentElement) {
            const c = getComputedStyle(n).backgroundColor;
            if (c && !c.startsWith('rgba(0, 0, 0, 0)') && c !== 'transparent')
                return c;
            n = n.parentElement; }
        return 'rgb(255,255,255)'; }""")
    bg = rgb(bg)
    l1, l2 = luminance(fg), luminance(bg)
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    expect(ratio >= minimum,
           f"contrast {ratio:.2f} < {minimum} on {name} (fg {fg} bg {bg})")


def feed_ids(page):
    return page.locator('[data-testid^="activity-item-"]').count()


def form_values(page):
    return {f: tid(page, f).input_value()
            for f in ("pay-handle", "pay-amount", "pay-note")}


def wait_text(page, name, timeout_s=4.0):
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        loc = tid(page, name)
        if loc.count():
            last = (loc.text_content() or "").strip()
            if last:
                return last
        page.wait_for_timeout(100)
    return last


def auth_ui_test_users(ctx):
    return [fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 5000),
            fx_user("u_cyd", "cyd", 5000), fx_user("u_dee", "dee", 5000)]


def login_as(ctx, page, handle, fx):
    ctx.reset(fx)
    page.set_viewport_size({"width": 1280, "height": 800})
    login_ui(page, ctx, handle)
    page.evaluate("window.__no_reload_marker = 42")


def assert_no_reload(page, what):
    eq(page.evaluate("window.__no_reload_marker"), 42,
       f"{what}: page must not reload")


# ----------------------------------------------------------------- auth screens

@test("ui_signup_login", "R104 R113 R114")
def ui_signup_login(ctx):
    need_pw()
    page = ctx.page()
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/signup")
    tid(page, "signup-email").fill("erin@example.com")
    tid(page, "signup-password").fill("longpassword1")
    tid(page, "signup-display-name").fill("Erin")
    tid(page, "signup-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    expect("Erin" in txt(page, "current-user"),
           f"current-user contains display name: {txt(page, 'current-user')!r}")
    eq(txt(page, "current-handle"), "erin", "current-handle exact, no @")
    expect(tid(page, "logout-button").is_visible(), "logout button visible")
    expect(tid(page, "auth-error").count() == 0 or
           not tid(page, "auth-error").is_visible(),
           "auth-error absent without an error")
    tid(page, "logout-button").click()
    tid(page, "logout-button").wait_for(state="hidden", timeout=8000)
    # failed login surfaces stage-1 error through auth-error
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill("wrong-password")
    tid(page, "login-submit").click()
    tid(page, "auth-error").wait_for(state="visible", timeout=8000)
    expect((txt(page, "auth-error") or "") != "", "auth-error nonempty")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    expect(tid(page, "auth-error").count() == 0 or
           not tid(page, "auth-error").is_visible(),
           "auth-error gone after success")
    # duplicate-email signup -> auth-error (stage-1 email_taken)
    tid(page, "logout-button").click()
    tid(page, "logout-button").wait_for(state="hidden", timeout=8000)
    page.goto(ctx.base + "/signup")
    tid(page, "signup-email").fill("ada@example.com")
    tid(page, "signup-password").fill("longpassword1")
    tid(page, "signup-display-name").fill("Imposter")
    tid(page, "signup-submit").click()
    tid(page, "auth-error").wait_for(state="visible", timeout=8000)


# ----------------------------------------------------------------- wallet display

@test("ui_wallet_with_and_without_holds", "R115 R120 R121 R147 R185 R186 R187 R191 R108")
def ui_wallet_with_and_without_holds(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx), authorizations=[
        fx_auth("a_h", "u_ada", "u_bob", 2000, "open", 7200)])
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/")
    eq(txt(page, "wallet-balance"), "100.00 EUR", "wallet-balance text")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "10000",
       "wallet-balance data-amount")
    eq(txt(page, "wallet-available"), "80.00 EUR", "wallet-available text")
    eq(tid(page, "wallet-available").get_attribute("data-amount"), "8000",
       "wallet-available data-amount")
    eq(txt(page, "wallet-held"), "20.00 EUR", "wallet-held text")
    eq(tid(page, "wallet-held").get_attribute("data-amount"), "2000",
       "wallet-held data-amount")
    fs_avail = tid(page, "wallet-available").evaluate(
        "e => getComputedStyle(e).fontSize")
    fs_bal = tid(page, "wallet-balance").evaluate(
        "e => getComputedStyle(e).fontSize")
    expect(float(fs_avail.rstrip("px")) >= float(fs_bal.rstrip("px")),
           f"available must be the headline: {fs_avail} vs balance {fs_bal}")
    # without holds: held absent, available == balance
    login_as(ctx, page, "ada", fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    eq(txt(page, "wallet-balance"), "100.00 EUR", "balance no holds")
    eq(txt(page, "wallet-available"), "100.00 EUR", "available == balance")
    expect(tid(page, "wallet-held").count() == 0 or
           not tid(page, "wallet-held").is_visible(),
           "wallet-held absent when held is zero")
    # seeded holds visible immediately after reset with no action (R191)
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/")
    eq(txt(page, "wallet-available"), "80.00 EUR",
       "seeded holds reflected with no action taken")


# ----------------------------------------------------------------- pay form

@test("ui_pay_decimal_rules", "R118 R119 R115")
def ui_pay_decimal_rules(ctx):
    need_pw()
    page = ctx.page()
    login_as(ctx, page, "ada", fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    tid(page, "pay-handle").fill("bob")
    cases = [("15.00", "85.00", "8500"), ("15", "70.00", "7000"),
             ("15.5", "54.50", "5450")]
    for amount, bal_text, bal_attr in cases:
        tid(page, "pay-amount").fill(amount)
        tid(page, "pay-submit").click()
        page.wait_for_timeout(300)
        expect(txt(page, "wallet-balance") == f"{bal_text} EUR" or
               tid(page, "wallet-balance").get_attribute("data-amount") == bal_attr,
               f"pay {amount!r}: balance should move to {bal_text}; got "
               f"{txt(page, 'wallet-balance')!r}")
    for bad in ("15.005", "abc"):
        before_feed = feed_ids(page)
        tid(page, "pay-amount").fill(bad)
        tid(page, "pay-submit").click()
        page.wait_for_timeout(300)
        expect(tid(page, "pay-error").is_visible(),
               f"pay {bad!r}: form error element must show")
        eq(tid(page, "wallet-balance").get_attribute("data-amount"), "5450",
           f"pay {bad!r}: no request sent (balance unchanged)")
        eq(feed_ids(page), before_feed, f"pay {bad!r}: no new feed entry")
    # visibility options
    opts = tid(page, "pay-visibility").locator("option").all_text_contents()
    vals = tid(page, "pay-visibility").locator("option").evaluate_all(
        "els => els.map(e => e.value)")
    expect(set(vals) <= {"public", "private"} and
           {"public", "private"} <= set(vals),
           f"pay-visibility options must be public/private: {vals}")


@test("ui_pay_resubmit_noop", "R116 R129")
def ui_pay_resubmit_noop(ctx):
    need_pw()
    page = ctx.page()
    login_as(ctx, page, "ada", fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("10.00")
    tid(page, "pay-note").fill("first")
    tid(page, "pay-submit").click()
    page.wait_for_timeout(400)
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "9000",
       "balance falls once")
    eq(feed_ids(page), 1, "one feed entry")
    # resubmit without changing a field: no second payment
    tid(page, "pay-submit").click()
    page.wait_for_timeout(400)
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "9000",
       "balance falls only once")
    eq(feed_ids(page), 1, "feed still one payment")
    expect(not tid(page, "pay-error").is_visible(), "pay-error absent")
    # changing a field -> a new payment
    tid(page, "pay-note").fill("second")
    tid(page, "pay-submit").click()
    page.wait_for_timeout(400)
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "8000",
       "second payment moves money")
    eq(feed_ids(page), 2, "feed has two payments")
    assert_no_reload(page, "resubmit flow")


@test("ui_pay_uncertain_then_retry", "R135 R136 R116 R117")
def ui_pay_uncertain_then_retry(ctx):
    need_pw()
    page = ctx.page()
    login_as(ctx, page, "ada", fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    state = {"aborted": 0}

    def maybe_abort(route):
        if route.request.method == "POST" and state["aborted"] == 0:
            state["aborted"] += 1
            route.abort()
        else:
            route.continue_()

    page.route("**/payments", maybe_abort)
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("13.00")
    tid(page, "pay-note").fill("uncertain")
    tid(page, "pay-submit").click()
    tid(page, "pay-uncertain").wait_for(state="visible", timeout=8000)
    expect((txt(page, "pay-uncertain") or "") != "", "pay-uncertain nonempty")
    expect(not tid(page, "pay-error").is_visible(),
           "unknown outcome is not a rejection")
    eq(form_values(page), {"pay-handle": "bob", "pay-amount": "13.00",
                           "pay-note": "uncertain"},
       "form preserved and retryable")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "10000",
       "balance not optimistically changed")
    # retry with the same key and body (no field changed)
    tid(page, "pay-submit").click()
    tid(page, "pay-uncertain").wait_for(state="hidden", timeout=8000)
    page.wait_for_timeout(400)
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "8700",
       "money moved exactly once after retry")
    expect(not tid(page, "pay-error").is_visible(), "no pay-error after success")
    eq(feed_ids(page), 1, "exactly one feed entry")


@test("ui_pay_refused_by_concurrent_spend", "R133")
def ui_pay_refused_by_concurrent_spend(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/")
    # another client (a second session of the same user) spends the balance
    other = ctx.person("ada", via_fixture=True)
    r = other.pay("bob", 9500, key="drain-1")
    eq(r.status, 201, "another client drained the wallet")
    tid(page, "pay-handle").fill("cyd")
    tid(page, "pay-amount").fill("50.00")
    tid(page, "pay-note").fill("will fail")
    tid(page, "pay-submit").click()
    tid(page, "pay-error").wait_for(state="visible", timeout=8000)
    expect((txt(page, "pay-error") or "") != "", "pay-error nonempty")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "500",
       "balance/feed refreshed after refusal")
    eq(feed_ids(page), 1, "feed refreshed with the other client's payment")
    eq(form_values(page), {"pay-handle": "cyd", "pay-amount": "50.00",
                           "pay-note": "will fail"},
       "all pay inputs preserved")


# ----------------------------------------------------------------- refresh

@test("ui_wallet_refresh_latest_wins", "R130 R131 R132")
def ui_wallet_refresh_latest_wins(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/")
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("5.00")
    ada = ctx.person("ada", via_fixture=True)
    real = ctx.api.get("/me", token=ada.token).json
    old_me = real
    new_me = dict(real, balance=real["balance"] - 1000,
                  total=real["total"] - 1000,
                  available=real["available"] - 1000)

    n = {"me": 0}

    def route_me(route):
        if route.request.method != "GET" or not route.request.url.endswith("/me"):
            route.continue_()
            return
        n["me"] += 1
        if n["me"] == 1:
            page.wait_for_timeout(700)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(old_me))
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(new_me))

    page.route("**/me", route_me)
    tid(page, "wallet-refresh").click()
    page.wait_for_timeout(120)
    tid(page, "wallet-refresh").click()
    page.wait_for_timeout(1200)
    eq(txt(page, "wallet-balance"), "90.00 EUR",
       "latest refresh wins over the delayed earlier read")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "9000",
       "data-amount updated")
    eq(form_values(page), {"pay-handle": "bob", "pay-amount": "5.00",
                           "pay-note": ""},
       "wallet-refresh must not clear the pay form")
    assert_no_reload(page, "refresh")


# ----------------------------------------------------------------- requests screen

@test("ui_requests_screen_flows", "R104 R125 R126 R129 R133")
def ui_requests_screen_flows(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    # seed via API: bob requests 30 from ada (incoming); ada requests 12 from bob
    bob = ctx.person("bob", via_fixture=True)
    rq_in = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 3000,
                                            "note": "dinner"},
                         token=bob.token, key="ui-rq-1").json
    ada = ctx.person("ada", via_fixture=True)
    rq_out = ada.api.post("/requests", body={"payer_handle": "bob", "amount": 1200},
                          token=ada.token, key="ui-rq-2").json
    page.goto(ctx.base + "/requests")
    expect(tid(page, "empty-requests").count() == 0 or
           not tid(page, "empty-requests").is_visible(), "lists nonempty")
    item = tid(page, f"request-item-{rq_in['request_id']}")
    eq(item.get_attribute("data-status"), "pending", "incoming pending")
    expect(tid(page, f"request-pay-{rq_in['request_id']}").is_visible(),
           "pay button on pending incoming")
    expect(tid(page, f"request-decline-{rq_in['request_id']}").is_visible(),
           "decline button on pending incoming")
    out_item = tid(page, f"request-item-{rq_out['request_id']}")
    expect(tid(page, f"request-cancel-{rq_out['request_id']}").is_visible(),
           "cancel button on pending outgoing")
    expect(tid(page, f"request-pay-{rq_out['request_id']}").count() == 0,
           "no pay button on outgoing")
    # pay it: money moves, list refreshes without reload
    tid(page, f"request-pay-{rq_in['request_id']}").click()
    page.wait_for_timeout(500)
    eq(item.get_attribute("data-status"), "paid", "request paid")
    expect(tid(page, f"request-pay-{rq_in['request_id']}").count() == 0,
           "pay button gone once paid")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "7000",
       "balance fell by 30.00")
    assert_no_reload(page, "pay from requests screen")
    # decline flow on a fresh incoming request
    rq_in2 = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 900},
                          token=bob.token, key="ui-rq-2").json
    page.goto(ctx.base + "/requests")
    tid(page, f"request-decline-{rq_in2['request_id']}").click()
    page.wait_for_timeout(500)
    eq(tid(page, f"request-item-{rq_in2['request_id']}").get_attribute(
        "data-status"), "declined", "incoming declined")
    expect(tid(page, f"request-decline-{rq_in2['request_id']}").count() == 0,
           "decline button gone once declined")
    # cancel outgoing flow (re-seed a fresh outgoing request)
    rq_out2 = ada.api.post("/requests", body={"payer_handle": "bob", "amount": 300},
                           token=ada.token, key="ui-rq-3").json
    page.goto(ctx.base + "/requests")
    tid(page, f"request-cancel-{rq_out2['request_id']}").click()
    page.wait_for_timeout(500)
    eq(tid(page, f"request-item-{rq_out2['request_id']}").get_attribute(
        "data-status"), "cancelled", "outgoing cancelled")
    expect(tid(page, f"request-cancel-{rq_out2['request_id']}").count() == 0,
           "cancel button gone")
    # refused pay shows request-error and refreshes
    fx2 = fixture([fx_user("u_ada", "ada", 500), fx_user("u_bob", "bob", 5000),
                   fx_user("u_cyd", "cyd", 5000), fx_user("u_dee", "dee", 5000)])
    ctx.reset(fx2)
    bob2 = ctx.person("bob", via_fixture=True)
    rq_big = bob2.api.post("/requests",
                           body={"payer_handle": "ada", "amount": 3000},
                           token=bob2.token, key="ui-rq-big").json
    page.goto(ctx.base + "/requests")
    tid(page, f"request-pay-{rq_big['request_id']}").click()
    tid(page, "request-error").wait_for(state="visible", timeout=8000)
    eq(tid(page, f"request-item-{rq_big['request_id']}").get_attribute(
        "data-status"), "pending", "refused pay changes nothing")
    # empty state
    ctx.reset(fixture([fx_user("u_frank", "frank", 100)]))
    page.goto(ctx.base + "/requests")
    expect(tid(page, "empty-requests").is_visible(), "empty-requests when no lists")


@test("ui_stale_pay_button_disappears", "R134")
def ui_stale_pay_button_disappears(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    bob = ctx.person("bob", via_fixture=True)
    rq = bob.api.post("/requests", body={"payer_handle": "ada", "amount": 700},
                      token=bob.token, key="stale-1").json
    page.goto(ctx.base + "/requests")
    expect(tid(page, f"request-pay-{rq['request_id']}").is_visible(),
           "pay button visible")
    bob.api.post(f"/requests/{rq['request_id']}/cancel", body={},
                 token=bob.token)  # cancelled elsewhere while button visible
    tid(page, f"request-pay-{rq['request_id']}").click()
    tid(page, "request-error").wait_for(state="visible", timeout=8000)
    page.wait_for_timeout(400)
    expect(tid(page, f"request-pay-{rq['request_id']}").count() == 0 or
           not tid(page, f"request-pay-{rq['request_id']}").is_visible(),
           "stale pay button disappears after the refusal refresh")
    eq(tid(page, f"request-item-{rq['request_id']}").get_attribute("data-status"),
       "cancelled", "list refreshed to the cancelled state")


# ----------------------------------------------------------------- split screen

@test("ui_split_preview_matches_submit", "R127 R128 R82 R83")
def ui_split_preview_matches_submit(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture([fx_user("u_ada", "ada", 10000), fx_user("u_bob", "bob", 0),
                  fx_user("u_cyd", "cyd", 0), fx_user("u_dee", "dee", 0),
                  fx_user("u_erin", "erin", 0)])
    login_as(ctx, page, "ada", fx)
    ctx.person("ada", via_fixture=True)
    table = [("10.00", "bob,cyd,dee", ["3.34", "3.33", "3.33"]),
             ("0.01", "bob,cyd,dee", ["0.01", "0.00", "0.00"]),
             ("0.10", "bob,cyd,dee", ["0.04", "0.03", "0.03"]),
             ("9.99", "bob,cyd,dee", ["3.33", "3.33", "3.33"]),
             ("0.05", "ada,bob,cyd,dee,erin", ["0.01"] * 5)]
    for amount, handles, shares in table:
        page.goto(ctx.base + "/split")
        tid(page, "split-amount").fill(amount)
        tid(page, "split-handles").fill(handles)
        tid(page, "split-note").fill(f"t{amount}")
        tid(page, "split-submit").wait_for(state="visible")
        # preview appears before submitting
        tid(page, "split-preview").wait_for(state="visible", timeout=8000)
        hlist = handles.split(",")
        wait_text(page, f"split-share-{hlist[0]}")
        got = [txt(page, f"split-share-{h}").split(" ")[0]
               for h in hlist]
        eq(got, shares, f"preview shares for {amount}/{handles}")
        ada_tok = ctx.person("ada", via_fixture=True).token
        n_requests_before = len(ctx.api.get("/requests?limit=200",
                                            token=ada_tok).json["requests"])
        tid(page, "split-submit").click()
        page.wait_for_timeout(500)
        n_requests_after = len(ctx.api.get("/requests?limit=200",
                                           token=ada_tok).json["requests"])
        created = n_requests_after - n_requests_before
        participants = len(hlist) - (1 if "ada" in hlist else 0)
        eq(created, participants,
           f"submit {amount}: one request per non-caller participant")
        rows = ctx.api.get("/requests?limit=200", token=ada_tok).json["requests"]
        newest = rows[:created]
        by_payer = {r["payer_handle"]: r for r in newest}
        for h, s in zip(hlist, shares):
            if h == "ada":
                continue
            minor = int(round(float(s) * 100))
            expect(h in by_payer,
                   f"submitted split created a request for {h} ({amount})")
            if h in by_payer:
                eq(by_payer[h]["amount"], minor,
                   f"submitted shares identical to preview for {h} ({amount})")
    # split-error on a refused split
    page.goto(ctx.base + "/split")
    tid(page, "split-amount").fill("5.00")
    tid(page, "split-handles").fill("bob,ghost_handle")
    tid(page, "split-submit").click()
    tid(page, "split-error").wait_for(state="visible", timeout=8000)
    eq(tid(page, "split-amount").input_value(), "5.00", "inputs preserved")


# ----------------------------------------------------------------- authorizations screen

@test("ui_authorizations_screen", "R184 R188 R189 R190 R129 R191")
def ui_authorizations_screen(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx), authorizations=[
        fx_auth("a_in", "u_bob", "u_ada", 800, "open", 7200),
        fx_auth("a_out", "u_ada", "u_bob", 2000, "open", 7200),
        fx_auth("a_cap", "u_ada", "u_bob", 500, "captured", 7200),
        fx_auth("a_void", "u_cyd", "u_ada", 300, "voided", 7200),
    ])
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/authorizations")
    item_out = tid(page, "authorization-item-a_out")
    eq(item_out.get_attribute("data-status"), "open", "outgoing open")
    expect(tid(page, "authorization-void-a_out").is_visible(),
           "void button on own outgoing open")
    expect(tid(page, "authorization-capture-a_out").count() == 0,
           "no capture button on outgoing")
    expect(tid(page, "authorization-capture-amount-a_out").count() == 0,
           "no capture field on outgoing")
    item_in = tid(page, "authorization-item-a_in")
    expect(tid(page, "authorization-capture-a_in").is_visible(),
           "capture button on incoming open")
    eq(tid(page, "authorization-capture-amount-a_in").input_value(), "8.00",
       "capture amount pre-filled with remaining")
    expect(tid(page, "authorization-void-a_in").count() == 0,
           "no void button on incoming")
    item_cap = tid(page, "authorization-item-a_cap")
    eq(item_cap.get_attribute("data-status"), "captured", "captured status")
    eq(txt(page, "authorization-captured-a_cap"), "5.00 EUR",
       "captured amount formatted")
    expect(tid(page, "authorization-void-a_cap").count() == 0 and
           tid(page, "authorization-capture-a_cap").count() == 0,
           "no buttons on a captured authorization")
    expires = txt(page, "authorization-expires-a_in")
    expect(re.match(r"^\d{4}-\d{2}-\d{2}T", expires),
           f"RFC3339 expires text: {expires!r}")
    eq(txt(page, "authorization-amount-a_out"), "20.00 EUR", "amount formatted")
    # void via UI: hold released, list+wallet refresh without reload
    tid(page, "authorization-void-a_out").click()
    page.wait_for_timeout(500)
    eq(tid(page, "authorization-item-a_out").get_attribute("data-status"),
       "voided", "voided via UI")
    eq(txt(page, "wallet-held"), "8.00 EUR", "held falls to the incoming hold")
    assert_no_reload(page, "void from authorizations screen")
    # capture via UI with the pre-filled remainder
    tid(page, "authorization-capture-a_in").click()
    page.wait_for_timeout(500)
    eq(tid(page, "authorization-item-a_in").get_attribute("data-status"),
       "captured", "captured via UI")
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "10800",
       "ada was the receiver: total 10000 + 800 captured")
    expect(tid(page, "wallet-held").count() == 0 or
           not tid(page, "wallet-held").is_visible(), "no holds left")
    # refused capture shows authorization-error
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/authorizations")
    tid(page, "authorization-capture-amount-a_in").fill("99.00")
    tid(page, "authorization-capture-a_in").click()
    tid(page, "authorization-error").wait_for(state="visible", timeout=8000)
    eq(tid(page, "authorization-item-a_in").get_attribute("data-status"),
       "open", "refused capture changes nothing")
    # empty state for a user with no authorizations
    ctx.reset(fixture([fx_user("u_frank", "frank", 100)]))
    page.goto(ctx.base + "/authorizations")
    expect(tid(page, "empty-authorizations").is_visible(),
           "empty-authorizations shown when the list is empty")


@test("ui_authorize_form_errors", "R108 R186 R188 R119 R163")
def ui_authorize_form_errors(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    target = None
    page.goto(ctx.base + "/authorizations")
    if tid(page, "authorize-handle").count():
        where = "/authorizations"
    else:
        page.goto(ctx.base + "/")
        where = "/"
    expect(tid(page, "authorize-handle").count() > 0,
           "authorize form must exist on / or /authorizations")
    tid(page, "authorize-handle").fill("bob")
    tid(page, "authorize-amount").fill("10001.00")
    tid(page, "authorize-submit").click()
    tid(page, "authorize-error").wait_for(state="visible", timeout=8000)
    ada = ctx.person("ada", via_fixture=True)
    eq(ada.me()["held"], 0, "refused authorization created no hold")
    tid(page, "authorize-amount").fill("100.005")
    tid(page, "authorize-submit").click()
    tid(page, "authorize-error").wait_for(state="visible", timeout=8000)
    eq(ada.me()["held"], 0, "decimal-rule rejection sends no request")
    tid(page, "authorize-amount").fill("100.5")
    tid(page, "authorize-submit").click()
    page.wait_for_timeout(500)
    eq(ada.me()["held"], 10050, "15.5-style decimal submits minor units (100.5->10050)")
    # headline: available primary once holds exist
    page.goto(ctx.base + "/")
    fs_avail = tid(page, "wallet-available").evaluate(
        "e => getComputedStyle(e).fontSize")
    fs_bal = tid(page, "wallet-balance").evaluate(
        "e => getComputedStyle(e).fontSize")
    expect(float(fs_avail.rstrip("px")) >= float(fs_bal.rstrip("px")),
           f"available is the headline number: {fs_avail} vs {fs_bal}")


# ----------------------------------------------------------------- upgrade survival

@test("ui_survives_export_import", "R139 R140 R141 R142")
def ui_survives_export_import(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    page.goto(ctx.base + "/")
    state = {"aborted": 0}

    def maybe_abort(route):
        if route.request.method == "POST" and state["aborted"] == 0:
            state["aborted"] += 1
            route.abort()
        else:
            route.continue_()

    page.route("**/payments", maybe_abort)
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("13.00")
    tid(page, "pay-note").fill("pre-upgrade")
    tid(page, "pay-submit").click()
    tid(page, "pay-uncertain").wait_for(state="visible", timeout=8000)
    # export/import completes between browser requests
    export = ctx.export()
    ctx.reset(fixture([fx_user("u_zed", "zed", 1)]))
    eq(ctx.api.post("/_test/import", body=export, timeout=15).status, 204,
       "import done while the browser stays open")
    # retry the same payment: completes exactly once against imported state
    page.route("**/payments", lambda route: route.continue_())
    tid(page, "pay-submit").click()
    tid(page, "pay-uncertain").wait_for(state="hidden", timeout=8000)
    page.wait_for_timeout(400)
    eq(tid(page, "wallet-balance").get_attribute("data-amount"), "8700",
       "imported balance with exactly one payment after retry")
    expect(tid(page, "current-user").is_visible(),
           "browser still signed in after the upgrade")
    assert_no_reload(page, "export/import survival")
    # wallet-refresh shows the imported state; pending retry identity survived
    tid(page, "wallet-refresh").click()
    page.wait_for_timeout(500)
    eq(txt(page, "wallet-balance"), "87.00 EUR", "imported balance rendered")


@test("ui_activity_feed", "R122 R123 R124 R109")
def ui_activity_feed(ctx):
    need_pw()
    page = ctx.page()
    fx = fixture(auth_ui_test_users(ctx))
    login_as(ctx, page, "ada", fx)
    ada = ctx.person("ada", via_fixture=True)
    bob = ctx.person("bob", via_fixture=True)
    cyd = ctx.person("cyd", via_fixture=True)
    p1 = ada.pay("bob", 1000, note="first payment", key="af-1").json
    time.sleep(1.1)  # p1 strictly older; p2/p3 may share a timestamp
    p2 = ada.pay("bob", 2550, note="", visibility="private", key="af-2").json
    p3 = bob.pay("ada", 725, note="back", key="af-3").json
    cyd.pay("dee", 500, visibility="private", key="af-4")  # never ada's business
    page.goto(ctx.base + "/")
    tid(page, "activity-list").wait_for(state="visible", timeout=8000)
    ids = page.evaluate(
        """Array.from(document.querySelectorAll('[data-testid^="activity-item-"]'))
             .map(e => e.dataset.testid.slice('activity-item-'.length))""")
    expect(set(ids) == {p1["payment_id"], p2["payment_id"], p3["payment_id"]},
           f"feed shows exactly the payments visible to ada: {ids}")
    expect(ids[-1] == p1["payment_id"],
           f"p1 (oldest) last; p2/p3 equal timestamps may tie: {ids}")
    item2 = tid(page, f"activity-item-{p2['payment_id']}")
    eq(item2.get_attribute("data-visibility"), "private", "data-visibility private")
    eq(tid(page, f"activity-item-{p1['payment_id']}").get_attribute(
        "data-visibility"), "public", "data-visibility public")
    parties = txt(page, f"activity-parties-{p1['payment_id']}")
    expect("ada" in parties and "bob" in parties,
           f"parties contain both handles: {parties!r}")
    eq(txt(page, f"activity-amount-{p1['payment_id']}"), "10.00 EUR",
       "amount exactly formatted")
    eq(txt(page, f"activity-note-{p1['payment_id']}"), "first payment",
       "note verbatim")
    note2 = tid(page, f"activity-note-{p2['payment_id']}")
    expect(note2.count() > 0, "note element present even when empty")
    eq((note2.text_content() or "").strip(), "", "empty note is empty text")
    # stage-1 visibility semantics in the UI (R124): private hidden from cyd
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill("cyd@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    c = ctx.person("cyd", via_fixture=True)
    d = ctx.person("dee", via_fixture=True)
    c.pay("dee", 800, visibility="private", key="af-5")
    priv = c.pay("dee", 300, visibility="private", key="af-7").json
    page.goto(ctx.base + "/")
    eq(tid(page, f"activity-item-{priv['payment_id']}").count(), 1,
       "own private payment visible to the sender")
    # empty state
    ctx.reset(fixture([fx_user("u_frank", "frank", 100)]))
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill("frank@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    page.goto(ctx.base + "/")
    expect(tid(page, "empty-activity").is_visible(),
           "empty-activity instead of the list when nothing is visible")


@test("ui_testid_presence", "R106")
def ui_testid_presence(ctx):
    need_pw()
    page = ctx.page()
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)

    def present(*names):
        missing = [n for n in names if not tid(page, n).count()]
        expect(not missing, f"missing data-testid {missing}")

    present("current-user", "current-handle", "logout-button",
            "wallet-balance", "wallet-available",
            "pay-handle", "pay-amount", "pay-note", "pay-visibility",
            "pay-submit",
            "request-handle", "request-amount", "request-note", "request-submit",
            "wallet-refresh")
    expect(tid(page, "activity-list").count() or
           tid(page, "empty-activity").count(), "feed or its empty state")
    page.goto(ctx.base + "/requests")
    present("incoming-list", "outgoing-list")
    page.goto(ctx.base + "/split")
    present("split-amount", "split-handles", "split-note", "split-submit")
    page.goto(ctx.base + "/authorizations")
    present("wallet-balance", "wallet-available")
    expect(tid(page, "authorization-list").count() or
           tid(page, "empty-authorizations").count(), "list or empty state")
    # the authorize form lives on /authorizations or on /
    found = tid(page, "authorize-handle").count() > 0
    if not found:
        page.goto(ctx.base + "/")
        found = tid(page, "authorize-handle").count() > 0
    expect(found, "authorize form must exist (on /authorizations or /)")
    present("authorize-handle", "authorize-amount", "authorize-note",
            "authorize-visibility", "authorize-submit")
    # signup/login screens
    page.goto(ctx.base + "/signup")
    present("signup-email", "signup-password", "signup-display-name",
            "signup-submit")
    page.goto(ctx.base + "/login")
    present("login-email", "login-password", "login-submit")


# ----------------------------------------------------------------- layout/a11y

ROUTES = ["/", "/requests", "/split", "/signup", "/login", "/authorizations"]


def layout_checks(ctx, page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    fonts = []
    for route in ROUTES:
        page.goto(ctx.base + route)
        page.wait_for_timeout(250)
        no_horizontal_scroll(page, width)
        fonts.append(page.evaluate(
            "getComputedStyle(document.body).fontFamily"))
    expect(len(set(fonts)) == 1 and "times" not in fonts[0].lower(),
           f"consistent non-default visual system across routes: {set(fonts)}")
    # authenticated routes get deeper checks
    page.goto(ctx.base + "/")
    for name in ("pay-handle", "pay-amount", "pay-note", "pay-visibility",
                 "request-handle", "request-amount", "request-note"):
        if tid(page, name).count():
            expect(label_ok(page, name), f"visible label for {name} at {width}px")
    focus_visible(page, "pay-handle")
    focus_visible(page, "pay-amount")
    for name in ("wallet-balance", "wallet-available"):
        if tid(page, name).count():
            contrast_ratio(page, name, 4.5)


@test("ui_layout_375", "R111 R107 R110")
def ui_layout_375(ctx):
    need_pw()
    page = ctx.page()
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/login")
    page.set_viewport_size({"width": 375, "height": 812})
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    layout_checks(ctx, page, 375, 812)


@test("ui_layout_desktop", "R111 R107 R110")
def ui_layout_desktop(ctx):
    need_pw()
    page = ctx.page()
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/login")
    page.set_viewport_size({"width": 1280, "height": 800})
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    layout_checks(ctx, page, 1280, 800)


@test("ui_navigation_and_reachability", "R104 R111")
def ui_navigation_and_reachability(ctx):
    need_pw()
    page = ctx.page()
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/login")
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill(PW_PW)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for(state="visible", timeout=8000)
    for route in ("/requests", "/split", "/authorizations"):
        page.goto(ctx.base + "/")
        nav = page.locator(f'a[href="{route}"]').first
        expect(nav.count() > 0, f"{route} must be reachable through the UI")
        nav.click()
        page.wait_for_timeout(300)
        expect(page.url.rstrip("/").endswith(route),
               f"navigated to {route}, got {page.url}")
        expect(tid(page, "current-user").is_visible(),
               f"current-user on {route}")
        expect(tid(page, "logout-button").count() > 0,
               f"logout available on {route}")


@test("ui_distinct_states_loading", "R110 R136")
def ui_distinct_states_loading(ctx):
    need_pw()
    page = ctx.page()
    login_as(ctx, page, "ada", fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    gate = {"open": True}

    def slow(route):
        if gate["open"]:
            page.wait_for_timeout(400)
        route.continue_()

    page.route("**/payments", slow)
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("3.00")
    tid(page, "pay-submit").click()
    page.wait_for_timeout(120)
    loading = tid(page, "pay-submit").evaluate(
        """e => e.disabled || e.getAttribute('aria-busy') === 'true' ||
               parseFloat(getComputedStyle(e).opacity) < 1""")
    expect(loading, "a visible loading state exists while the write is in flight")
    gate["open"] = False
    page.wait_for_timeout(700)
    expect(not tid(page, "pay-error").is_visible() and
           not tid(page, "pay-uncertain").is_visible(),
           "success clears error and uncertainty")
    # refused state: another client spends the wallet first
    ctx.reset(fixture(auth_ui_test_users(ctx)))
    drainer = ctx.person("ada", via_fixture=True)
    drainer.pay("bob", 9990, key="drain-ui2")
    page.goto(ctx.base + "/")
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("50.00")
    tid(page, "pay-submit").click()
    tid(page, "pay-error").wait_for(state="visible", timeout=8000)
    refused_text = txt(page, "pay-error")
    state2 = {"aborted": 0}

    def abort_once(route):
        if route.request.method == "POST" and state2["aborted"] == 0:
            state2["aborted"] += 1
            route.abort()
        else:
            route.continue_()

    ctx.reset(fixture(auth_ui_test_users(ctx)))
    page.goto(ctx.base + "/")
    page.route("**/payments", abort_once)
    tid(page, "pay-handle").fill("bob")
    tid(page, "pay-amount").fill("4.00")
    tid(page, "pay-submit").click()
    tid(page, "pay-uncertain").wait_for(state="visible", timeout=8000)
    uncertain_text = txt(page, "pay-uncertain")
    expect(refused_text != uncertain_text,
           "refused and uncertain states must be visually/textually distinct")