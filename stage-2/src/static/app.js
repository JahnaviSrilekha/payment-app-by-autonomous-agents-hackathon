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
    var anchor = host.querySelector(".button, button");
    if (anchor) {
      host.insertBefore(el, anchor);
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
    balance.textContent = "Total " + formatAmount(me.total, me.minor_units, me.currency);
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
      held.textContent = formatAmount(me.held, me.minor_units, me.currency) + " held";
    } else if (held) {
      held.parentNode.remove();
    }
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

  return {
    parseDecimalAmount: parseDecimalAmount,
    formatAmount: formatAmount,
    createRefreshGuard: createRefreshGuard,
    sha256Hex: sha256Hex,
    deriveKey: deriveKey,
    fetchJSON: fetchJSON,
    readCookie: readCookie,
    sessionToken: sessionToken,
    setSession: setSession,
    clearSession: clearSession,
    apiFetch: apiFetch,
    showMessage: showMessage,
    removeMessage: removeMessage,
    classifyOutcome: classifyOutcome,
    initAuth: initAuth
  };
});
