/* NX panels: Canvas (workspace), Project (instructions/scope/status/workflows), Context, Record, Status. */
(function () {
  'use strict';
  const NX = window.NX;
  const { esc, $, fmtTime, byId } = NX.util;
  const S = NX.state;

  const TABS = [['canvas', 'Canvas'], ['project', 'Project'], ['context', 'Context'], ['record', 'Record'], ['status', 'Status']];
  const C = { path: null, content: '', files: [], run: '', recordFilter: 'chat', ctx: null, status: null };

  function projectId() {
    return (S.chat && S.chat.project_id) || S.projectId || S.inbox;
  }

  function open(tab) {
    if (S.panel === tab) return close();
    S.panel = tab;
    $('#panel').classList.remove('hidden');
    document.getElementById('app').classList.add('panel-open');
    render();
    NX.chat.refresh(false).catch(() => {});
  }

  function close() {
    S.panel = null;
    $('#panel').classList.add('hidden');
    document.getElementById('app').classList.remove('panel-open');
    NX.chat && NX.chat.refresh && NX.bus.emit('state');
    const t = $('#panelToggles');
    if (t) t.querySelectorAll('button').forEach((b) => b.classList.remove('on'));
  }

  async function render() {
    if (!S.panel) return;
    $('#tabs').innerHTML = TABS.map(([id, label]) =>
      `<button class="tab ${S.panel === id ? 'on' : ''}" data-tab="${id}" role="tab">${label}</button>`).join('');
    const body = $('#panelBody');
    try {
      if (S.panel === 'canvas') await renderCanvas(body);
      else if (S.panel === 'project') await renderProject(body);
      else if (S.panel === 'context') await renderContext(body);
      else if (S.panel === 'record') await renderRecord(body);
      else if (S.panel === 'status') await renderStatus(body);
    } catch (e) {
      body.innerHTML = `<div class="card">${esc(e.message)}</div>`;
    }
    const t = $('#panelToggles');
    if (t) t.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.panel === S.panel));
  }

  /* ---------- Canvas: workspace files + editor ---------- */
  async function loadFiles() {
    C.files = (await NX.api('GET', `/api/files?project=${encodeURIComponent(projectId())}`)).files;
  }

  async function renderCanvas(body) {
    await loadFiles();
    const p = NX.chat.currentProject();
    const scope = (p && p.scope) || [];
    const file = C.path ? await NX.api('GET', `/api/files?project=${encodeURIComponent(projectId())}&path=${encodeURIComponent(C.path)}`).catch(() => null) : null;
    if (file && C.content === '' && !C.dirty) C.content = file.content;
    const isHtml = C.path && /\.html?$/i.test(C.path);
    const isMd = C.path && /\.md$/i.test(C.path);
    const isPy = C.path && /\.py$/i.test(C.path);
    body.innerHTML = `
      <div class="row"><h3 style="flex:1">Workspace &middot; ${esc(p ? p.name : '')}</h3>
        <button class="mini" data-c="new">New file</button><button class="mini" data-c="upload">Upload</button>
        <input type="file" id="upl" class="hidden" accept=".txt,.md,.py,.js,.ts,.json,.csv,.html,.css,.yaml,.yml,.xml,.sql,.sh,.go,.rs,.java,.c,.cpp,.h,.rb,.php,.tsx,.jsx"></div>
      <div>${C.files.length ? C.files.map((f) => `
        <div class="file-row ${C.path === f.path ? 'on' : ''}" data-path="${esc(f.path)}">
          <span class="n">${esc(f.path)}</span>${scope.includes(f.path) ? '<span class="tool-chip">scope</span>' : ''}
          <span class="s">${f.bytes}</span></div>`).join('') : '<div class="muted">Empty workspace. Save a reply, create a file, or upload one.</div>'}</div>
      ${C.path && file ? `
        <h3>${esc(C.path)}</h3>
        <textarea id="ed" class="textarea" style="min-height:260px">${esc(C.content)}</textarea>
        <div class="row">
          <button class="btn primary" data-c="save">Save</button>
          <button class="btn" data-c="attach">Attach to chat</button>
          ${isPy && S.pythonEnabled ? '<button class="btn" data-c="run">Run Python</button>' : ''}
          <span class="spacer"></span><button class="btn ghost danger" data-c="delete">Delete</button>
        </div>
        ${isHtml ? `<iframe class="preview" sandbox="allow-scripts" srcdoc="${esc(C.content)}" style="height:280px;width:100%"></iframe>` : ''}
        ${isMd ? `<div class="card bubble">${NX.md(C.content)}</div>` : ''}
        ${C.run ? `<pre class="pre">${esc(C.run)}</pre>` : ''}` : ''}`;
    const ed = $('#ed', body);
    if (ed) ed.oninput = () => { C.content = ed.value; C.dirty = true; };
    body.onclick = (e) => onCanvasClick(e, body);
    $('#upl', body).onchange = onUpload;
  }

  async function onCanvasClick(e, body) {
    const row = e.target.closest('[data-path]');
    if (row) {
      C.path = row.dataset.path; C.content = ''; C.dirty = false; C.run = '';
      return render();
    }
    const b = e.target.closest('[data-c]');
    if (!b) return;
    const pid = projectId();
    const act = b.dataset.c;
    try {
      if (act === 'new') {
        const path = await NX.ask('New workspace file', 'notes/untitled.md');
        if (!path) return;
        await NX.api('PUT', '/api/files', { project: pid, path, content: '' });
        C.path = path; C.content = ''; C.dirty = false;
        NX.bus.emit('file-changed');
        return render();
      }
      if (act === 'upload') return $('#upl').click();
      if (act === 'save') {
        await NX.api('PUT', '/api/files', { project: pid, path: C.path, content: C.content });
        C.dirty = false;
        NX.toast('Saved ' + C.path, 'ok');
        NX.bus.emit('file-changed');
        return render();
      }
      if (act === 'attach') {
        S.attachments = (S.attachments || []).concat([{ path: C.path, content: C.content.slice(0, 20000) }]);
        NX.toast('Attached ' + C.path + ' to the next message', 'ok');
        return NX.chat.refresh(false);
      }
      if (act === 'run') {
        const r = await NX.api('POST', '/api/python', { project_id: pid, code: C.content });
        C.run = r.output;
        NX.toast(r.ok ? 'Python finished' : 'Python exited with error', r.ok ? 'ok' : 'err');
        NX.bus.emit('file-changed');
        return render();
      }
      if (act === 'delete') {
        if (!(await NX.confirm('Delete file', C.path + ' will be removed from the workspace. The record keeps the deletion.'))) return;
        await NX.api('DELETE', `/api/files?project=${encodeURIComponent(pid)}&path=${encodeURIComponent(C.path)}`);
        C.path = null; C.content = ''; C.run = '';
        NX.bus.emit('file-changed');
        return render();
      }
    } catch (err) {
      NX.toast(err.message, 'err');
    }
  }

  function onUpload(e) {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const path = f.name.replace(/[^A-Za-z0-9._\- ]/g, '_');
      try {
        await NX.api('PUT', '/api/files', { project: projectId(), path, content: String(reader.result) });
        C.path = path; C.content = String(reader.result); C.dirty = false;
        NX.toast('Uploaded ' + path, 'ok');
        NX.bus.emit('file-changed');
        render();
      } catch (err) { NX.toast(err.message, 'err'); }
    };
    reader.readAsText(f);
    e.target.value = '';
  }

  /* ---------- Project: instructions, scope, status, workflows ---------- */
  async function renderProject(body) {
    const p = NX.chat.currentProject();
    if (!p) { body.innerHTML = '<div class="muted">No project selected.</div>'; return; }
    await loadFiles();
    const detail = await NX.api('GET', '/api/projects/' + p.id);
    const st = p.status || {};
    const scope = p.scope || [];
    const wfs = S.workflows.filter((w) => !w.project_id || w.project_id === p.id);
    body.innerHTML = `
      <h3>Project</h3>
      <div class="field"><label>Name</label><input id="pName" class="input" value="${esc(p.name)}"></div>
      <div class="field"><label>Instructions (injected into every turn)</label>
        <textarea id="pInstr" class="textarea" style="min-height:120px;font-family:inherit">${esc(p.instructions || '')}</textarea></div>
      <h3>Status</h3>
      <div class="card kv">
        <div>State</div><div><select id="pState" class="select">${['active', 'paused', 'done'].map((s) =>
          `<option ${st.state === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
        <div>Verified note</div><div>${esc(st.note || '—')}</div>
        <div>Next step</div><div>${esc(st.next_step || '—')}</div>
        <div>Updated</div><div>${fmtTime(st.updated)}</div>
      </div>
      <div class="field"><label>Set progress (manual, recorded)</label>
        <input id="pNote" class="input" placeholder="verified note"><input id="pNext" class="input" placeholder="next step"></div>
      <h3>Source Scope</h3>
      <div class="muted">Only checked files can be used as sources by this project's chats.</div>
      <div>${C.files.length ? C.files.map((f) => `<label class="check"><input type="checkbox" data-scope="${esc(f.path)}" ${scope.includes(f.path) ? 'checked' : ''}> ${esc(f.path)}</label>`).join('') : '<div class="muted">No workspace files yet.</div>'}</div>
      <div class="row"><button class="btn primary" id="pSave">Save project</button>
        ${p.id !== S.inbox ? '<button class="btn ghost danger" id="pDel">Delete project</button>' : ''}
        <span class="spacer"></span><button class="btn" id="pResume" ${st.next_step ? '' : 'disabled'}>Continue next step</button></div>
      <h3>Workflows</h3>
      <div>${wfs.length ? wfs.map((w) => `<div class="card row"><span style="flex:1">${esc(w.name)} <span class="muted">${(w.steps || []).length} steps</span></span>
        <button class="mini" data-wf="${esc(w.id)}">Run</button></div>`).join('') : '<div class="muted">No workflows. Create one in Settings &rarr; Workflows.</div>'}</div>
      <h3>Chats</h3>
      <div>${detail.chats.length ? detail.chats.map((c) => `<button class="nav-item" data-chat="${esc(c.id)}"><span class="t">${esc(c.title)}</span><span class="sub">${c.messages} msgs</span></button>`).join('') : '<div class="muted">No chats in this project.</div>'}</div>
      <h3>Recent record</h3>
      <div>${detail.actions.slice(0, 12).map(actionHtml).join('') || '<div class="muted">No actions yet.</div>'}</div>`;

    $('#pSave', body).onclick = async () => {
      const scopeNext = [...body.querySelectorAll('[data-scope]')].filter((c) => c.checked).map((c) => c.dataset.scope);
      const status = { state: $('#pState', body).value, note: $('#pNote', body).value.trim() || st.note || '',
        next_step: $('#pNext', body).value.trim() || st.next_step || '' };
      const nextDoc = Object.assign({}, p, { name: $('#pName', body).value.trim() || p.name, instructions: $('#pInstr', body).value,
        scope: scopeNext, status });
      try {
        await NX.api('PUT', '/api/doc/project/' + p.id, nextDoc);
        NX.toast('Project saved', 'ok');
        await NX.chat.refresh(false);
        render();
      } catch (e) { NX.toast(e.message, 'err'); }
    };
    const del = $('#pDel', body);
    if (del) del.onclick = async () => {
      if (!(await NX.confirm('Delete project', 'Its chats are kept and moved to Inbox. Workspace files are deleted.'))) return;
      for (const c of detail.chats) await NX.api('PATCH', '/api/chats/' + c.id, { project_id: S.inbox });
      for (const f of detail.files) await NX.api('DELETE', `/api/files?project=${encodeURIComponent(p.id)}&path=${encodeURIComponent(f.path)}`);
      await NX.api('DELETE', `/api/doc/project/${p.id}`);
      S.projectId = null;
      NX.toast('Project deleted', 'ok');
      await NX.chat.refresh(false);
      render();
    };
    $('#pResume', body).onclick = () => {
      $('#input').value = 'Continue from the last verified state. Next step: ' + (st.next_step || '');
      NX.chat.send();
    };
    body.onclick = async (e) => {
      const c = e.target.closest('[data-chat]');
      if (c) { await NX.chat.open(c.dataset.chat); return render(); }
      const w = e.target.closest('[data-wf]');
      if (w) {
        const input = await NX.ask('Workflow input', '', { hint: 'Substituted for {{input}} in each step.' });
        if (input === null) return;
        NX.watchRun(await NX.api('POST', `/api/workflows/${w.dataset.wf}/run`, { input, project_id: p.id }));
      }
    };
  }

  /* ---------- Context: exact payload snapshot ---------- */
  async function renderContext(body) {
    if (!S.chat) { body.innerHTML = '<div class="muted">Open a chat to inspect the context it sends.</div>'; return; }
    const { messages, snapshot } = await NX.api('GET', `/api/chats/${S.chat.id}/context`);
    C.ctx = snapshot;
    body.innerHTML = `
      <h3>Snapshot</h3>
      <div class="card kv">
        <div>Hash</div><div>${esc(snapshot.hash)}</div>
        <div>Size</div><div>${snapshot.chars.toLocaleString()} chars &middot; ~${snapshot.tokens_est.toLocaleString()} tokens (budget ${snapshot.budget_chars.toLocaleString()})</div>
        <div>History</div><div>${snapshot.history.included} included &middot; ${snapshot.history.dropped} dropped</div>
        <div>Reminder</div><div>${snapshot.reminder ? 'yes' : 'no'}</div>
        <div>Scope</div><div>${snapshot.scope.length ? esc(snapshot.scope.join(', ')) : '—'}</div>
        <div>Retrieved</div><div>${snapshot.retrieved.length ? esc(snapshot.retrieved.join(', ')) : '—'}</div>
        <div>Tools</div><div>${snapshot.tools.length ? esc(snapshot.tools.join(', ')) : 'none'}</div>
      </div>
      <h3>Blocks</h3>
      <div class="card">${snapshot.blocks.map((b) => `<div class="row"><span style="flex:1">${esc(b.tag)}</span><span class="muted">${b.chars}</span></div>`).join('')}</div>
      <h3>Messages (exact order sent)</h3>
      ${messages.map((m, i) => `<details class="card"><summary><b>${i + 1}. ${esc(m.role)}</b> <span class="muted">${m.content.length} chars</span></summary>
        <pre class="pre">${esc(m.content)}</pre></details>`).join('')}`;
  }

  /* ---------- Record: append-only action log ---------- */
  function actionHtml(a) {
    const p = a.payload || {};
    const summary = Object.entries(p).filter(([k]) => k !== 'content').slice(0, 4)
      .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
    return `<div class="rec"><div class="h">#${a.id} &middot; ${fmtTime(a.ts)} &middot; ${esc(a.type)}${a.ref ? ' &middot; ' + esc(a.ref) : ''}</div>
      <div class="b">${esc(summary).slice(0, 320)}</div></div>`;
  }

  async function renderRecord(body) {
    const f = C.recordFilter;
    const q = f === 'chat' && S.chat ? `chat=${S.chat.id}` : f === 'project' ? `project=${projectId()}` : 'limit=200';
    const { actions } = await NX.api('GET', `/api/actions?${q}&limit=200`);
    body.innerHTML = `
      <div class="row"><h3 style="flex:1">Action record</h3>
        <select id="recF" class="select"><option value="chat">This chat</option><option value="project">This project</option><option value="all">All</option></select></div>
      <div class="muted">Append-only. Written only from saved messages, executed tools, file writes, status changes, and runs.</div>
      ${actions.map(actionHtml).join('') || '<div class="muted">No records yet.</div>'}`;
    const sel = $('#recF', body);
    sel.value = f;
    sel.onchange = () => { C.recordFilter = sel.value; render(); };
  }

  /* ---------- Status: model, MCP, python, storage, connect ---------- */
  async function renderStatus(body) {
    const st = await NX.api('GET', '/api/status');
    C.status = st;
    const pr = st.provider || {};
    NX.statusPill.set(pr);
    const origin = location.origin;
    body.innerHTML = `
      <h3>Model</h3>
      <div class="card kv">
        <div>Route</div><div>${esc(pr.route || '—')}</div>
        <div>Model</div><div>${esc(pr.model || '—')}</div>
        <div>Reachable</div><div><span class="dot ${pr.reachable ? 'ok' : 'bad'}" style="display:inline-block"></span> ${pr.reachable ? 'yes' : 'no'}${pr.latency_ms != null ? ' &middot; ' + pr.latency_ms + ' ms' : ''}</div>
        <div>Error</div><div>${esc(pr.error || '—')}</div>
      </div>
      <h3>MCP servers</h3>
      <div>${st.mcp.length ? st.mcp.map((m) => `<div class="card row"><span style="flex:1">${esc(m.name)} <span class="muted">${m.enabled ? 'enabled' : 'disabled'}</span></span>
        <button class="mini" data-probe="${esc(m.id)}">Probe</button></div>`).join('') : '<div class="muted">None. Add one in Settings &rarr; MCP.</div>'}</div>
      <h3>Runtime</h3>
      <div class="card kv">
        <div>Version</div><div>${esc(st.version)}</div>
        <div>Python tool</div><div>${st.python_enabled ? 'enabled' : 'disabled (NX_ENABLE_PYTHON=1 to enable)'}</div>
        <div>Store</div><div>${esc(st.db.path)}</div>
        <div>Rows</div><div>${st.db.docs} docs &middot; ${st.db.files} files &middot; ${st.db.actions} actions</div>
      </div>
      <h3>Connect (external)</h3>
      <div class="card"><div class="muted">Bearer token = Settings &rarr; Data. Replace $NX_TOKEN.</div>
        <pre class="pre">curl -X POST ${esc(origin)}/api/chats/CHAT_ID/messages \\
  -H "Authorization: Bearer $NX_TOKEN" -H "Content-Type: application/json" \\
  -d '{"content":"status?"}'

curl -X POST ${esc(origin)}/api/workflows/WF_ID/run \\
  -H "Authorization: Bearer $NX_TOKEN" -H "Content-Type: application/json" \\
  -d '{"input":"...","project_id":"PROJECT_ID"}'</pre></div>`;
    body.onclick = async (e) => {
      const b = e.target.closest('[data-probe]');
      if (!b) return;
      const r = await NX.api('POST', `/api/mcp/${b.dataset.probe}/probe`);
      NX.toast(r.ok ? 'MCP ok: ' + r.tools.join(', ') : 'MCP error: ' + r.error, r.ok ? 'ok' : 'err');
    };
  }

  /* ---------- runs ---------- */
  NX.watchRun = function (run) {
    NX.toast('Run started: ' + run.id);
    const started = Date.now();
    const tick = async () => {
      const r = await NX.api('GET', '/api/runs/' + run.id);
      if (r.status === 'done' || r.status === 'failed') {
        NX.toast(`Run ${r.status}: ${r.steps.length} step(s)${r.error ? ' - ' + r.error : ''}`, r.status === 'done' ? 'ok' : 'err');
        await NX.chat.refresh(false);
        NX.bus.emit('file-changed');
        if (S.panel) render();
        return;
      }
      if (Date.now() - started < 600000) setTimeout(tick, 1200);
    };
    setTimeout(tick, 800);
    return run;
  };

  NX.panels = {
    open, close, render,
    get tabs() { return TABS; },
    byId,
  };
  NX.panelsUI = {
    init() {
      $('#tabs').onclick = (e) => { const b = e.target.closest('[data-tab]'); if (b) { S.panel = b.dataset.tab; render(); } };
      $('#closePanel').onclick = close;
      NX.bus.on('chat-open', () => { if (S.panel) render(); });
      NX.bus.on('turn-done', () => { if (S.panel === 'context' || S.panel === 'record') render(); });
    },
  };
})();
