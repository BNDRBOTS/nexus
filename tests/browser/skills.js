// Skill maker in Settings → Skills: Draft fills the new-skill form; nothing is saved until Save.
const assert = require('assert');
const { sleep, waitFor, api, openApp, noErrors } = require('./harness');

module.exports = {
  name: 'skill draft (Settings → Skills)',
  async run({ base, token }) {
    const call = api(base, token);
    const before = (await call('GET', '/api/state')).body.skills.length;
    const app = await openApp(base, token);
    const { w, d, errors } = app;
    try {
      w.NX.settings.open('skills');
      const sec = () => d.querySelector('#sec');
      await waitFor(() => sec().querySelector('[data-draft-desc]'));
      const setDesc = (v) => { sec().querySelector('[data-draft-desc]').value = v; };

      setDesc('  ');                                   // empty description refused client-side
      sec().querySelector('[data-do="draft"]').click();
      await waitFor(() => /Describe the skill first/.test(d.body.textContent));

      setDesc('SKILLBAD: something');                   // non-JSON model output: error toast, editor untouched
      sec().querySelector('[data-do="draft"]').click();
      await waitFor(() => /did not return a JSON object/.test(d.body.textContent));

      setDesc('SKILLDRAFT: review contracts');          // good draft: fields filled for review
      sec().querySelector('[data-do="draft"]').click();
      await waitFor(() => sec().querySelector('[data-f="name"]') &&
        sec().querySelector('[data-f="name"]').value === 'Contract Reviewer');
      assert.ok(sec().querySelector('[data-f="instructions"]').value.startsWith('Check every clause'));
      assert.ok(/New skill/.test(sec().querySelector('h3').textContent), 'new-skill mode');
      assert.strictEqual((await call('GET', '/api/state')).body.skills.length, before, 'not saved yet');

      sec().querySelector('[data-do="save"]').click();  // Save persists it
      await sleep(1500);
      const after = (await call('GET', '/api/state')).body.skills;
      assert.strictEqual(after.length, before + 1, 'saved after review');
      assert.ok(after.some((k) => k.name === 'Contract Reviewer'));
      noErrors(errors);
    } finally { app.close(); }
  },
};
