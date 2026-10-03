"use strict";

/* --- minimal DOM shim --------------------------------------------------------- */

let cookieJar = "";

function fakeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [],
    parentNode: null,
    attrs: {},
    listeners: {},
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    className: "",
    disabled: false,
    classList: {
      add(c) { if (!el.className.includes(c)) el.className = (el.className + " " + c).trim(); },
      remove(c) { el.className = el.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
      contains(c) { return el.className.split(/\s+/).includes(c); },
    },
    addEventListener(type, fn) {
      el.listeners[type] = fn;
    },
    setAttribute(name, value) {
      el.attrs[name] = String(value);
      if (name === "data-testid") {
        document._byTestid[value] = el;
      }
    },
    getAttribute(name) {
      return el.attrs[name] === undefined ? null : el.attrs[name];
    },
    removeAttribute(name) {
      delete el.attrs[name];
    },
    appendChild(child) {
      child.parentNode = el;
      el.children.push(child);
      return child;
    },
    insertBefore(child, ref) {
      child.parentNode = el;
      if (ref) {
        const i = el.children.indexOf(ref);
        if (i >= 0) el.children.splice(i, 0, child);
        else el.children.push(child);
      } else {
        el.children.push(child);
      }
      return child;
    },
    remove() {
      const unregister = (node) => {
        const tid = node.attrs["data-testid"];
        if (tid && document._byTestid[tid] === node) {
          delete document._byTestid[tid];
        }
        node.children.forEach(unregister);
      };
      unregister(el);
      if (el.parentNode) {
        const i = el.parentNode.children.indexOf(el);
        if (i >= 0) el.parentNode.children.splice(i, 1);
        el.parentNode = null;
      }
    },
    closest(sel) {
      if (sel === "button" && el.tagName === "BUTTON") return el;
      return null;
    },
    querySelector(sel) {
      if (sel === ".button, button" || sel === "button") {
        return el.children.find((c) => c.tagName === "BUTTON"
          || (c.className || "").includes("button")) || null;
      }
      if (sel.startsWith("#")) {
        return document._ids[sel.slice(1)] || null;
      }
      const m = sel.match(/^\[data-testid="(.+)"\]$/);
      if (m) {
        return document._byTestid[m[1]] || null;
      }
      return null;
    },
    get firstChild() {
      return el.children[0] || null;
    },
    removeChild(c) {
      const i = el.children.indexOf(c);
      if (i >= 0) el.children.splice(i, 1);
      c.parentNode = null;
      return c;
    },
  };
  return el;
}

const document = {
  _ids: {},
  _byTestid: {},
  cookie: "",
  getElementById(id) {
    return document._ids[id] || document._byTestid[id] || null;
  },
  querySelector(sel) {
    if (sel === "main") return document._ids["main"] || null;
    const m = sel.match(/^\[data-testid="(.+)"\]$/);
    if (m) return document._byTestid[m[1]] || null;
    if (sel.startsWith("#")) return document._ids[sel.slice(1)] || null;
    return null;
  },
  createElement(tag) {
    return fakeEl(tag);
  },
};
globalThis.document = document;

function registerPage(dom) {
  // dom: {main, ids: {name: el}} — testid lookups keep working through the shim's
  // auto-registration on setAttribute.
  document._ids = Object.assign({ main: dom.main }, dom.ids);
  for (const [name, el] of Object.entries(dom.ids)) {
    el.attrs = el.attrs || {};
    el.attrs.id = name;
  }
}

const fetchCalls = [];
let fetchImpl = async () => ({ status: 200, text: async () => "{}" });
globalThis.fetch = async (url, init) => {
  fetchCalls.push({ url, init });
  return fetchImpl(url, init);
};
globalThis.window = { location: { href: "" } };

const byTestid = (t) => document._byTestid[t];
const findFetch = (url) => {
  for (let i = fetchCalls.length - 1; i >= 0; i--) {
    if (fetchCalls[i].url === url || fetchCalls[i].url.startsWith(url)) {
      return fetchCalls[i];
    }
  }
  return null;
};
const lastFetch = () => fetchCalls[fetchCalls.length - 1];

const clearFetchCalls = () => {
  fetchCalls.length = 0;
};


globalThis.fetch = async (url, init) => {
  fetchCalls.push({ url, init });
  return fetchImpl(url, init);
};

function setFetchImpl(fn) {
  fetchImpl = fn;
}


const BOOT = {
  screen: "home", signed_in: true, handle: "ada", minor_units: 2, currency: "EUR",
};

function homePage() {
  const main = fakeEl("main");
  const ids = {};
  const mk = (testid, tag) => {
    const el = fakeEl(tag || "input");
    el.setAttribute("data-testid", testid);
    return el;
  };
  const available = mk("wallet-available", "p");
  const balance = mk("wallet-balance", "p");
  balance.parentNode = main; main.children.push(balance);
  available.parentNode = balance; // renderWallet walks balance.nextSibling
  const refresh = mk("wallet-refresh", "button");
  const payForm = fakeEl("form"); payForm.setAttribute("id", "pay-form");
  ids["pay-form"] = payForm;
  const paySubmit = mk("pay-submit", "button");
  payForm.children.push(paySubmit); paySubmit.parentNode = payForm;
  mk("pay-handle"); mk("pay-amount").value = "15.00"; mk("pay-note"); mk("pay-visibility").value = "public";
  const requestForm = fakeEl("form"); requestForm.setAttribute("id", "request-form");
  ids["request-form"] = requestForm;
  const requestSubmit = mk("request-submit", "button");
  requestForm.children.push(requestSubmit); requestSubmit.parentNode = requestForm;
  mk("request-handle"); mk("request-amount").value = "10.00"; mk("request-note");
  const activityList = mk("activity-list", "ul");
  activityList.parentNode = main; main.children.push(activityList);
  registerPage({ main, ids });
  return { available, balance, refresh, paySubmit, requestSubmit, main, activityList };
}


module.exports = { fakeEl, document, registerPage, fetchCalls, setFetchImpl,
  clearFetchCalls, byTestid, findFetch, lastFetch, BOOT, homePage };
module.exports.BOOT = BOOT;
module.exports.homePage = homePage;
