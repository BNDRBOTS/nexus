/* NX settings modal: model, theme, roles, skills, MCP, workflows, data. Generic doc editors driven by FORMS. */
(function () {
  'use strict';
  const NX = window.NX;
  const { esc, $, byId } = NX.util;
  const S = NX.state;

  const SECTIONS = [['model', 'Model'], ['theme', 'Theme'], ['roles', 'Roles'], ['skills', 'Skills'], ['knacks', 'Knacks'],
    ['mcp', 'MCP'], ['workflows', 'Workflows'], ['data', 'Data']];

  const STEPS_EXAMPLE = [
    { id: 'plan', prompt: 'Write a short plan for: {{input}}. Use workspace_write to save it to plan.md.', mode: 'work', save_path: 'plan.md' },
    { id: 'review', prompt: 'Review this plan and list the top three risks:\n{{prev}}', mode: 'research', save_path: 'risks.md' },
  ];

  const FORMS = {
    role: {
      label: 'Role', list: 'roles',
      fields: [['name', 'text', 'Name'], ['title', 'text', 'Business role (shown in the picker)'], ['system_prompt', 'textarea', 'System prompt']],
      blank: { name: 'New role', title: '', system_prompt: '' },
    },
    skill: {
      label: 'Skill', list: 'skills',
      fields: [['name', 'text', 'Name'], ['instructions', 'textarea', 'Instructions (injected into the system block when enabled on a chat)']],
      blank: { name: 'New skill', instructions: '' },
    },
    mcp: {
      label: 'MCP server', list: 'mcp',
      fields: [['name', 'text', 'Name'], ['url', 'text', 'Endpoint URL (Streamable HTTP JSON-RPC)'], ['enabled', 'bool', 'Enabled'],
        ['headers', 'json', 'Headers (JSON object, e.g. {"Authorization":"Bearer ..."})']],
      blank: { name: 'New MCP server', url: 'https://', enabled: true, headers: {} },
    },
    workflow: {
      label: 'Workflow', list: 'workflows',
      fields: [['name', 'text', 'Name'], ['steps', 'json', 'Steps (JSON array of {id, prompt, mode, save_path}). {{input}} = run input, {{prev}} = previous step output']],
      blank: { name: 'New workflow', steps: STEPS_EXAMPLE },
    },
  };

  const sel = {}; // kind -> selected id ('' = new)
  let pendingDraft = null; // skill maker: draft waiting to fill the new-skill editor
  const takeDraft = () => { const d = pendingDraft; pendingDraft = null; return d; };
  const draftBox = () => `<div class="field"><label>Draft from description (model drafts; you review before Save)</label>
    <textarea class="textarea" data-draft-desc style="min-height:70px" placeholder="What should this skill make the assistant do?"></textarea>
    <div class="row"><button class="btn" data-do="draft">Draft skill</button></div></div>`;

  function open(section) {
    NX.modal.open(`
      <div class="modal-h"><h2>Settings</h2><button class="mini" data-x="close">Close</button></div>
      <div class="settings-grid">
        <nav class="settings-nav">${SECTIONS.map(([id, label]) => `<button data-s="${id}">${label}</button>`).join('')}</nav>
        <div class="settings-body" id="sec"></div>
      </div>`);
    const card = $('#modalCard');
    card.onclick = (e) => {
      if (e.target.dataset.x === 'close') return NX.modal.close();
      const s = e.target.closest('[data-s]');
      if (s) show(s.dataset.s);
    };
    show(section || 'model');
  }

  function show(id) {
    document.querySelectorAll('#modalCard [data-s]').forEach((b) => b.classList.toggle('on', b.dataset.s === id));
    const sec = $('#sec');
    sec.onclick = null;
    sec.oninput = null;
    const fn = { model: modelSec, theme: themeSec, knacks: knacksSec, data: dataSec }[id];
    if (fn) return fn(sec);
    docSec(sec, Object.keys(FORMS).find((k) => FORMS[k].list === id));
  }

  /* ---------- Model ---------- */
  function modelSec(root) {
    const st = S.settings;
    const p = st.provider;
    const presets = S.providerPresets || {};
    const preset = presets[p.preset] ? p.preset : 'custom';
    const info = presets[preset] || {};
    const keyNote = p.mode === 'gateway'
      ? (S.gatewayConfigured ? 'Operator gateway key is configured on the server.' : 'No gateway key on server (NX_GATEWAY_KEY unset).')
      : 'Your key is kept in this browser only and sent per request. Never stored on the server.';
    const efforts = info.efforts || [];
    root.innerHTML = `
      <h3>Model route</h3>
      <div class="seg" id="route">
        <button data-r="custom" class="${p.mode === 'custom' ? 'on' : ''}">Your key</button>
        <button data-r="gateway" class="${p.mode === 'gateway' ? 'on' : ''}">Operator gateway</button>
      </div>
      <div class="field"><label>Provider</label>
        <select id="pPreset" class="select">${Object.entries(presets).map(([k, v]) =>
          `<option value="${k}" ${k === preset ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select>
        <div class="muted" id="pPresetNote">Preset fills base URL and model. Edit them below if needed.</div></div>
      <div class="field"><label>Base URL (OpenAI-compatible)</label><input id="pBase" class="input" value="${esc(p.base_url)}"></div>
      <div class="field"><label>Model</label><input id="pModel" class="input" value="${esc(p.model)}"
        placeholder="${preset === 'openrouter' ? 'e.g. a model id from openrouter.ai/models' : ''}"></div>
      <div class="field" id="thinkField" ${info.thinking ? '' : 'hidden'}><label>Thinking (DeepSeek)</label>
        <select id="pThink" class="select">
          <option value="" ${!p.thinking ? 'selected' : ''}>API default (enabled)</option>
          <option value="enabled" ${p.thinking === 'enabled' ? 'selected' : ''}>enabled</option>
          <option value="disabled" ${p.thinking === 'disabled' ? 'selected' : ''}>disabled</option></select></div>
      <div class="field" id="effortField" ${efforts.length ? '' : 'hidden'}><label>Reasoning effort</label>
        <select id="pEffort" class="select"><option value="">provider default</option>${efforts.map((e) =>
          `<option value="${e}" ${p.reasoning_effort === e ? 'selected' : ''}>${e}</option>`).join('')}</select></div>
      <div class="field"><label>Temperature (blank = mode default; ignored by DeepSeek thinking mode)</label>
        <input id="pTemp" class="input" type="number" step="0.1" min="0" max="2" value="${p.temperature == null ? '' : p.temperature}"></div>
      <div class="field"><label>API key (this browser only)</label><input id="pKey" class="input" type="password" autocomplete="off" value="${esc(NX.auth.providerKey())}" placeholder="API key">
        <div class="muted">${esc(keyNote)}</div></div>
      <h3>Behavior</h3>
      <div class="field"><label>Global system prompt</label><textarea id="pSys" class="textarea">${esc(st.system_prompt)}</textarea></div>
      <div class="field"><label>Context budget (chars)</label><input id="pBudget" class="input" type="number" min="1000" step="1000" value="${st.context_chars}"></div>
      <div class="row"><button class="btn primary" id="pSave">Save model settings</button><span class="spacer"></span><button class="btn ghost" id="pTest">Test connection</button></div>`;
    let route = p.mode;
    root.querySelectorAll('#route [data-r]').forEach((b) => b.onclick = () => {
      route = b.dataset.r;
      root.querySelectorAll('#route [data-r]').forEach((x) => x.classList.toggle('on', x === b));
    });
    $('#pPreset', root).onchange = (e) => {
      const pr = presets[e.target.value] || {};
      $('#pBase', root).value = pr.base_url || '';
      $('#pModel', root).value = pr.model || '';
      $('#thinkField', root).hidden = !pr.thinking;
      const eff = pr.efforts || [];
      $('#effortField', root).hidden = !eff.length;
      $('#pEffort', root).innerHTML = '<option value="">provider default</option>' + eff.map((x) => `<option value="${x}">${x}</option>`).join('');
    };
    $('#pSave', root).onclick = async () => {
      const temp = $('#pTemp', root).value;
      const presetNow = $('#pPreset', root).value;
      const pr = presets[presetNow] || {};
      try {
        S.settings = await NX.api('PUT', '/api/settings', {
          system_prompt: $('#pSys', root).value,
          context_chars: +$('#pBudget', root).value,
          provider: { mode: route, preset: presetNow, base_url: $('#pBase', root).value.trim(), model: $('#pModel', root).value.trim(),
            thinking: pr.thinking ? ($('#pThink', root).value || null) : null,
            reasoning_effort: (pr.efforts || []).length ? ($('#pEffort', root).value || null) : null,
            temperature: temp === '' ? null : +temp },
        });
        localStorage.setItem('nx.providerKey', $('#pKey', root).value.trim());
        NX.toast('Model settings saved', 'ok');
        NX.statusPill.refresh();
      } catch (e) { NX.toast(e.message, 'err'); }
    };
    $('#pTest', root).onclick = async () => {
      const r = await NX.api('GET', '/api/status').catch((e) => ({ provider: { reachable: false, error: e.message } }));
      const pr = r.provider || {};
      NX.toast(pr.reachable ? `Reachable · ${pr.latency_ms} ms` : 'Unreachable: ' + (pr.error || 'unknown'), pr.reachable ? 'ok' : 'err');
      NX.statusPill.set(pr);
    };
  }

  /* ---------- Theme ---------- */
  function themeSec(root) {
    const t = Object.assign({}, S.settings.theme);
    const presets = NX.presets;
    root.innerHTML = `
      <h3>Presets</h3>
      <div class="presets">${Object.keys(presets).map((k) => `<button class="chip" data-preset="${k}">${k}</button>`).join('')}</div>
      <h3>Colors</h3>
      <div class="field"><label>Accent</label><div class="row"><input type="color" id="tAccent" value="${t.accent}"><code>${t.accent}</code></div></div>
      <div class="field"><label>Background</label><div class="row"><input type="color" id="tBg" value="${t.bg}"><code>${t.bg}</code></div></div>
      <div class="field"><label>Surface</label><div class="row"><input type="color" id="tSurface" value="${t.surface}"><code>${t.surface}</code></div></div>
      <div class="field"><label>Text</label><div class="row"><input type="color" id="tText" value="${t.text}"><code>${t.text}</code></div></div>
      <h3>Shape</h3>
      <div class="field"><label>Radius <span id="rOut">${t.radius}</span>px</label><input type="range" id="tRadius" min="4" max="20" value="${t.radius}"></div>
      <div class="field"><label>Font scale <span id="fOut">${t.font_scale}</span></label><input type="range" id="tFont" min="0.9" max="1.2" step="0.05" value="${t.font_scale}"></div>
      <div class="row"><button class="btn primary" id="tSave">Save theme</button><span class="spacer"></span><button class="btn ghost" id="tReset">Reset to graphite</button></div>`;
    const read = () => ({
      accent: $('#tAccent', root).value, bg: $('#tBg', root).value, surface: $('#tSurface', root).value,
      text: $('#tText', root).value, radius: +$('#tRadius', root).value, font_scale: +$('#tFont', root).value,
    });
    const live = () => {
      Object.assign(t, read());
      $('#rOut', root).textContent = t.radius;
      $('#fOut', root).textContent = t.font_scale;
      NX.theme.apply(t);
    };
    root.oninput = live;
    root.onclick = (e) => {
      const b = e.target.closest('[data-preset]');
      if (b) {
        const pr = presets[b.dataset.preset];
        $('#tAccent', root).value = pr.accent; $('#tBg', root).value = pr.bg; $('#tSurface', root).value = pr.surface;
        $('#tText', root).value = pr.text; $('#tRadius', root).value = pr.radius;
        live();
      }
    };
    $('#tSave', root).onclick = async () => {
      try {
        live();
        S.settings = await NX.api('PUT', '/api/settings', { theme: read() });
        NX.toast('Theme saved', 'ok');
      } catch (e) { NX.toast(e.message, 'err'); }
    };
    $('#tReset', root).onclick = () => { $('#tAccent', root).value = presets.graphite.accent; $('#tBg', root).value = presets.graphite.bg;
      $('#tSurface', root).value = presets.graphite.surface; $('#tText', root).value = presets.graphite.text;
      $('#tRadius', root).value = presets.graphite.radius; $('#tFont', root).value = 1; live(); };
  }

  /* ---------- Generic doc editor (roles, skills, mcp, workflows) ---------- */
  function docSec(root, kind) {
    const F = FORMS[kind];
    const items = S[F.list] || [];
    const id = sel[kind] === undefined ? (items[0] && items[0].id) || '' : sel[kind];
    const doc = id ? byId(items, id) : Object.assign({}, F.blank, takeDraft());
    root.innerHTML = `
      <div class="row" style="flex-wrap:wrap">
        ${items.map((d) => `<button class="chip ${d.id === id ? 'on' : ''}" data-pick="${esc(d.id)}">${esc(d.name || d.id)}</button>`).join('')}
        <button class="chip" data-pick="">+ New ${F.label.toLowerCase()}</button>
      </div>
      <h3>${id ? 'Edit ' + esc(F.label.toLowerCase()) : 'New ' + esc(F.label.toLowerCase())}</h3>
      ${kind === 'skill' ? draftBox() : ''}
      ${F.fields.map(([k, type, label]) => field(k, type, label, doc[k])).join('')}
      <div class="row">
        <button class="btn primary" data-do="save">Save</button>
        ${kind === 'workflow' && id ? '<button class="btn" data-do="run">Run</button>' : ''}
        ${kind === 'mcp' && id ? '<button class="btn" data-do="probe">Probe</button>' : ''}
        <span class="spacer"></span>
        ${id ? '<button class="btn ghost danger" data-do="delete">Delete</button>' : ''}
      </div>`;
    root.onclick = (e) => onDocClick(e, kind, F, id, doc);
  }

  function field(k, type, label, v) {
    if (type === 'bool') return `<label class="check"><input type="checkbox" data-f="${k}" ${v ? 'checked' : ''}> ${esc(label)}</label>`;
    if (type === 'textarea') return `<div class="field"><label>${esc(label)}</label><textarea class="textarea" data-f="${k}" style="min-height:120px">${esc(v || '')}</textarea></div>`;
    if (type === 'json') return `<div class="field"><label>${esc(label)}</label><textarea class="textarea mono" data-f="${k}" data-json="1" style="min-height:120px">${esc(JSON.stringify(v || (k === 'steps' ? [] : {}), null, 2))}</textarea></div>`;
    return `<div class="field"><label>${esc(label)}</label><input class="input" data-f="${k}" value="${esc(v || '')}"></div>`;
  }

  async function onDocClick(e, kind, F, id, doc) {
    const pick = e.target.closest('[data-pick]');
    if (pick) { sel[kind] = pick.dataset.pick; return show(F.list); }
    const b = e.target.closest('[data-do]');
    if (!b) return;
    const act = b.dataset.do;
    try {
      if (act === 'draft') {
        const desc = (document.querySelector('#sec [data-draft-desc]') || {}).value || '';
        if (!desc.trim()) return NX.toast('Describe the skill first', 'err');
        b.disabled = true;
        try { pendingDraft = await NX.api('POST', '/api/skills/draft', { description: desc.trim() }); } finally { b.disabled = false; }
        sel[kind] = '';
        NX.toast('Draft ready. Review the fields, then Save.', 'ok');
        return show(F.list);
      }
      if (act === 'save') {
        const body = Object.assign({}, doc);
        for (const el of document.querySelectorAll('#sec [data-f]')) {
          const k = el.dataset.f;
          if (el.type === 'checkbox') body[k] = el.checked;
          else if (el.dataset.json) body[k] = JSON.parse(el.value || 'null');
          else body[k] = el.value;
        }
        const saved = id ? await NX.api('PUT', `/api/doc/${kind}/${id}`, body)
          : await NX.api('POST', `/api/doc/${kind}`, body);
        sel[kind] = saved.id;
        await NX.chat.refresh(false);
        NX.toast('Saved', 'ok');
        return show(F.list);
      }
      if (act === 'delete') {
        if (!(await NX.confirm('Delete ' + F.label.toLowerCase(), doc.name || id))) return;
        await NX.api('DELETE', `/api/doc/${kind}/${id}`);
        sel[kind] = undefined;
        await NX.chat.refresh(false);
        return show(F.list);
      }
      if (act === 'run') {
        const input = await NX.ask('Workflow input', '', { hint: 'Substituted for {{input}}.' });
        if (input === null) return;
        const projectId = (S.chat && S.chat.project_id) || S.projectId || S.inbox;
        NX.modal.close();
        return NX.watchRun(await NX.api('POST', `/api/workflows/${id}/run`, { input, project_id: projectId }));
      }
      if (act === 'probe') {
        const r = await NX.api('POST', `/api/mcp/${id}/probe`);
        NX.toast(r.ok ? 'MCP ok: ' + r.tools.join(', ') : 'MCP error: ' + r.error, r.ok ? 'ok' : 'err');
      }
    } catch (err) {
      NX.toast(err.message, 'err');
    }
  }

  /* ---------- Knacks: user-defined prompt transforms. Built-ins are listed, not editable. Server validates. ---------- */
  function knacksSec(root) {
    const rows = JSON.parse(JSON.stringify((S.settings && S.settings.knacks) || []));
    const newId = () => 'u-' + Math.random().toString(36).slice(2, 8);
    const max = 20;   // = config.KNACK_MAX (server enforces)
    const draw = () => {
      root.innerHTML = `
        <h3>Knacks</h3>
        <div class="muted">Per-message prompts for assistant replies (Knacks button). Built-in: ${esc(NX.KNACKS.map((k) => k.label).join(', '))}.
          The prompt is sent with the reply text as the next turn. Max ${max} knacks.</div>
        <div id="kList">${rows.map((k, i) => `
          <div class="kRow" data-i="${i}">
            <div class="field"><label>Label (max 24)</label><input class="input" data-f="label" maxlength="24" value="${esc(k.label)}"></div>
            <div class="field"><label>Prompt (max 2000)</label><textarea class="textarea" data-f="prompt" maxlength="2000">${esc(k.prompt)}</textarea></div>
            <div class="row"><span class="muted mono">${esc(k.id)}</span><span class="spacer"></span><button class="btn ghost" data-rm="${i}">Remove</button></div>
          </div>`).join('') || '<div class="muted">No user knacks yet.</div>'}</div>
        <div class="row"><button class="btn" id="kAdd" ${rows.length >= max ? 'disabled' : ''}>Add knack</button><span class="spacer"></span><button class="btn primary" id="kSave">Save knacks</button></div>`;
    };
    root.oninput = (e) => {
      const row = e.target.closest('.kRow');
      if (row && e.target.dataset.f) rows[+row.dataset.i][e.target.dataset.f] = e.target.value;
    };
    root.onclick = async (e) => {
      const rm = e.target.closest('[data-rm]');
      if (rm) { rows.splice(+rm.dataset.rm, 1); return draw(); }
      if (e.target.closest('#kAdd')) { rows.push({ id: newId(), label: 'New knack', prompt: '' }); return draw(); }
      if (e.target.closest('#kSave')) {
        try {
          S.settings = await NX.api('PUT', '/api/settings', { knacks: rows.map((r) => ({ id: r.id, label: r.label, prompt: r.prompt })) });
          rows.splice(0, rows.length, ...JSON.parse(JSON.stringify(S.settings.knacks || [])));
          NX.toast('Knacks saved', 'ok');
          NX.bus.emit('knacks-changed');
          draw();
        } catch (err) { NX.toast(err.message, 'err'); }
      }
    };
    draw();
  }

  /* ---------- Data: token, backup, restore, connect ---------- */
  function dataSec(root) {
    root.innerHTML = `
      <h3>Access token</h3>
      <div class="muted">Required when the server runs with NX_TOKEN. Stored in this browser only.</div>
      <div class="field"><input id="dTok" class="input" type="password" autocomplete="off" value="${esc(NX.auth.token())}"></div>
      <div class="row"><button class="btn primary" id="dTokSave">Save token</button><button class="btn ghost" id="dTokClear">Clear</button></div>
      <h3>Backup &amp; restore</h3>
      <div class="muted">Backup = full JSON export of docs, files, and the append-only record. Restore replaces all data.</div>
      <div class="row"><button class="btn" id="dBackup">Download backup</button>
        <button class="btn ghost" id="dRestore">Restore from file</button><input type="file" id="dFile" class="hidden" accept="application/json"></div>
      <h3>Connect</h3>
      <div class="muted">Machine interface: REST + SSE. Full endpoint list: <code>GET /api/state</code>, <code>GET /api/status</code>.</div>
      <pre class="pre">${esc(location.origin)}/api/state</pre>`;
    $('#dTokSave', root).onclick = () => { localStorage.setItem('nx.token', $('#dTok', root).value.trim()); NX.toast('Token saved', 'ok'); NX.chat.refresh(false).catch(() => {}); };
    $('#dTokClear', root).onclick = () => { localStorage.removeItem('nx.token'); $('#dTok', root).value = ''; NX.toast('Token cleared'); };
    $('#dBackup', root).onclick = async () => {
      const data = await NX.api('GET', '/api/backup');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url; a.download = 'nexus-backup-' + new Date().toISOString().slice(0, 10) + '.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    $('#dRestore', root).onclick = () => $('#dFile', root).click();
    $('#dFile', root).onchange = (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = async () => {
        if (!(await NX.confirm('Restore backup', 'All current data is replaced. Continue?'))) return;
        try {
          await NX.api('POST', '/api/restore', JSON.parse(reader.result));
          NX.toast('Restored', 'ok');
          await NX.chat.refresh(false);
        } catch (err) { NX.toast(err.message, 'err'); }
      };
      reader.readAsText(f);
      e.target.value = '';
    };
  }

  NX.settings = { open };
  NX.settingsUI = {
    init() {
      $('#openSettings').onclick = () => open('model');
    },
  };
})();
