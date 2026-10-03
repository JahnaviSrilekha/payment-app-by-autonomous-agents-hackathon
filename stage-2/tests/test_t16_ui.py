"""T16: Accept negotiation (R105), static UI assets, CSS token discipline (R110) and
the 375px/1280px layout smoke check (R111), over HTTP against the real server."""

import http.client
import json
import os
import re
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import server  # noqa: E402
import ui  # noqa: E402


def raw_client():
    """(method, path, headers) -> (status, content_type, body_text) — a client that
    does not force JSON parsing, for HTML responses."""
    port, srv = _SERVER
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    return conn


def request(method, path, headers=None):
    conn = raw_client()
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read().decode("utf-8")
    finally:
        conn.close()


_SERVER = [None, None]


def start():
    srv = server.make_server(0)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _SERVER[0], _SERVER[1] = srv.server_address[1], srv


def stop():
    _SERVER[1].shutdown()
    _SERVER[1].server_close()


def register_shared_stub():
    """A stub shared route exercising the negotiation shape every real screen reuses
    (R105): text/html gets the rendered layout, anything else gets JSON."""

    def shared(ctx):
        if ui.wants_html(ctx.headers):
            body = '<section class="card"><h1 class="card-title">Shared stub</h1></section>'
            return 200, server.Raw("text/html; charset=utf-8",
                                   ui.page("Shared", body, active="/").encode("utf-8"))
        return 200, {"shared": True}

    server.ROUTES.append(server.Route("GET", r"/_test/shared", shared, public=True))


DEMO_BODY = """
<section class="card">
  <h1 class="card-title">Wallet</h1>
  <div class="wallet">
    <p class="wallet-headline">80.00 EUR</p>
    <p class="wallet-secondary">Total 100.00 EUR</p>
    <span class="wallet-chip wallet-chip-held">Held 20.00 EUR</span>
  </div>
</section>
<section class="card">
  <h2 class="card-title">Recent activity</h2>
  <ul class="list">
    <li class="row"><div class="row-main"><p class="row-title">ada to bob</p>
      <p class="row-sub">coffee</p></div>
      <p class="row-money">4.50 EUR</p></li>
    <li class="row"><div class="row-main"><p class="row-title">bob to ada</p>
      <p class="row-sub">lunch <span class="tag-private">private</span></p></div>
      <p class="row-money">12.00 EUR</p></li>
  </ul>
  <p class="empty">Nothing here yet.</p>
  <form class="form">
    <div class="field"><label class="label" for="demo-amount">Amount</label>
      <input class="input" id="demo-amount" type="text" inputmode="decimal" value="15.00"></div>
    <button class="button button-primary" type="button">Pay</button>
  </form>
</section>
"""


def register_demo():
    """Static demo page for the layout smoke check: the shared layout and CSS tokens
    across two card sections, exactly the chrome later screens render into."""

    def demo(ctx):
        return 200, server.Raw("text/html; charset=utf-8",
                               ui.page("Demo", DEMO_BODY).encode("utf-8"))

    server.ROUTES.append(server.Route("GET", r"/_test/demo", demo, public=True))


start()
register_shared_stub()
register_demo()


