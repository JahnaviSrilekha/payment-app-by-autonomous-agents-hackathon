/* Pebble shared behaviour module (design.md section 14).
 *
 * One module owns the cross-cutting browser logic so each screen wires itself with
 * thin call-site code:
 *   1. parseDecimalAmount / formatAmount — decimal-input and formatted-amount rules
 *      (R118-R121). The decimal parser decides entirely client-side: nonnumeric input
 *      or more than `minor_units` decimal places never sends a request (R119).
 *   2. createRefreshGuard — sequence-numbered refresh; a delayed earlier read never
 *      overwrites a later refresh, even arriving out of order (R131, A12).
 *   3. deriveKey — the idempotency key is sha256(formId + "|" + JSON of field values),
 *      so the key changes only when a field changes (R116, R135).
 *   4. fetchJSON — one fetch wrapper for the submit/refresh lifecycle: resolves with
 *      {status, body} for any HTTP response, rejects only on network failure (the
 *      lost-response case that renders pay-uncertain, R135/A13).
 *
 * Loads in the browser (window.Pebble) and under Node (module.exports) so the pure
 * logic is unit-tested without a DOM.
 */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.Pebble = factory();
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  var GLOBAL = typeof globalThis === "object" ? globalThis
    : (typeof self === "object" ? self : this);

  /* --- amounts (R118-R121) -------------------------------------------------- */

  function parseDecimalAmount(raw, minorUnits) {
    if (typeof raw !== "string") {
      return { ok: false, error: "Enter an amount" };
    }
    var text = raw.trim();
    if (!/^\d+(\.\d+)?$/.test(text)) {
      return { ok: false, error: "Enter a decimal amount, e.g. 15.00" };
    }
    var parts = text.split(".");
    var decimals = parts[1] ? parts[1].length : 0;
    if (decimals > minorUnits) {
      return {
        ok: false,
        error: "At most " + minorUnits + (minorUnits === 1 ? " decimal place" : " decimal places")
      };
    }
    var frac = (parts[1] || "").padEnd(minorUnits, "0");
    var digits = (parts[0] + frac).replace(/^0+(?=\d)/, "");
    if (digits.length > 15) {
      return { ok: false, error: "Amount is too large" };
    }
    return { ok: true, minor: Number(digits) };
  }

  function formatAmount(minor, minorUnits, currency) {
    var amount = typeof minor === "number" && isFinite(minor) ? Math.round(minor) : 0;
    if (amount < 0) {
      amount = 0; // balances are never negative; the formatter never emits a sign (R121)
    }
    var text;
    if (minorUnits === 0) {
      text = String(amount);
    } else {
      var scale = Math.pow(10, minorUnits);
      var frac = String(amount % scale).padStart(minorUnits, "0");
      text = Math.floor(amount / scale) + "." + frac;
    }
    return text + " " + currency;
  }

  /* --- sequence-numbered refresh (R131, R132, A12) --------------------------- */

  function createRefreshGuard() {
    var issued = 0;
    var applied = 0;
    return {
      issue: function () {
        issued += 1;
        return issued;
      },
      /* True when this response may be rendered: only a sequence number higher than
       * every already-rendered one wins, so a delayed earlier read is dropped. */
      arrive: function (seq) {
        if (seq <= applied) {
          return false;
        }
        applied = seq;
        return true;
      },
      highestApplied: function () {
        return applied;
      }
    };
  }

  /* --- idempotency-key derivation (R116, R135) ------------------------------- */

  function utf8Hex(text) {
    var bytes = new TextEncoder().encode(text);
    var hex = "";
    for (var i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, "0");
    }
    return hex;
  }

  function sha256Fallback(asciiHex) {
    /* Pure-JS SHA-256 over an ASCII string (the UTF-8 hex encoding above), for
     * non-secure contexts where crypto.subtle is unavailable. */
    function rr(v, a) { return (v >>> a) | (v << (32 - a)); }
    var maxWord = Math.pow(2, 32);
    var result = "";
    var words = [];
    var bitLength = asciiHex.length * 8;
    var hash = [];
    var k = [];
    var primeCounter = 0;
    var isComposite = {};
    for (var candidate = 2; primeCounter < 64; candidate++) {
      if (!isComposite[candidate]) {
        for (var i = 0; i < 313; i += candidate) {
          isComposite[i] = candidate;
        }
        hash[primeCounter] = (Math.pow(candidate, 0.5) * maxWord) | 0;
        k[primeCounter++] = (Math.pow(candidate, 1 / 3) * maxWord) | 0;
      }
    }
    asciiHex += "\u0080";
    while (asciiHex.length % 64 - 56) asciiHex += "\u0000";
    for (i = 0; i < asciiHex.length; i++) {
      var code = asciiHex.charCodeAt(i);
      words[i >> 2] |= code << ((3 - i) % 4) * 8;
    }
    words[words.length] = (bitLength / maxWord) | 0;
    words[words.length] = bitLength;
    for (var blockStart = 0; blockStart < words.length;) {
      var w = words.slice(blockStart, blockStart += 16);
      var oldHash = hash.slice(0);
      for (i = 0; i < 64; i++) {
        var w15 = w[i - 15];
        var w2 = w[i - 2];
        var a = hash[0];
        var e = hash[4];
        var s1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25);
        var ch = (e & hash[5]) ^ ((~e) & hash[6]);
        var s0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22);
        var maj = (a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]);
        var temp1 = hash[7] + s1 + k[i]
          + (w[i] = (i < 16) ? w[i]
            : (w[i - 16] + (rr(w15, 7) ^ rr(w15, 18) ^ (w15 >>> 3))
              + w[i - 7] + (rr(w2, 17) ^ rr(w2, 19) ^ (w2 >>> 10))) | 0);
        var temp2 = s0 + maj;
        hash = [(temp1 + temp2) | 0].concat(hash.slice(0, 7));
        hash[4] = (hash[4] + temp1) | 0;
      }
      for (i = 0; i < 8; i++) {
        hash[i] = (hash[i] + oldHash[i]) | 0;
      }
    }
    var result = "";
    for (i = 0; i < 8; i++) {
      for (var j = 3; j + 1; j--) {
        var byte = (hash[i] >> (j * 8)) & 255;
        result += ((byte >> 4).toString(16)) + ((byte & 15).toString(16));
      }
    }
    return result;
  }

  function subtleAvailable() {
    return typeof GLOBAL === "object" && GLOBAL !== null
      && typeof GLOBAL.crypto === "object" && GLOBAL.crypto !== null
      && typeof GLOBAL.crypto.subtle === "object"
      && typeof GLOBAL.crypto.subtle.digest === "function";
  }

  function sha256Hex(text) {
    if (subtleAvailable()) {
      return GLOBAL.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))
        .then(function (buffer) {
          var view = new Uint8Array(buffer);
          var hex = "";
          for (var i = 0; i < view.length; i++) {
            hex += view[i].toString(16).padStart(2, "0");
          }
          return hex;
        });
    }
    return Promise.resolve(sha256Fallback(utf8Hex(text)));
  }

  /* The key for a write form: stable while every field value is unchanged, and it
   * changes when any field changes — because it hashes the values themselves. */
  function deriveKey(formId, fields) {
    return sha256Hex(formId + "|" + JSON.stringify(fields));
  }

  /* --- fetch wrapper (submit/refresh lifecycle) ------------------------------- */

  function fetchJSON(url, options) {
    var init = Object.assign({ headers: {} }, options || {});
    if (init.body !== undefined && typeof init.body !== "string"
      && !(init.body instanceof Blob) && !(init.body instanceof ArrayBuffer)) {
      init.body = JSON.stringify(init.body);
      init.headers["Content-Type"] = "application/json";
    }
    return fetch(url, init).then(function (response) {
      return response.text().then(function (text) {
        var body = null;
        if (text) {
          try {
            body = JSON.parse(text);
          } catch (err) {
            body = { raw: text };
          }
        }
        return { status: response.status, body: body };
      });
    });
  }

  /* --- split shares: port of splits.split_shares (R128, A11) -----------------
   * Largest-remainder equal split in participant order: the first amount % count
   * participants get one extra minor unit. Cross-language tests pin this port to
   * the server's Python function over a sweep so the two can never diverge. */
  function splitShares(amount, count) {
    var base = Math.floor(amount / count);
    var remainder = amount % count;
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push(base + (i < remainder ? 1 : 0));
    }
    return out;
  }

  /* --- browser session (cookie; the JSON API itself stays bearer-only) -------- */

  function readCookie(name) {
    var parts = String(document.cookie).split(";");
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].trim();
      var eq = kv.indexOf("=");
      if (eq > -1 && kv.slice(0, eq) === name) {
        return decodeURIComponent(kv.slice(eq + 1));
      }
    }
    return null;
  }

  function sessionToken() {
    return readCookie("pebble_token");
  }

  function setSession(token) {
    document.cookie = "pebble_token=" + encodeURIComponent(token)
      + "; path=/; max-age=86400; samesite=lax";
  }

  function clearSession() {
    document.cookie = "pebble_token=; path=/; max-age=0; samesite=lax";
  }

  function apiFetch(method, path, body, key) {
    var headers = {};
    var token = sessionToken();
    if (token) {
      headers.Authorization = "Bearer " + token;
    }
    if (key !== undefined && key !== null) {
      headers["Idempotency-Key"] = key;
    }
    return fetchJSON(path, { method: method, body: body, headers: headers });
  }

  function withBusy(button, run) {
    /* R110/R136: a visible loading state while the write is in flight. */
    if (!button) {
      return run();
    }
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    var done = function () {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
    };
    return Promise.resolve(run()).then(function (result) {
      done();
      return result;
    }, function (err) {
      done();
      throw err;
    });
  }

  /* --- transient message elements ---------------------------------------------
   * Error/uncertain messages exist in the DOM only while there is one (R115:
   * "present only when there is one"); created on demand, removed on success. */
  function messageHost(testid) {
    var map = {
      "pay-error": "#pay-form",
      "pay-uncertain": "#pay-form",
      "request-error": "#request-form",
      "split-error": "#split-form",
      "auth-error": "#login-form"
    };
    var selector = map[testid] || "main";
    return document.querySelector(selector) || document.querySelector("main");
  }

  function showMessage(testid, kind, message) {
    removeMessage(testid);
    var el = document.createElement("p");
    el.className = "banner banner-" + kind;
    el.setAttribute("data-testid", testid);
    el.setAttribute("role", "alert");
    el.textContent = message;
    var host = messageHost(testid);
    if (!host) {
      document.body.appendChild(el);
      return;
    }
    var anchor = host.querySelector(".button, button");
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(el, anchor); // the anchor may be nested
    } else {
      host.insertBefore(el, host.firstChild);
    }
  }

  function removeMessage(testid) {
    var el = document.querySelector('[data-testid="' + testid + '"]');
    if (el) {
      el.remove();
    }
  }

  function errorMessage(body, fallback) {
    if (body && body.error && body.error.message) {
      return body.error.message;
    }
    return fallback;
  }

  /* Outcome classification for the submit lifecycle (R133/R135): any HTTP response
   * is a definite answer; only a thrown fetch (network failure / lost response) is
   * uncertain. */
  function classifyOutcome(status) {
    if (status >= 200 && status < 300) {
      return "success";
    }
    return "refused";
  }

  /* --- shared rendering helpers ------------------------------------------------ */

  function fieldOf(testid) {
    return document.querySelector('[data-testid="' + testid + '"]');
  }

  function val(testid) {
    var el = fieldOf(testid);
    return el ? el.value : "";
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function renderWallet(me) {
    var available = fieldOf("wallet-available");
    var balance = fieldOf("wallet-balance");
    if (!available || !balance) {
      return;
    }
    available.setAttribute("data-amount", String(me.available));
    available.textContent = formatAmount(me.available, me.minor_units, me.currency);
    balance.setAttribute("data-amount", String(me.total));
    balance.textContent = formatAmount(me.total, me.minor_units, me.currency);
    var held = fieldOf("wallet-held");
    if (me.held > 0) {
      if (!held) {
        held = document.createElement("span");
        held.className = "wallet-chip wallet-chip-held";
        held.setAttribute("data-testid", "wallet-held");
        var paragraph = document.createElement("p");
        paragraph.className = "wallet-secondary";
        paragraph.appendChild(held);
        balance.parentNode.insertBefore(paragraph, balance.nextSibling);
      }
      held.setAttribute("data-amount", String(me.held));
      held.textContent = formatAmount(me.held, me.minor_units, me.currency);
    } else if (held) {
      held.parentNode.remove();
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function span(testid, text) {
    var node = el("span", "", text);
    node.setAttribute("data-testid", testid);
    return node;
  }

  function feedNode(p, cfg) {
    var item = el("li", "row");
    item.setAttribute("data-testid", "activity-item-" + p.payment_id);
    item.setAttribute("data-visibility", p.visibility);
    var main = el("div", "row-main");
    var title = el("p", "row-title",
      esc(p.from_handle) + " \u2192 " + esc(p.to_handle));
    title.setAttribute("data-testid", "activity-parties-" + p.payment_id);
    main.appendChild(title);
    var sub = el("p", "row-sub");
    sub.appendChild(span("activity-note-" + p.payment_id, p.note || ""));
    if (p.visibility === "private") {
      sub.appendChild(el("span", "tag-private", "private"));
    }
    main.appendChild(sub);
    item.appendChild(main);
    var money = el("p", "row-money",
      formatAmount(p.amount, cfg.minor_units, cfg.currency));
    money.setAttribute("data-testid", "activity-amount-" + p.payment_id);
    money.setAttribute("data-amount", String(p.amount));
    item.appendChild(money);
    return item;
  }

  function renderFeed(payments, cfg) {
    var host = fieldOf("activity-list");
    var emptyHost = fieldOf("empty-activity");
    if (!host && !emptyHost) {
      return;
    }
    if (!payments.length) {
      if (host) {
        host.remove();
      }
      if (!emptyHost) {
        var empty = document.createElement("p");
        empty.className = "empty";
        empty.setAttribute("data-testid", "empty-activity");
        empty.textContent = "No activity yet.";
        (host && host.parentNode ? host.parentNode
          : document.querySelector("main")).appendChild(empty);
      }
      return;
    }
    if (emptyHost) {
      emptyHost.remove();
    }
    if (!host) {
      host = document.createElement("ul");
      host.className = "list";
      host.setAttribute("data-testid", "activity-list");
      document.querySelector("main").appendChild(host);
    }
    while (host.firstChild) {
      host.removeChild(host.firstChild);
    }
    payments.forEach(function (p) {
      host.appendChild(feedNode(p, cfg));
    });
  }

  /* --- home screen (T18) -------------------------------------------------------- */

  function initHome(boot) {
    if (!boot.signed_in) {
      return;
    }
    var guard = createRefreshGuard();
    var cfg = boot;

    async function refresh() {
      var seq = guard.issue();
      var results = await Promise.allSettled([
        apiFetch("GET", "/me"),
        apiFetch("GET", "/activity?limit=50")
      ]);
      if (!guard.arrive(seq)) {
        return; // latest refresh wins (R131): a delayed earlier read is dropped
      }
      if (results[0].status === "fulfilled" && results[0].value.status === 200) {
        renderWallet(results[0].value.body);
      }
      if (results[1].status === "fulfilled" && results[1].value.status === 200) {
        renderFeed(results[1].value.body.payments || [], cfg);
      }
    }

    document.getElementById("wallet-refresh").addEventListener("click", refresh);

    var payForm = document.getElementById("pay-form");
    var payButton = payForm.querySelector('[data-testid="pay-submit"]');
    payButton.addEventListener("click",
      function () {
        return withBusy(payButton, async function () {
        var values = {
          to_handle: val("pay-handle"),
          amount: val("pay-amount"),
          note: val("pay-note"),
          visibility: val("pay-visibility")
        };
        var parsed = parseDecimalAmount(values.amount, cfg.minor_units);
        if (!parsed.ok) {
          showMessage("pay-error", "error", parsed.error); // R119: no request sent
          return;
        }
        removeMessage("pay-error");
        removeMessage("pay-uncertain");
        var body = {
          to_handle: values.to_handle,
          amount: parsed.minor,
          note: values.note,
          visibility: values.visibility
        };
        var key = await deriveKey("pay-form",
          [values.to_handle, values.amount, values.note, values.visibility]);
        var outcome;
        try {
          outcome = await apiFetch("POST", "/payments", body, key);
        } catch (err) {
          outcome = null; // lost response (R135): uncertain, retry with same key/body
        }
        if (outcome === null) {
          showMessage("pay-uncertain", "uncertain",
            "We couldn't confirm this payment — it may not have gone through."
            + " Press Pay again without changing anything to check.");
          return;
        }
        if (classifyOutcome(outcome.status) === "success") {
          await refresh(); // R116: keep the pay form's values after success
          return;
        }
        // refused (R133): show pay-error, refresh balance/feed, preserve inputs
        showMessage("pay-error", "error", errorMessage(outcome.body, "Payment refused"));
        await refresh();
        });
      });

    var requestForm = document.getElementById("request-form");
    requestForm.querySelector('[data-testid="request-submit"]')
      .addEventListener("click", async function () {
        var values = {
          payer_handle: val("request-handle"),
          amount: val("request-amount"),
          note: val("request-note")
        };
        var parsed = parseDecimalAmount(values.amount, cfg.minor_units);
        if (!parsed.ok) {
          showMessage("request-error", "error", parsed.error); // R119
          return;
        }
        removeMessage("request-error");
        var body = {
          payer_handle: values.payer_handle,
          amount: parsed.minor,
          note: values.note
        };
        var key = await deriveKey("request-form",
          [values.payer_handle, values.amount, values.note]);
        var outcome;
        try {
          outcome = await apiFetch("POST", "/requests", body, key);
        } catch (err) {
          outcome = null;
        }
        if (outcome === null) {
          showMessage("request-error", "uncertain",
            "We couldn't confirm this request — press Request again to check.");
          return;
        }
        if (classifyOutcome(outcome.status) === "success") {
          await refresh(); // R129: balance and feed show the new state
          return;
        }
        showMessage("request-error", "error",
          errorMessage(outcome.body, "Request refused"));
      });

    refresh();
  }

  /* --- requests screen (T19) ------------------------------------------------------ */

  function requestNode(request, direction, cfg) {
    var rid = request.request_id;
    var item = el("li", "row");
    item.setAttribute("data-testid", "request-item-" + rid);
    item.setAttribute("data-status", request.status);
    var main = el("div", "row-main");
    var title = el("p", "row-title",
      esc(request.requester_handle) + " \u2192 " + esc(request.payer_handle));
    var chip = el("span", "status-chip status-" + request.status, request.status);
    title.appendChild(chip);
    main.appendChild(title);
    var sub = el("p", "row-sub");
    sub.appendChild(span("request-note-" + rid, request.note || ""));
    main.appendChild(sub);
    item.appendChild(main);
    var money = el("p", "row-money",
      formatAmount(request.amount, cfg.minor_units, cfg.currency));
    money.setAttribute("data-testid", "request-amount-" + rid);
    money.setAttribute("data-amount", String(request.amount));
    item.appendChild(money);
    var actions = el("div");
    if (direction === "incoming" && request.status === "pending") {
      var pay = el("button", "button button-primary", "Pay");
      pay.setAttribute("type", "button");
      pay.setAttribute("data-testid", "request-pay-" + rid);
      actions.appendChild(pay);
      var decline = el("button", "button button-danger", "Decline");
      decline.setAttribute("type", "button");
      decline.setAttribute("data-testid", "request-decline-" + rid);
      actions.appendChild(decline);
    }
    if (direction === "outgoing" && request.status === "pending") {
      var cancel = el("button", "button button-quiet", "Cancel");
      cancel.setAttribute("type", "button");
      cancel.setAttribute("data-testid", "request-cancel-" + rid);
      actions.appendChild(cancel);
    }
    item.appendChild(actions);
    return item;
  }

  function renderRequests(incoming, outgoing, cfg) {
    var sections = [
      ["incoming-list", incoming, "incoming"],
      ["outgoing-list", outgoing, "outgoing"]
    ];
    var anyRows = incoming.length + outgoing.length > 0;
    sections.forEach(function (entry) {
      var testid = entry[0];
      var rows = entry[1];
      var direction = entry[2];
      var host = fieldOf(testid);
      if (!host) {
        return;
      }
      while (host.firstChild) {
        host.removeChild(host.firstChild);
      }
      rows.forEach(function (r) {
        host.appendChild(requestNode(r, direction, cfg));
      });
    });
    var emptyHost = fieldOf("empty-requests");
    if (anyRows && emptyHost) {
      emptyHost.remove();
    } else if (!anyRows && !emptyHost) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.setAttribute("data-testid", "empty-requests");
      empty.textContent = "No requests yet. Ask someone for money from the home screen.";
      document.querySelector("main").appendChild(empty);
    }
  }

  function initRequests(boot) {
    if (!boot.signed_in) {
      return;
    }
    var guard = createRefreshGuard();
    var cfg = boot;

    async function refresh() {
      // R129: balance, feed and request lists refresh together on this page
      var seq = guard.issue();
      var results = await Promise.allSettled([
        apiFetch("GET", "/me"),
        apiFetch("GET", "/activity?limit=50"),
        apiFetch("GET", "/requests?direction=incoming&limit=50"),
        apiFetch("GET", "/requests?direction=outgoing&limit=50")
      ]);
      if (!guard.arrive(seq)) {
        return;
      }
      if (results[0].status === "fulfilled" && results[0].value.status === 200) {
        renderWallet(results[0].value.body);
      }
      if (results[1].status === "fulfilled" && results[1].value.status === 200) {
        renderFeed(results[1].value.body.payments || [], cfg);
      }
      var incoming = results[2].status === "fulfilled"
        && results[2].value.status === 200
        ? (results[2].value.body.requests || []) : null;
      var outgoing = results[3].status === "fulfilled"
        && results[3].value.status === 200
        ? (results[3].value.body.requests || []) : null;
      if (incoming !== null && outgoing !== null) {
        renderRequests(incoming, outgoing, cfg);
      }
    }

    document.querySelector("main").addEventListener("click", async function (event) {
      var button = event.target.closest("button");
      if (!button) {
        return;
      }
      var testid = button.getAttribute("data-testid") || "";
      var match = testid.match(/^request-(pay|decline|cancel)-(.+)$/);
      if (!match) {
        return;
      }
      var action = match[1];
      var rid = match[2];
      removeMessage("request-error");
      var outcome;
      try {
        if (action === "pay") {
          var key = await deriveKey("request-pay",
            [rid, val("request-visibility") || "public"]);
          outcome = await apiFetch("POST", "/requests/" + rid + "/pay", {}, key);
        } else {
          outcome = await apiFetch("POST", "/requests/" + rid + "/" + action);
        }
      } catch (err) {
        outcome = null;
      }
      if (outcome === null) {
        showMessage("request-error", "uncertain",
          "We couldn't confirm that action — it may not have gone through.");
        return;
      }
      if (classifyOutcome(outcome.status) !== "success") {
        // R134: a request cancelled elsewhere while its pay button is visible
        showMessage("request-error", "error",
          errorMessage(outcome.body, "That request is no longer available"));
      }
      await refresh(); // the stale button disappears on the resulting refresh
    });

    refresh();
  }

  /* --- split screen (T20) ----------------------------------------------------------- */

  function parseHandles(raw) {
    return String(raw || "").split(",").map(function (h) {
      return h.trim();
    }).filter(function (h) {
      return h.length > 0;
    });
  }

  function renderSplitPreview(amountMinor, handles, cfg) {
    var preview = fieldOf("split-preview");
    if (!preview) {
      return;
    }
    var shares = splitShares(amountMinor, handles.length);
    var list = el("ul", "list");
    handles.forEach(function (handle, i) {
      var item = el("li", "row");
      var main = el("div", "row-main");
      main.appendChild(el("p", "row-title", esc(handle)));
      item.appendChild(main);
      var money = el("p", "row-money",
        formatAmount(shares[i], cfg.minor_units, cfg.currency));
      money.setAttribute("data-testid", "split-share-" + handle);
      money.setAttribute("data-amount", String(shares[i]));
      item.appendChild(money);
      list.appendChild(item);
    });
    var title = el("h2", "card-title", "Preview");
    while (preview.firstChild) {
      preview.removeChild(preview.firstChild);
    }
    preview.appendChild(title);
    preview.appendChild(list);
    preview.hidden = false;
  }

  function hideSplitPreview() {
    var preview = fieldOf("split-preview");
    if (preview) {
      preview.hidden = true;
      preview.innerHTML = "";
    }
  }

  function initSplit(boot) {
    if (!boot.signed_in) {
      return;
    }
    var cfg = boot;
    var form = document.getElementById("split-form");

    function updatePreview() {
      var parsed = parseDecimalAmount(val("split-amount"), cfg.minor_units);
      var handles = parseHandles(val("split-handles"));
      if (!parsed.ok || handles.length < 1) {
        hideSplitPreview();
        return;
      }
      renderSplitPreview(parsed.minor, handles, cfg);
    }

    ["split-amount", "split-handles"].forEach(function (id) {
      document.getElementById(id).addEventListener("input", updatePreview);
    });

    form.querySelector('[data-testid="split-submit"]').addEventListener("click",
      async function () {
        var amountRaw = val("split-amount");
        var handles = parseHandles(val("split-handles"));
        var note = val("split-note");
        var parsed = parseDecimalAmount(amountRaw, cfg.minor_units);
        if (!parsed.ok) {
          showMessage("split-error", "error", parsed.error); // R119
          return;
        }
        if (handles.length < 1) {
          showMessage("split-error", "error", "Add at least one participant handle");
          return;
        }
        removeMessage("split-error");
        var body = {
          amount: parsed.minor,
          participant_handles: handles,
          note: note
        };
        var key = await deriveKey("split-form", [amountRaw, val("split-handles"), note]);
        var outcome;
        try {
          outcome = await apiFetch("POST", "/splits", body, key);
        } catch (err) {
          outcome = null;
        }
        if (outcome === null) {
          showMessage("split-error", "uncertain",
            "We couldn't confirm this split — press Split again to check.");
          return;
        }
        if (classifyOutcome(outcome.status) === "success") {
          // R129: any mechanism is fine — a full navigation waits for the write
          window.location.href = "/requests";
          return;
        }
        showMessage("split-error", "error",
          errorMessage(outcome.body, "Split refused")); // inputs preserved
      });
  }

  /* --- requests screen (T19) ------------------------------------------------------ */

  function requestNode(request, direction, cfg) {
    var rid = request.request_id;
    var item = el("li", "row");
    item.setAttribute("data-testid", "request-item-" + rid);
    item.setAttribute("data-status", request.status);
    var main = el("div", "row-main");
    var title = el("p", "row-title",
      esc(request.requester_handle) + " \u2192 " + esc(request.payer_handle));
    var chip = el("span", "status-chip status-" + request.status, request.status);
    title.appendChild(chip);
    main.appendChild(title);
    var sub = el("p", "row-sub");
    sub.appendChild(span("request-note-" + rid, request.note || ""));
    main.appendChild(sub);
    item.appendChild(main);
    var money = el("p", "row-money",
      formatAmount(request.amount, cfg.minor_units, cfg.currency));
    money.setAttribute("data-testid", "request-amount-" + rid);
    money.setAttribute("data-amount", String(request.amount));
    item.appendChild(money);
    var actions = el("div");
    if (direction === "incoming" && request.status === "pending") {
      var pay = el("button", "button button-primary", "Pay");
      pay.setAttribute("type", "button");
      pay.setAttribute("data-testid", "request-pay-" + rid);
      actions.appendChild(pay);
      var decline = el("button", "button button-danger", "Decline");
      decline.setAttribute("type", "button");
      decline.setAttribute("data-testid", "request-decline-" + rid);
      actions.appendChild(decline);
    }
    if (direction === "outgoing" && request.status === "pending") {
      var cancel = el("button", "button button-quiet", "Cancel");
      cancel.setAttribute("type", "button");
      cancel.setAttribute("data-testid", "request-cancel-" + rid);
      actions.appendChild(cancel);
    }
    item.appendChild(actions);
    return item;
  }

  function renderRequests(incoming, outgoing, cfg) {
    var sections = [
      ["incoming-list", incoming, "incoming"],
      ["outgoing-list", outgoing, "outgoing"]
    ];
    var anyRows = incoming.length + outgoing.length > 0;
    sections.forEach(function (entry) {
      var testid = entry[0];
      var rows = entry[1];
      var direction = entry[2];
      var host = fieldOf(testid);
      if (!host) {
        return;
      }
      while (host.firstChild) {
        host.removeChild(host.firstChild);
      }
      rows.forEach(function (r) {
        host.appendChild(requestNode(r, direction, cfg));
      });
    });
    var emptyHost = fieldOf("empty-requests");
    if (anyRows && emptyHost) {
      emptyHost.remove();
    } else if (!anyRows && !emptyHost) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.setAttribute("data-testid", "empty-requests");
      empty.textContent = "No requests yet. Ask someone for money from the home screen.";
      document.querySelector("main").appendChild(empty);
    }
  }

  function initRequests(boot) {
    if (!boot.signed_in) {
      return;
    }
    var guard = createRefreshGuard();
    var cfg = boot;

    async function refresh() {
      // R129: balance, feed and request lists refresh together on this page
      var seq = guard.issue();
      var results = await Promise.allSettled([
        apiFetch("GET", "/me"),
        apiFetch("GET", "/activity?limit=50"),
        apiFetch("GET", "/requests?direction=incoming&limit=50"),
        apiFetch("GET", "/requests?direction=outgoing&limit=50")
      ]);
      if (!guard.arrive(seq)) {
        return;
      }
      if (results[0].status === "fulfilled" && results[0].value.status === 200) {
        renderWallet(results[0].value.body);
      }
      if (results[1].status === "fulfilled" && results[1].value.status === 200) {
        renderFeed(results[1].value.body.payments || [], cfg);
      }
      var incoming = results[2].status === "fulfilled"
        && results[2].value.status === 200
        ? (results[2].value.body.requests || []) : null;
      var outgoing = results[3].status === "fulfilled"
        && results[3].value.status === 200
        ? (results[3].value.body.requests || []) : null;
      if (incoming !== null && outgoing !== null) {
        renderRequests(incoming, outgoing, cfg);
      }
    }

    document.querySelector("main").addEventListener("click", async function (event) {
      var button = event.target.closest("button");
      if (!button) {
        return;
      }
      var testid = button.getAttribute("data-testid") || "";
      var match = testid.match(/^request-(pay|decline|cancel)-(.+)$/);
      if (!match) {
        return;
      }
      var action = match[1];
      var rid = match[2];
      removeMessage("request-error");
      var outcome;
      try {
        if (action === "pay") {
          var key = await deriveKey("request-pay",
            [rid, val("request-visibility") || "public"]);
          outcome = await apiFetch("POST", "/requests/" + rid + "/pay", {}, key);
        } else {
          outcome = await apiFetch("POST", "/requests/" + rid + "/" + action);
        }
      } catch (err) {
        outcome = null;
      }
      if (outcome === null) {
        showMessage("request-error", "uncertain",
          "We couldn't confirm that action — it may not have gone through.");
        return;
      }
      if (classifyOutcome(outcome.status) !== "success") {
        // R134: a request cancelled elsewhere while its pay button is visible
        showMessage("request-error", "error",
          errorMessage(outcome.body, "That request is no longer available"));
      }
      await refresh(); // the stale button disappears on the resulting refresh
    });

    refresh();
  }

  /* --- split screen (T20) ----------------------------------------------------------- */

  function parseHandles(raw) {
    return String(raw || "").split(",").map(function (h) {
      return h.trim();
    }).filter(function (h) {
      return h.length > 0;
    });
  }

  function renderSplitPreview(amountMinor, handles, cfg) {
    var preview = fieldOf("split-preview");
    if (!preview) {
      return;
    }
    var shares = splitShares(amountMinor, handles.length);
    var list = el("ul", "list");
    handles.forEach(function (handle, i) {
      var item = el("li", "row");
      var main = el("div", "row-main");
      main.appendChild(el("p", "row-title", esc(handle)));
      item.appendChild(main);
      var money = el("p", "row-money",
        formatAmount(shares[i], cfg.minor_units, cfg.currency));
      money.setAttribute("data-testid", "split-share-" + handle);
      money.setAttribute("data-amount", String(shares[i]));
      item.appendChild(money);
      list.appendChild(item);
    });
    var title = el("h2", "card-title", "Preview");
    while (preview.firstChild) {
      preview.removeChild(preview.firstChild);
    }
    preview.appendChild(title);
    preview.appendChild(list);
    preview.hidden = false;
  }

  function hideSplitPreview() {
    var preview = fieldOf("split-preview");
    if (preview) {
      preview.hidden = true;
      preview.innerHTML = "";
    }
  }

  function initSplit(boot) {
    if (!boot.signed_in) {
      return;
    }
    var cfg = boot;
    var form = document.getElementById("split-form");

    function updatePreview() {
      var parsed = parseDecimalAmount(val("split-amount"), cfg.minor_units);
      var handles = parseHandles(val("split-handles"));
      if (!parsed.ok || handles.length < 1) {
        hideSplitPreview();
        return;
      }
      renderSplitPreview(parsed.minor, handles, cfg);
    }

    ["split-amount", "split-handles"].forEach(function (id) {
      document.getElementById(id).addEventListener("input", updatePreview);
    });

    form.querySelector('[data-testid="split-submit"]').addEventListener("click",
      async function () {
        var amountRaw = val("split-amount");
        var handles = parseHandles(val("split-handles"));
        var note = val("split-note");
        var parsed = parseDecimalAmount(amountRaw, cfg.minor_units);
        if (!parsed.ok) {
          showMessage("split-error", "error", parsed.error); // R119
          return;
        }
        if (handles.length < 1) {
          showMessage("split-error", "error", "Add at least one participant handle");
          return;
        }
        removeMessage("split-error");
        var body = {
          amount: parsed.minor,
          participant_handles: handles,
          note: note
        };
        var key = await deriveKey("split-form", [amountRaw, val("split-handles"), note]);
        var outcome;
        try {
          outcome = await apiFetch("POST", "/splits", body, key);
        } catch (err) {
          outcome = null;
        }
        if (outcome === null) {
          showMessage("split-error", "uncertain",
            "We couldn't confirm this split — press Split again to check.");
          return;
        }
        if (classifyOutcome(outcome.status) === "success") {
          // R129: any mechanism is fine — a full navigation waits for the write
          window.location.href = "/requests";
          return;
        }
        showMessage("split-error", "error",
          errorMessage(outcome.body, "Split refused")); // inputs preserved
      });
  }

  /* --- auth screens (T17) -------------------------------------------------------- */

  function initAuth(boot) {
    var form = document.getElementById(boot.mode === "signup"
      ? "signup-form" : "login-form");
    if (!form) {
      return;
    }
    form.querySelector('[data-testid="' + boot.mode + '-submit"]')
      .addEventListener("click", async function () {
        var email = val(boot.mode + "-email");
        var password = val(boot.mode + "-password");
        var body = { email: email, password: password };
        if (boot.mode === "signup") {
          body.display_name = val("signup-display-name");
        }
        var outcome;
        try {
          outcome = await apiFetch("POST", "/auth/" + boot.mode, body);
        } catch (err) {
          outcome = null;
        }
        if (outcome === null) {
          showMessage("auth-error", "error",
            "We couldn't reach the server — please try again.");
          return;
        }
        if (classifyOutcome(outcome.status) === "success") {
          setSession(outcome.body.token); // R114: stage-1 endpoints mint the token
          window.location.href = "/";
          return;
        }
        showMessage("auth-error", "error",
          errorMessage(outcome.body, "Signup failed")); // R113: error without a reload
      });

    var logout = fieldOf("logout-button");
    if (logout) {
      logout.addEventListener("click", function () {
        clearSession();
        window.location.href = "/login";
      });
    }
  }


  /* --- authorizations screen (T21, R188-R191) -------------------------------- */

  function decimalString(minor, minorUnits) {
    if (minorUnits === 0) {
      return String(minor);
    }
    var scale = Math.pow(10, minorUnits);
    return Math.floor(minor / scale) + "."
      + String(minor % scale).padStart(minorUnits, "0");
  }

  function authorizationNode(authz, viewerHandle, cfg) {
    var aid = authz.authorization_id;
    var item = el("li", "row");
    item.setAttribute("data-testid", "authorization-item-" + aid);
    item.setAttribute("data-status", authz.status);
    var main = el("div", "row-main");
    var title = el("p", "row-title",
      esc(authz.to_handle === viewerHandle ? "Incoming from " + authz.from_handle
        : "Outgoing to " + authz.to_handle));
    title.appendChild(el("span", "status-chip status-" + authz.status, authz.status));
    main.appendChild(title);
    var sub = el("p", "row-sub");
    sub.appendChild(span("authorization-note-" + aid, authz.note || ""));
    main.appendChild(sub);
    var expires = el("p", "row-sub", "Expires ");
    expires.appendChild(span("authorization-expires-" + aid, authz.expires_at));
    main.appendChild(expires);
    if (authz.status === "captured") {
      var captured = el("p", "row-sub");
      captured.appendChild(el("span", "muted", "Captured"));
      var capturedValue = el("span", "",
        formatAmount(authz.captured_amount, cfg.minor_units, cfg.currency));
      capturedValue.setAttribute("data-testid", "authorization-captured-" + aid);
      capturedValue.setAttribute("data-amount", String(authz.captured_amount));
      captured.appendChild(capturedValue);
      main.appendChild(captured);
    }
    item.appendChild(main);
    var money = el("p", "row-money",
      formatAmount(authz.amount, cfg.minor_units, cfg.currency));
    money.setAttribute("data-testid", "authorization-amount-" + aid);
    money.setAttribute("data-amount", String(authz.amount));
    item.appendChild(money);
    var actions = el("div");
    if (authz.status === "open" && authz.to_handle === viewerHandle) {
      var input = el("input", "input input-amount");
      input.setAttribute("type", "text");
      input.setAttribute("inputmode", "decimal");
      input.setAttribute("data-testid", "authorization-capture-amount-" + aid);
      input.setAttribute("aria-label", "Capture amount");
      input.value = decimalString(authz.remaining_amount, cfg.minor_units);
      actions.appendChild(input);
      var capture = el("button", "button button-primary", "Capture");
      capture.setAttribute("type", "button");
      capture.setAttribute("data-testid", "authorization-capture-" + aid);
      actions.appendChild(capture);
    }
    if (authz.status === "open" && authz.from_handle === viewerHandle) {
      var voidBtn = el("button", "button button-danger", "Void");
      voidBtn.setAttribute("type", "button");
      voidBtn.setAttribute("data-testid", "authorization-void-" + aid);
      actions.appendChild(voidBtn);
    }
    item.appendChild(actions);
    return item;
  }

  function renderAuthorizations(authorizations, cfg) {
    var host = fieldOf("authorization-list");
    var emptyHost = fieldOf("empty-authorizations");
    if (!host && !emptyHost) {
      return;
    }
    if (!authorizations.length) {
      if (host) {
        host.remove();
      }
      if (!emptyHost) {
        var empty = document.createElement("p");
        empty.className = "empty";
        empty.setAttribute("data-testid", "empty-authorizations");
        empty.textContent = "No authorizations yet.";
        document.querySelector("main").appendChild(empty);
      }
      return;
    }
    if (emptyHost) {
      emptyHost.remove();
    }
    if (!host) {
      host = document.createElement("ul");
      host.className = "list";
      host.setAttribute("data-testid", "authorization-list");
      document.querySelector("main").appendChild(host);
    }
    while (host.firstChild) {
      host.removeChild(host.firstChild);
    }
    authorizations.forEach(function (a) {
      host.appendChild(authorizationNode(a, cfg.handle, cfg));
    });
  }

  function initAuthorizations(boot) {
    if (!boot.signed_in) {
      return;
    }
    var guard = createRefreshGuard();
    var cfg = boot;

    async function refresh() {
      var seq = guard.issue();
      var results = await Promise.allSettled([
        apiFetch("GET", "/me"),
        apiFetch("GET", "/activity?limit=50"),
        apiFetch("GET", "/authorizations?limit=50")
      ]);
      if (!guard.arrive(seq)) {
        return;
      }
      if (results[0].status === "fulfilled" && results[0].value.status === 200) {
        renderWallet(results[0].value.body);
      }
      if (results[1].status === "fulfilled" && results[1].value.status === 200) {
        renderFeed(results[1].value.body.payments || [], cfg);
      }
      if (results[2].status === "fulfilled" && results[2].value.status === 200) {
        renderAuthorizations(results[2].value.body.authorizations || [], cfg);
      }
    }

    var form = document.getElementById("authorize-form");
    var authorizeButton = form.querySelector('[data-testid="authorize-submit"]');
    authorizeButton.addEventListener("click",
      function () {
        return withBusy(authorizeButton, async function () {
          var values = {
            to_handle: val("authorize-handle"),
            amount: val("authorize-amount"),
            note: val("authorize-note"),
            visibility: val("authorize-visibility")
          };
          var parsed = parseDecimalAmount(values.amount, cfg.minor_units);
          if (!parsed.ok) {
            showMessage("authorize-error", "error", parsed.error); // R119: no request
            return;
          }
          removeMessage("authorize-error");
          var body = {
            to_handle: values.to_handle,
            amount: parsed.minor,
            note: values.note,
            visibility: values.visibility
          };
          var key = await deriveKey("authorize-form",
            [values.to_handle, values.amount, values.note, values.visibility]);
          var outcome;
          try {
            outcome = await apiFetch("POST", "/authorizations", body, key);
          } catch (err) {
            outcome = null;
          }
          if (outcome === null) {
            showMessage("authorize-error", "uncertain",
              "We couldn't confirm this authorization — press Authorize again to check.");
            return;
          }
          if (classifyOutcome(outcome.status) !== "success") {
            showMessage("authorize-error", "error",
              errorMessage(outcome.body, "Authorization refused")); // incl. funds
            await refresh();
            return;
          }
          await refresh(); // wallet-held/available and the list update (R129/R191)
        });
      });

    document.querySelector("main").addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) {
        return;
      }
      var testid = button.getAttribute("data-testid") || "";
      var captureMatch = testid.match(/^authorization-capture-(.+)$/);
      var voidMatch = testid.match(/^authorization-void-(.+)$/);
      if (captureMatch) {
        return withBusy(button, async function () {
          var aid = captureMatch[1];
          var input = fieldOf("authorization-capture-amount-" + aid);
          var rawAmount = input ? input.value : "";
          var parsed = parseDecimalAmount(rawAmount, cfg.minor_units);
          if (!parsed.ok) {
            showMessage("authorization-error", "error", parsed.error); // R119
            return;
          }
          removeMessage("authorization-error");
          var key = await deriveKey("authorization-capture", [aid, rawAmount]);
          var outcome;
          try {
            outcome = await apiFetch("POST", "/authorizations/" + aid + "/capture",
              { amount: parsed.minor }, key);
          } catch (err) {
            outcome = null;
          }
          if (outcome === null) {
            showMessage("authorization-error", "uncertain",
              "We couldn't confirm this capture — press Capture again to check.");
            return;
          }
          if (classifyOutcome(outcome.status) !== "success") {
            showMessage("authorization-error", "error",
              errorMessage(outcome.body, "Capture refused"));
            await refresh();
            return;
          }
          await refresh(); // R168: the release is visible in the same step
        });
        return;
      }
      if (voidMatch) {
        return withBusy(button, async function () {
          var aid = voidMatch[1];
          removeMessage("authorization-error");
          var outcome;
          try {
            outcome = await apiFetch("POST", "/authorizations/" + aid + "/void");
          } catch (err) {
            outcome = null;
          }
          if (outcome === null) {
            showMessage("authorization-error", "uncertain",
              "We couldn't confirm this void — press Void again to check.");
            return;
          }
          if (classifyOutcome(outcome.status) !== "success") {
            showMessage("authorization-error", "error",
              errorMessage(outcome.body, "Void refused")); // R178: closed holds
            await refresh();
            return;
          }
          await refresh();
        });
      }
    });

    refresh();
  }

  return {
    parseDecimalAmount: parseDecimalAmount,
    formatAmount: formatAmount,
    createRefreshGuard: createRefreshGuard,
    sha256Hex: sha256Hex,
    deriveKey: deriveKey,
    fetchJSON: fetchJSON,
    splitShares: splitShares,
    readCookie: readCookie,
    sessionToken: sessionToken,
    setSession: setSession,
    clearSession: clearSession,
    apiFetch: apiFetch,
    showMessage: showMessage,
    removeMessage: removeMessage,
    classifyOutcome: classifyOutcome,
    renderWallet: renderWallet,
    renderFeed: renderFeed,
    initHome: initHome,
    renderRequests: renderRequests,
    initRequests: initRequests,
    renderSplitPreview: renderSplitPreview,
    parseHandles: parseHandles,
    initSplit: initSplit,
    renderAuthorizations: renderAuthorizations,
    initAuthorizations: initAuthorizations
  };
});
