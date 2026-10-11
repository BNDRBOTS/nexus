/* NX core: single global namespace, API client, markdown, theme, dialogs.
   Load order: 00-core, 05-knacks, 10-chat, 20-panels, 30-settings, 90-boot. Plain scripts, no build step. */
(function () {
  'use strict';
  const NX = (window.NX = window.NX || {});

  NX.state = {
    version: '', modes: {}, toolGroups: [], inbox: 'inbox', settings: null,
    projects: [], chats: [], roles: [], skills: [], workflows: [], mcp: [],
    pythonEnabled: false, tokenRequired: false,
    projectId: null,      // selected project in sidebar (new chats land here)
    chatId: null,         // open chat
    chat: null,           // full chat doc
    draft: { mode: 'work', role_id: null, tool_groups: null, attachments: [] },
    panel: null,          // open panel tab id or null
    busy: false,
    live: null,           // streaming assistant state
    ctx: null,            // last context snapshot from SSE
    search: '',
  };

  /* ---------- util ---------- */
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const fmtTime = (ts) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    return d.toDateString() === now.toDateString()
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };
  const slug = (s) => String(s || 'file').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'file';
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const byId = (list, id) => (list || []).find((x) => x.id === id);

  NX.util = { esc, $, $$, fmtTime, slug, clone, sleep, byId };

  /* ---------- events ---------- */
  const handlers = {};
  NX.bus = {
    on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); },
    emit(ev, data) { (handlers[ev] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
  };

  /* ---------- toasts & dialogs ---------- */
  NX.toast = function (msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), kind === 'err' ? 6000 : 3200);
  };

  NX.modal = {
    open(html, opts) {
      const card = $('#modalCard');
      card.className = 'modal-card' + (opts && opts.small ? ' small' : '');
      card.innerHTML = html;
      $('#modal').classList.remove('hidden');
      return card;
    },
    close() { $('#modal').classList.add('hidden'); $('#modalCard').innerHTML = ''; },
  };

  NX.ask = function (title, value, opts) {
    return new Promise((resolve) => {
      const multi = opts && opts.multiline;
      NX.modal.open(`
        <div class="modal-h"><h2>${esc(title)}</h2></div>
        <div class="modal-b">
          ${multi ? `<textarea id="askV" class="textarea" style="min-height:140px">${esc(value || '')}</textarea>`
                  : `<input id="askV" class="input" value="${esc(value || '')}" autocomplete="off">`}
          ${opts && opts.hint ? `<div class="muted">${esc(opts.hint)}</div>` : ''}
        </div>
        <div class="modal-f"><button class="btn ghost" data-x="no">Cancel</button><button class="btn primary" data-x="ok">OK</button></div>`,
      { small: !multi });
      const input = $('#askV');
      input.focus();
      input.select && input.select();
      const done = (v) => { NX.modal.close(); resolve(v); };
      $('#modalCard').onclick = (e) => {
        const x = e.target.dataset.x;
        if (x === 'ok') done(input.value);
        if (x === 'no') done(null);
      };
      input.onkeydown = (e) => { if (e.key === 'Enter' && !multi) done(input.value); if (e.key === 'Escape') done(null); };
    });
  };

  NX.confirm = function (title, text) {
    return new Promise((resolve) => {
      NX.modal.open(`
        <div class="modal-h"><h2>${esc(title)}</h2></div>
        <div class="modal-b"><div>${esc(text)}</div></div>
        <div class="modal-f"><button class="btn ghost" data-x="no">Cancel</button><button class="btn danger" data-x="ok">Confirm</button></div>`,
      { small: true });
      $('#modalCard').onclick = (e) => {
        const x = e.target.dataset.x;
        if (x) { NX.modal.close(); resolve(x === 'ok'); }
      };
    });
  };

  /* ---------- API client ---------- */
  NX.auth = {
    token() { return localStorage.getItem('nx.token') || ''; },
    providerKey() { return localStorage.getItem('nx.providerKey') || ''; },
  };

  function headersFor(path, extra) {
    const h = Object.assign({ 'Content-Type': 'application/json' }, extra || {});
    const tok = NX.auth.token();
    if (tok) h.Authorization = 'Bearer ' + tok;
    if (/^\/api\/(chats|status)/.test(path) && NX.auth.providerKey()) h['X-NX-Provider-Key'] = NX.auth.providerKey();
    return h;
  }

  function apiError(res, data) {
    const err = new Error((data && data.error) || res.statusText || ('HTTP ' + res.status));
    err.status = res.status;
    if (res.status === 401) NX.bus.emit('auth-required');
    return err;
  }

  NX.api = async function (method, path, body) {
    const res = await fetch(path, { method, headers: headersFor(path),
      body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (!res.ok) throw apiError(res, data);
    return data;
  };

  /* POST that streams SSE. onEvent(name, data). Resolves when stream closes. */
  NX.stream = async function (path, body, onEvent) {
    const res = await fetch(path, { method: 'POST', headers: headersFor(path, { Accept: 'text/event-stream' }),
      body: JSON.stringify(Object.assign({}, body, { stream: true })) });
    if (!res.ok) {
      const text = await res.text();
      let data = null;
      try { data = JSON.parse(text); } catch (e) { data = text; }
      throw apiError(res, data);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let ev = 'message';
        let data = '';
        block.split('\n').forEach((line) => {
          if (line.startsWith('event:')) ev = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        });
        if (data) onEvent(ev, JSON.parse(data));
      }
    }
  };

  /* ---------- markdown (escape-first, no raw HTML passthrough) ---------- */
  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?]|$)/g, '$1<em>$2</em>');
    s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => '<code>' + codes[+i] + '</code>');
    return s;
  }

  function blocks(text) {
    let html = '', list = null, para = [];
    const flush = () => { if (para.length) { html += '<p>' + inline(para.join('<br>')) + '</p>'; para = []; } };
    const close = () => { if (list) { html += '</' + list + '>'; list = null; } };
    for (const raw of text.split('\n')) {
      const line = raw.replace(/\s+$/, '');
      let m;
      if (!line.trim()) { flush(); close(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); close(); const lv = m[1].length + 1; html += `<h${lv}>${inline(m[2])}</h${lv}>`; continue; }
      if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flush(); close(); html += '<hr>'; continue; }
      if ((m = line.match(/^&gt;\s?(.*)$/))) { flush(); close(); html += `<blockquote>${inline(m[1])}</blockquote>`; continue; }
      if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { flush(); if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flush(); if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; continue; }
      close();
      para.push(line);
    }
    flush(); close();
    return html;
  }

  NX.md = function (src) {
    const parts = String(src || '').split('```');
    let out = '';
    parts.forEach((p, i) => {
      if (i % 2) {
        const nl = p.indexOf('\n');
        let lang = '', code = p;
        if (nl >= 0 && !p.slice(0, nl).includes(' ')) { lang = p.slice(0, nl).trim(); code = p.slice(nl + 1); }
        code = code.replace(/\n$/, '');
        out += `<div class="code"><div class="code-h"><span>${esc(lang || 'text')}</span><button class="mini" data-copy>Copy</button></div><pre><code>${esc(code)}</code></pre></div>`;
      } else {
        out += blocks(esc(p));
      }
    });
    return out;
  };

  /* ---------- theme (settings.theme -> CSS variables) ---------- */
  NX.presets = {
    graphite: { accent: '#f97316', bg: '#0b0d10', surface: '#14171c', text: '#e7e9ee', radius: 12, light: false },
    ocean: { accent: '#22d3ee', bg: '#06101a', surface: '#0c1a27', text: '#e6f1f7', radius: 14, light: false },
    ember: { accent: '#ef4444', bg: '#120a0a', surface: '#1c1210', text: '#f5e9e6', radius: 10, light: false },
    paper: { accent: '#2563eb', bg: '#f6f6f3', surface: '#ffffff', text: '#1b1d22', radius: 12, light: true },
  };

  function rgb(hex) {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) {
    const A = rgb(a), B = rgb(b);
    return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, '0')).join('');
  }
  function isLight(hex) {
    const [r, g, b] = rgb(hex);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150;
  }

  NX.theme = {
    apply(t) {
      if (!t) return;
      const root = document.documentElement;
      const light = isLight(t.bg);
      const surfaceIsDark = !isLight(t.surface);
      root.toggleAttribute('data-light', light);
      root.style.setProperty('--accent', t.accent);
      root.style.setProperty('--accent-ink', isLight(t.accent) ? '#111' : '#120700');
      root.style.setProperty('--bg', t.bg);
      root.style.setProperty('--surface', t.surface);
      root.style.setProperty('--surface-2', mix(t.surface, light ? '#000' : '#fff', 0.05));
      root.style.setProperty('--surface-3', mix(t.surface, light ? '#000' : '#fff', 0.10));
      root.style.setProperty('--text', t.text);
      root.style.setProperty('--muted', mix(t.text, t.bg, 0.45));
      root.style.setProperty('--line', light || !surfaceIsDark ? 'rgba(0,0,0,.09)' : 'rgba(255,255,255,.08)');
      root.style.setProperty('--line-2', light ? 'rgba(0,0,0,.16)' : 'rgba(255,255,255,.14)');
      root.style.setProperty('--radius', (t.radius || 12) + 'px');
      root.style.setProperty('--font-scale', String(t.font_scale || 1));
      root.style.colorScheme = light ? 'light' : 'dark';
    },
  };

  NX.escAttr = esc;
})();
