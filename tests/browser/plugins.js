// Settings → Plugins: create a plugin through the form; it appears in /api/state.
const assert = require('assert');
const { sleep, waitFor, api, openApp, noErrors } = require('./harness');

module.exports = {
  name: 'plugin editor (Settings → Plugins)',
  async run({ base, token }) {
    const call = api(base, token);
    const app = await openApp(base, token);
    const { w, d, errors } = app;
    try {
      w.NX.settings.open('plugins');
      const sec = () => d.querySelector('#sec');
      await waitFor(() => sec().querySelector('[data-f="url"]'));
      assert.ok(sec().querySelector('[data-f="parameters"]'), 'parameters field rendered');
      sec().querySelector('[data-f="name"]').value = 'Ping Plugin';
      sec().querySelector('[data-f="url"]').value = 'https://plugins.example.test/ping';
      sec().querySelector('[data-f="description"]').value = 'Ping a host';
      sec().querySelector('[data-do="save"]').click();
      await sleep(1500);
      const plugins = (await call('GET', '/api/state')).body.plugins;
      const p = plugins.find((x) => x.name === 'Ping Plugin');
      assert.ok(p, 'plugin saved');
      assert.strictEqual(p.url, 'https://plugins.example.test/ping');
      noErrors(errors);
    } finally { app.close(); }
  },
};
