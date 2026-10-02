"""Server-rendered browser UI foundation (design.md section 14, ADR-005).

Server-rendered HTML chosen by the Accept header alone (R105): a shared GET route
returns HTML when the client asks for text/html, JSON otherwise. One layout partial
carries the brand, navigation and page chrome; every screen (T17-T21) renders into it.
The cross-cutting client logic (idempotency-key derivation, submit/retry lifecycle,
sequence-numbered refresh, amount formatting/parsing) lives in /static/app.js.
"""

import html

APP_NAME = "Pebble"

NAV_ITEMS = (
    ("Home", "/"),
    ("Requests", "/requests"),
    ("Split", "/split"),
    ("Authorisations", "/authorizations"),
)


def wants_html(headers):
    """True only when the Accept header offers text/html with a nonzero quality (R105:
    negotiation by the Accept header alone). Absent header, */* and API clients get
    JSON."""
    accept = headers.get("Accept")
    if not accept:
        return False
    for part in accept.split(","):
        pieces = part.split(";")
        media = pieces[0].strip().lower()
        if media != "text/html":
            continue
        q = 1.0
        for param in pieces[1:]:
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False


def esc(text):
    return html.escape(str(text), quote=True)


def page(title, body, active=None, user=None, boot=None):
    """The shared layout partial: brand header, consistent navigation, session area,
    stylesheet and behaviour module. Every screen renders its content into <main>.
    `user` is the signed-in user row (None renders the signed-out area); `boot` is a
    dict of screen config embedded as JSON for the page's init wiring."""
    nav_links = "".join(
        '<a href="%s" class="nav-link%s">%s</a>'
        % (href, " is-active" if href == active else "", esc(label))
        for label, href in NAV_ITEMS
    )
    if user is not None:
        session_area = (
            '<div class="session">'
            '<span class="muted" data-testid="current-user">%s</span> '
            '<span class="handle-chip" data-testid="current-handle">%s</span> '
            '<button type="button" class="button button-quiet"'
            ' data-testid="logout-button" id="logout-button">Log out</button>'
            "</div>"
            % (esc(user["display_name"]), esc(user["handle"]))
        )
    else:
        session_area = (
            '<div class="session"><a class="nav-link" href="/login">Log in</a>'
            '<a class="nav-link nav-cta" href="/signup">Sign up</a></div>'
        )
    boot_script = ""
    if boot is not None:
        import json
        boot_script = ('<script>window.__PEBBLE_BOOT__ = %s;</script>'
                       % json.dumps(boot, ensure_ascii=False))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s · %s</title>\n"
        '<link rel="stylesheet" href="/static/app.css">\n'
        "</head>\n"
        "<body>\n"
        '<header class="topbar">\n'
        '<div class="topbar-inner">\n'
        '<a class="brand" href="/"><span class="brand-mark" aria-hidden="true">'
        "</span>%s</a>\n"
        '<nav class="nav" aria-label="Primary">%s</nav>\n'
        "%s\n"
        "</div>\n"
        "</header>\n"
        '<main class="page">\n'
        "%s\n"
        "</main>\n"
        '<footer class="footer"><p>%s is a demo wallet. Money is fictional.</p></footer>\n'
        "%s\n"
        '<script src="/static/app.js" defer></script>\n'
        '<script src="/static/app-boot.js" defer></script>\n'
        "</body>\n"
        "</html>\n"
        % (esc(title), esc(APP_NAME), esc(APP_NAME), nav_links, session_area, body,
           esc(APP_NAME), boot_script)
    )
# --- screen helpers --------------------------------------------------------------


def format_amount(minor, minor_units, currency):
    """Server-side twin of Pebble.formatAmount (R120/R121): exactly minor_units decimal
    places, one space, the currency code; no decimal point when minor_units is 0; no
    minus sign ever."""
    if not isinstance(minor, int) or minor < 0:
        minor = 0
    if minor_units == 0:
        text = str(minor)
    else:
        scale = 10 ** minor_units
        text = "%d.%0*d" % (minor // scale, minor_units, minor % scale)
    return "%s %s" % (text, currency)


def field(testid, label, input_html, hint=None):
    hint_html = '<p class="hint">%s</p>' % esc(hint) if hint else ""
    return (
        '<div class="field"><label class="label" for="%s">%s</label>%s%s</div>'
        % (testid, esc(label), input_html, hint_html)
    )


def text_input(testid, name=None, value="", placeholder="", inputmode=None,
               autocomplete=None, required=False):
    name = name or testid
    return (
        '<input class="input" id="%s" name="%s" type="text" value="%s"'
        ' placeholder="%s"%s%s%s data-testid="%s">'
        % (esc(testid), esc(name), esc(value), esc(placeholder),
           ' inputmode="%s"' % inputmode if inputmode else "",
           ' autocomplete="%s"' % autocomplete if autocomplete else "",
           " required" if required else "", esc(testid))
    )


def submit_button(testid, label):
    return ('<button type="button" class="button button-primary" data-testid="%s"'
            ' id="%s">%s</button>' % (esc(testid), esc(testid), esc(label)))


def error_banner(testid):
    """No server-rendered placeholder: R113/R115 say the error element is present
    only when there is one, so app.js creates the message element on demand and
    removes it on success or refresh."""
    return ""


# --- signup and login (T17, R113/R114) -------------------------------------------


def signup_page(user=None):
    inputs = (
        field("signup-email", "Email",
              text_input("signup-email", placeholder="you@example.com",
                         autocomplete="email", required=True),
              "We derive your handle from the email.")
        + field("signup-password", "Password (8+ characters)",
                '<input class="input" id="signup-password" name="signup-password"'
                ' type="password" placeholder="At least 8 characters"'
                ' autocomplete="new-password" required data-testid="signup-password">')
        + field("signup-display-name", "Display name",
                text_input("signup-display-name", placeholder="Ada", required=True))
    )
    body = (
        '<section class="card"><h1 class="card-title">Create your %s account</h1>'
        '<form class="form" id="signup-form" novalidate>%s%s%s</form>'
        '<p class="muted">Already have an account? <a href="/login">Log in</a>.</p>'
        "</section>"
        % (esc(APP_NAME), inputs, error_banner("auth-error"),
           submit_button("signup-submit", "Sign up"))
    )
    return page("Sign up", body, active="/signup", user=user,
                boot={"screen": "auth", "mode": "signup"})


def login_page(user=None):
    inputs = (
        field("login-email", "Email",
              text_input("login-email", placeholder="you@example.com",
                         autocomplete="email", required=True))
        + field("login-password", "Password",
                '<input class="input" id="login-password" name="login-password"'
                ' type="password" placeholder="Your password"'
                ' autocomplete="current-password" required data-testid="login-password">')
    )
    body = (
        '<section class="card"><h1 class="card-title">Welcome back to %s</h1>'
        '<form class="form" id="login-form" novalidate>%s%s%s</form>'
        '<p class="muted">New here? <a href="/signup">Create an account</a>.</p>'
        "</section>"
        % (esc(APP_NAME), inputs, error_banner("auth-error"),
           submit_button("login-submit", "Log in"))
    )
    return page("Log in", body, active="/login", user=user,
                boot={"screen": "auth", "mode": "login"})
