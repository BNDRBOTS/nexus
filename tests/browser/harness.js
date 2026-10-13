// Shared jsdom helpers for tests/browser. Loads the real UI (index.html + js/*) from a running server.
const { JSDOM, VirtualConsole } = require('jsdom');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, ms = 10000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const v = fn(); if (v) return v; } catch (e) { /* retry until timeout */ }
    await sleep(100);
  }
  throw new Error('timeout waiting for condition');
}

// api(base, token)(method, path, body) -> {status, body}
function api(base, token) {
  return async (method, path, body) => {
    const r = await fetch(base + path, {
      method,
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { /* non-JSON body */ }
    return { status: r.status, body: json };
  };
}

// Opens the app as the owner UI with the given token stored in localStorage.
async function openApp(base, token) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.message || e)));
  const dom = await JSDOM.fromURL(base + '/', {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.localStorage.setItem('nx.token', token);
      w.TextDecoder = TextDecoder; w.TextEncoder = TextEncoder;
      w.fetch = (u, o) => fetch(new URL(u, base), o);
      w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {} });
      w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window;
  w.addEventListener('error', (ev) => errors.push('error: ' + ev.message));
  await waitFor(() => w.NX && w.NX.settings && w.NX.chat && w.NX.panels);
  await sleep(300);
  return { w, d: w.document, errors, close: () => dom.window.close() };
}

function noErrors(errors) {
  if (errors.length) throw new Error('page errors: ' + errors.join(' | '));
}

module.exports = { sleep, waitFor, api, openApp, noErrors };
