// Project panel → Source Scope: tick/untick files, Save, then the server record and the chat's context snapshot match.
const assert = require('assert');
const { sleep, waitFor, api, openApp, noErrors } = require('./harness');

module.exports = {
  name: 'project scope panel (Project → Source Scope)',
  async run({ base, token }) {
    const call = api(base, token);
    const pid = (await call('POST', '/api/doc/project', { name: 'UI Scope Project' })).body.id;
    await call('PUT', '/api/files', { project: pid, path: 'notes/a.md', content: 'alpha facts' });
    await call('PUT', '/api/files', { project: pid, path: 'notes/b.md', content: 'beta facts' });
    const cid = (await call('POST', '/api/chats', { project_id: pid, title: 'scope chat' })).body.id;

    const app = await openApp(base, token);
    const { w, d, errors } = app;
    const checked = () => [...d.querySelectorAll('#panel [data-scope]')]
      .filter((c) => c.checked).map((c) => c.dataset.scope).sort();
    const box = (path) => waitFor(() => d.querySelector(`#panel [data-scope="${path}"]`));
    const save = async () => {
      d.querySelector('#pSave').click();
      await waitFor(() => d.querySelector('#panel [data-scope]'));
      await sleep(800);
    };
    const serverScope = async () => (await call('GET', `/api/projects/${pid}`)).body.project.scope;
    try {
      w.NX.chat.selectProject(pid);
      await sleep(300);
      await w.NX.chat.refresh(true);
      w.NX.panels.open('project');
      await box('notes/a.md');
      assert.deepStrictEqual(checked(), [], 'no scope by default');

      (await box('notes/a.md')).click();
      await save();
      assert.deepStrictEqual(await serverScope(), ['notes/a.md'], 'a.md saved to project record');
      assert.deepStrictEqual((await call('GET', `/api/chats/${cid}/context`)).body.snapshot.scope,
        ['notes/a.md'], 'context snapshot uses saved scope');

      (await box('notes/b.md')).click();
      await save();
      assert.deepStrictEqual((await serverScope()).slice().sort(), ['notes/a.md', 'notes/b.md'], 'both saved');

      (await box('notes/a.md')).click();
      await save();
      assert.deepStrictEqual(await serverScope(), ['notes/b.md'], 'untick removes from scope');
      assert.deepStrictEqual((await call('GET', `/api/chats/${cid}/context`)).body.snapshot.scope,
        ['notes/b.md'], 'snapshot follows the untick');

      noErrors(errors);
    } finally { app.close(); }
  },
};
