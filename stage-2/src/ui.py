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


def page(title, body, active=None):
    """The shared layout partial: brand header, consistent navigation, stylesheet and
    behaviour module. Every screen renders its content into <main>."""
    nav_links = "".join(
        '<a href="%s" class="nav-link%s">%s</a>'
        % (href, " is-active" if href == active else "", esc(label))
        for label, href in NAV_ITEMS
    )
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
        "</div>\n"
        "</header>\n"
        '<main class="page">\n'
        "%s\n"
        "</main>\n"
        '<footer class="footer"><p>%s is a demo wallet. Money is fictional.</p></footer>\n'
        '<script src="/static/app.js" defer></script>\n'
        "</body>\n"
        "</html>\n"
        % (esc(title), esc(APP_NAME), esc(APP_NAME), nav_links, body, esc(APP_NAME))
    )