class TestT16Negotiation(unittest.TestCase):
    def test_accept_html_gets_html(self):
        status, ctype, body = request("GET", "/_test/shared",
                                      {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("<title>Shared · Pebble</title>", body)
        self.assertIn('href="/static/app.css"', body)
        self.assertIn('src="/static/app.js"', body)
        self.assertIn("Pebble", body)  # brand in the page chrome

    def test_browser_shaped_accept_gets_html(self):
        browser = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8")
        status, ctype, body = request("GET", "/_test/shared", {"Accept": browser})
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertIn("Pebble", body)

    def test_without_accept_header_gets_json(self):
        status, ctype, body = request("GET", "/_test/shared")
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertEqual(json.loads(body), {"shared": True})

    def test_json_accept_gets_json(self):
        status, ctype, body = request("GET", "/_test/shared",
                                      {"Accept": "application/json"})
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertEqual(json.loads(body), {"shared": True})

    def test_wildcard_accept_gets_json(self):
        status, ctype, body = request("GET", "/_test/shared", {"Accept": "*/*"})
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertEqual(json.loads(body), {"shared": True})

    def test_q_zero_html_gets_json(self):
        status, ctype, body = request("GET", "/_test/shared",
                                      {"Accept": "text/html;q=0"})
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertEqual(json.loads(body), {"shared": True})

    def test_wants_html_unit(self):
        class H(dict):
            def get(self, key, default=None):
                return super().get(key.lower(), default)

        self.assertFalse(ui.wants_html(H()))
        self.assertFalse(ui.wants_html(H({"accept": "*/*"})))
        self.assertFalse(ui.wants_html(H({"accept": "application/json"})))
        self.assertFalse(ui.wants_html(H({"accept": "text/html;q=0"})))
        self.assertTrue(ui.wants_html(H({"accept": "text/html"})))
        self.assertTrue(ui.wants_html(H({"accept": "TEXT/HTML"})))
        self.assertTrue(ui.wants_html(H({"accept": "text/html;q=0.9,*/*;q=0.1"})))

    def test_layout_shares_nav_across_screens(self):
        status, ctype, body = request("GET", "/_test/shared", {"Accept": "text/html"})
        for label, href in ui.NAV_ITEMS:
            self.assertIn('href="%s"' % href, body)


class TestT16StaticAssets(unittest.TestCase):
    def test_app_css_served(self):
        status, ctype, body = request("GET", "/static/app.css")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/css"), ctype)
        self.assertIn(":root", body)

    def test_app_js_served(self):
        status, ctype, body = request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("application/javascript"), ctype)
        self.assertIn("parseDecimalAmount", body)
        self.assertIn("createRefreshGuard", body)

    def test_no_cdn_or_external_references(self):
        _, _, css = request("GET", "/static/app.css")
        _, _, js = request("GET", "/static/app.js")
        for blob in (css, js):
            self.assertNotIn("http://", blob)
            self.assertNotIn("https://", blob)
            self.assertNotIn("//cdn", blob)


class TestT16DesignSystem(unittest.TestCase):
    """R110: tokens defined once, demonstrably reused — no hardcoded duplicates."""

    @classmethod
    def setUpClass(cls):
        with open(server.STATIC_DIR + "/app.css", encoding="utf-8") as f:
            cls.css = f.read()
        # everything after the :root block: rules must only reference var()
        cls.rules = cls.css.split("}", 1)[1]

    def test_colour_tokens_declared_once_in_root(self):
        self.assertIn("--color-ink", self.css)
        # strip the :root block, then require no raw colour literal in any rule
        rules = self.css.split("}", 1)[1]
        hex_outside = re.findall(r"#[0-9a-fA-F]{3,8}\b", rules)
        self.assertEqual(
            hex_outside, [],
            "hardcoded colours outside :root: %s" % hex_outside[:5])

    def test_rules_consume_tokens_via_var(self):
        self.assertGreaterEqual(self.css.count("var(--"), 40)

    def test_spacing_and_radius_tokens_used_in_rules(self):
        for token in ("--space-", "--radius-", "--text-"):
            self.assertIn("var(%s" % token, self.rules)

    def test_status_states_have_distinct_tokens(self):
        for name in ("--color-success", "--color-danger", "--color-pending",
                     "--color-held", "--color-uncertain", "--color-accent",
                     "--color-focus"):
            self.assertIn(name + ":", self.css)

    def test_focus_ring_defined(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn("--focus-ring", self.css)


class TestT16LayoutSmoke(unittest.TestCase):
    """R111: usable at 375px and desktop widths, no horizontal page scrolling. A
    static demo page renders the shared components; the CSS must set no fixed width
    (or min-width) above the 375px viewport, and the page must carry the viewport
    meta and the shared stylesheet."""

    def test_demo_page_renders_with_shared_css(self):
        status, ctype, body = request("GET", "/_test/demo", {"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"), ctype)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', body)
        self.assertIn('href="/static/app.css"', body)
        self.assertIn("Pebble", body)
        self.assertIn('class="card"', body)
        self.assertIn('class="list"', body)

    def test_demo_shows_two_sections_reusing_one_system(self):
        status, _, body = request("GET", "/_test/demo", {"Accept": "text/html"})
        self.assertGreaterEqual(body.count('class="card"'), 2)

    def test_no_fixed_width_above_375px(self):
        with open(server.STATIC_DIR + "/app.css", encoding="utf-8") as f:
            css = f.read()
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        offenders = []
        for match in re.finditer(r"(min-width|width)\s*:\s*([\d.]+)px", css):
            if float(match.group(2)) > 375:
                offenders.append(match.group(0))
        self.assertEqual(
            offenders, [],
            "fixed widths above the 375px viewport force horizontal scrolling: %s"
            % offenders)
        self.assertIn("max-width", css)  # desktop width comes from max-width, fluid below

    def test_responsive_container(self):
        with open(server.STATIC_DIR + "/app.css", encoding="utf-8") as f:
            css = f.read()
        self.assertIn("max-width: 60rem", css)
        self.assertIn("width: 100%", css)
        self.assertIn("flex-wrap: wrap", css)


class TestT16WantsHtmlOnlyOnSharedRoutes(unittest.TestCase):
    def test_api_endpoint_stays_json_even_with_html_accept(self):
        status, ctype, body = request("GET", "/me", {"Accept": "text/html"})
        self.assertEqual(status, 401)  # unauthenticated, and the envelope is JSON
        self.assertTrue(ctype.startswith("application/json"), ctype)


if __name__ == "__main__":
    unittest.main()