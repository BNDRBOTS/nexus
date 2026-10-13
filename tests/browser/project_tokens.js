// Project panel → Access tokens: create (shown once), authenticates, revoke from the panel, then rejected.
const assert = require('assert');
const { sleep, waitFor, api, openApp, noErrors } = require('./harness');

module.exports = {
  name: 'project token panel (Project → Access tokens)',
  async run({ base, token }) {
    const call = api(base, token);
    const pr = (await call('POST', '/api/doc/project', { name: 'UI Token Project' })).body;
    const pid = pr.id;
    const app = await openApp(base, token);
    const { w, d, errors } = app;
    try {
      w.NX.chat.selectProject(pid);
      await sleep(300);
      await w.NX.chat.refresh(true);
      w.NX.panels.open('project');
      await waitFor(() => d.querySelector('#pTokNew'));
      d.querySelector('#pTokName').value = 'ui-token';
      d.querySelector('#pTokNew').click();
      const shownEl = await waitFor(() => [...d.querySelectorAll('#panel .mono')]
        .find((x) => x.textContent.startsWith('nxp_')));
      const tok = shownEl.textContent.trim();
      const list = (await call('GET', `/api/projects/${pid}/tokens`)).body;
      assert.strictEqual(list.tokens.length, 1);
      assert.strictEqual(list.tokens[0].name, 'ui-token');
      assert.ok(!JSON.stringify(list).includes(tok), 'listing never returns the token');
      assert.strictEqual((await api(base, tok)('GET', `/api/projects/${pid}`)).status, 200, 'token authenticates');

      await waitFor(() => d.querySelector('[data-revoke]'));
      d.querySelector('[data-revoke]').click();
      await waitFor(() => d.querySelector('#modalCard [data-x="ok"]'));
      d.querySelector('#modalCard [data-x="ok"]').click();
      await sleep(1500);
      assert.strictEqual((await call('GET', `/api/projects/${pid}/tokens`)).body.tokens.length, 0, 'revoked in UI');
      assert.strictEqual((await api(base, tok)('GET', `/api/projects/${pid}`)).status, 401, 'revoked token rejected');
      noErrors(errors);
    } finally { app.close(); }
  },
};
