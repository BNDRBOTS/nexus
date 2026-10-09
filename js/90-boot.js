/* NX boot: status pill, routing, shortcuts, startup. Must load last. */
(function () {
  'use strict';
  const NX = window.NX;
  const { $ } = NX.util;
  const S = NX.state;

  /* ---------- status pill (model reachability) ---------- */
  NX.statusPill = {
    set(pr) {
      const pill = $('#statusPill');
      const text = $('#statusText');
      const dot = pill.querySelector('.dot');
      pr = pr || {};
      let cls = 'bad';
      let label;
      if (pr.error && !pr.reachable) {
        label = pr.error;
      } else if (pr.reachable) {
        cls = pr.error ? 'warn' : 'ok';
        label = (pr.model || 'model') + (pr.latency_ms != null ? ' · ' + pr.latency_ms + ' ms' : '') + (pr.error ? ' · ' + pr.error : '');
      } else {
        label = 'checking';
        cls = 'warn';
      }
      dot.className = 'dot ' + cls;
      text.textContent = label.length > 40 ? label.slice(0, 40) + '…' : label;
      pill.title = (pr.route || '') + ' · ' + (pr.model || '') + (pr.error ? ' · ' + pr.error : '');
    },
    async refresh() {
      try {
        const r = await NX.api('GET', '/api/status');
        this.set(r.provider);
      } catch (e) {
        this.set({ reachable: false, error: e.message });
      }
    },
  };

  /* ---------- routing ---------- */
  async function route() {
    const m = location.hash.match(/^#chat=(\w+)/);
    if (m && m[1] !== S.chatId) {
      try { await NX.chat.open(m[1]); } catch (e) { NX.toast('Chat not found', 'err'); history.replaceState(null, '', '#'); }
    }
  }
  window.addEventListener('hashchange', route);

  /* ---------- shortcuts ---------- */
  document.addEventListener('keydown', (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#search').focus(); }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); NX.chat.newChat().catch((err) => NX.toast(err.message, 'err')); }
    if (e.key === 'Escape') {
      if (!$('#modal').classList.contains('hidden')) NX.modal.close();
      else if (S.panel) NX.panels.close();
    }
  });

  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') NX.modal.close(); });

  NX.bus.on('auth-required', () => {
    if (S.authPrompted) return;
    S.authPrompted = true;
    NX.toast('Access token required. Enter it in Settings → Data.', 'err');
    NX.settings.open('data');
  });

  /* ---------- boot ---------- */
  async function boot() {
    NX.chatUI.init();
    NX.panelsUI.init();
    NX.settingsUI.init();
    try {
      await NX.chat.refresh(false);
      await route();
    } catch (e) {
      NX.toast('Server unreachable: ' + e.message, 'err');
    }
    NX.statusPill.refresh();
    setInterval(() => NX.statusPill.refresh(), 180000);
    NX.bus.emit('ready');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
