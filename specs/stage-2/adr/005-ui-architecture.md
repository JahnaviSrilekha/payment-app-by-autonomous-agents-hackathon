# ADR-005: Server-rendered pages with one shared retry/sequencing script, no SPA framework

## Context

Stage 2 adds a browser UI (five, then six, URL-addressable screens) with specific
behavioural requirements: no page reload after a successful action (R129), retry-safe
writes with a stable idempotency key tied to unchanged form fields (R116, R135), and
"latest refresh wins" ordering for out-of-order GET responses (R131). The delivery
constraints forbid any runtime network access and any CDN link (bundle every asset). No UI
framework, build tool or package manager is available inside the stdlib-only, no-network
image.

## Options

- **Single-page app (React/Vue/etc., bundled at build time).** Would need a JS build step
  (bundler, dependency install) baked into the Docker image build — network-during-build is
  allowed, but adds real complexity and image weight for capabilities the spec explicitly
  says are not required ("there is no live-update requirement," R129's "any mechanism is
  fine, including a full navigation").
- **Server-rendered HTML per route + one small hand-written vanilla-JS module, inlined, no
  dependencies.** Each route's handler renders HTML server-side (reusing the same
  state-reading functions the JSON API already calls); a single `<script>` (or one static
  file served from the same process) implements exactly the three cross-cutting behaviours
  design §14 lists (idempotency-key derivation, submit/error/uncertain lifecycle,
  sequence-numbered refresh) and nothing else framework-shaped.

## Decision

Server-rendered HTML plus one small vanilla-JS module. It needs no build step, no new
runtime dependency, and no CDN asset — it is just more Python (template strings) and one
more static file alongside the existing stdlib HTTP server, served by the same process that
already serves the JSON API. The three behaviours that must be shared across forms (key
derivation, retry/uncertain handling, sequence-numbered refresh) are written once and
called from each screen's few lines of wiring code, rather than duplicated per form.

## Consequences

- The `Accept`-header content-negotiation rule (R105) is implemented once, in the routing
  layer: the same handler function that already computes a JSON response for `/requests`
  and `/authorizations` also has an HTML-rendering branch; there is no separate "UI server."
- Visual/product-quality requirements (§B) are met with hand-written CSS (a small shared
  stylesheet, inlined or served statically) rather than a design-system library — acceptable
  per R112 ("a custom illustration, brand asset or exact visual match ... is not required"),
  but still needs deliberate typography/spacing/color/state tokens (design §14, product
  direction) since "coherent, presentation-ready" is still required.
- Because there is no virtual DOM, each refresh handler re-renders only the specific
  containers the spec names (`activity-list`, `request-item-*`, `authorization-item-*`,
  `wallet-*`) from the JSON response, by direct DOM manipulation — more code per screen than
  a framework's declarative re-render, but bounded and explicit, and easy to make
  sequence-number-aware (ADR's main point) without fighting a framework's own render
  scheduling.
