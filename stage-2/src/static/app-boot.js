/* Per-screen wiring: each server-rendered page embeds a small config
 * (window.__PEBBLE_BOOT__) and this thin boot file dispatches to the shared
 * module's init function (design.md section 14: one shared JS module, screens
 * carry only their wiring). Deferred after app.js, so Pebble is defined. */
(function () {
  "use strict";
  var boot = window.__PEBBLE_BOOT__;
  if (!boot || typeof window.Pebble !== "object") {
    return;
  }
  var inits = {
    home: Pebble.initHome,
    auth: Pebble.initAuth,
    requests: Pebble.initRequests,
    split: Pebble.initSplit
  };
  var init = inits[boot.screen];
  if (typeof init === "function") {
    init(boot);
  }
})();