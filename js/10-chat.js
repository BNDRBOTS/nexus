/* NX chat view: sidebar, topbar, messages, composer, slash commands, streaming turns. */
(function () {
  'use strict';
  const NX = window.NX;
  const { esc, $, fmtTime, slug, byId } = NX.util;
  const S = NX.state;

  const SUGGEST = {
    work: ['Draft a project plan with milestones and owners', 'Turn my notes into a checklist and save it to the workspace', 'Summarize the current status and the next step'],
    research: ['Summarize the Source Scope and cite the files', 'List open questions the sources do not answer', 'Write a brief from the scope and save it'],
    code: ['Write a Python script that parses a CSV and saves a report', 'Review the workspace code for bugs and fix them', 'Create a small HTML page and save it as index.html'],
  };

  const COMMANDS = [
    ['/mode', 'work|research|code', 'Set the mode for this chat'],
    ['/role', 'name | none', 'Set the role for this chat'],
    ['/reminder', 'text', 'Sticky note appended to every turn (empty clears)'],
    ['/chatprompt', 'text', 'Chat-level instruction (empty clears)'],
    ['/save', 'path', 'Save the last reply to the project workspace'],
    ['/pin', '', 'Pin or unpin the last reply as a memory anchor'],
    ['/run', 'workflow | input', 'Run a workflow against this project'],
    ['/ctx', '', 'Open the context inspector'],
    ['/new', '', 'Start a new chat'],
  ];

  /* ---------- helpers ---------- */
  function effective() {
    const c = S.chat || S.draft;
    const mode = c.mode || 'work';
    const groups = c.tool_groups || (S.modes[mode] ? S.modes[mode].tool_groups : []);
    return { mode, role_id: c.role_id || null, groups };
  }

  function currentProject() {
    const pid = (S.chat && S.chat.project_id) || S.projectId || S.inbox;
    return byId(S.projects, pid) || byId(S.projects, S.inbox);
  }

  // Mode change resets per-chat tool groups to the mode default (null = default).
  async function setField(field, value) {
    const patch = field === 'mode' ? { mode: value, tool_groups: null } : { [field]: value };
    if (S.chat) {
      S.chat = await NX.api('PATCH', '/api/chats/' + S.chat.id, patch);
    }
    Object.assign(S.draft, patch);
    await refresh(false);
  }

  /* ---------- refresh & open ---------- */
  async function refresh(full) {
    const st = await NX.api('GET', '/api/state');
    Object.assign(S, {
      version: st.version, modes: st.modes, toolGroups: st.tool_groups, inbox: st.inbox, settings: st.settings,
      projects: st.projects, chats: st.chats, roles: st.roles, skills: st.skills, workflows: st.workflows,
      mcp: st.mcp, pythonEnabled: st.python_enabled, tokenRequired: st.token_required,
      gatewayConfigured: st.gateway_configured, providerPresets: st.provider_presets,
    });
    NX.theme.apply(S.settings.theme);
    if (S.chatId && !byId(S.chats, S.chatId)) { S.chatId = null; S.chat = null; }
    if (full !== false && S.chatId) S.chat = await NX.api('GET', '/api/chats/' + S.chatId);
    renderAll();
    NX.bus.emit('state');
  }

  async function open(id) {
    S.chatId = id;
    S.chat = await NX.api('GET', '/api/chats/' + id);
    S.projectId = S.chat.project_id;
    S.draft = { mode: S.chat.mode, role_id: S.chat.role_id, tool_groups: S.chat.tool_groups, skill_ids: S.chat.skill_ids || [] };
    S.attachments = [];
    S.live = null;
    history.replaceState(null, '', '#chat=' + id);
    renderAll();
    NX.bus.emit('chat-open', S.chat);
  }

  // Selecting a project scopes the sidebar and new chats. An open chat from another project is closed,
  // so the open chat's project and the selection never disagree.
  function selectProject(id) {
    S.projectId = S.projectId === id ? null : id;
    if (S.chat && S.projectId && S.chat.project_id !== S.projectId) {
      S.chatId = null; S.chat = null; S.live = null;
      history.replaceState(null, '', '#');
    }
    renderAll();
    NX.bus.emit('chat-open', S.chat);
  }

  async function newChat() {
    const pid = S.projectId || S.inbox;
    const d = S.draft;
    const chat = await NX.api('POST', '/api/chats', {
      project_id: pid, mode: d.mode || 'work', role_id: d.role_id || null,
      tool_groups: d.tool_groups || null, skill_ids: d.skill_ids || [],
    });
    S.chats.unshift({ id: chat.id, title: chat.title, project_id: chat.project_id, mode: chat.mode, updated: chat.updated, messages: 0 });
    await open(chat.id);
    return chat;
  }

  /* ---------- rendering ---------- */
  function renderAll() {
    renderSidebar();
    renderTop();
    renderTools();
    renderMessages();
    NX.panels && NX.panels.render();
  }

  function renderSidebar() {
    const q = (S.search || '').toLowerCase();
    const pl = $('#projectList');
    pl.innerHTML = S.projects
      .slice()
      .sort((a, b) => (a.id === S.inbox ? -1 : b.id === S.inbox ? 1 : (b.updated || 0) - (a.updated || 0)))
      .map((p) => {
        const st = p.status || {};
        return `<button class="nav-item ${S.projectId === p.id ? 'active' : ''}" data-project="${esc(p.id)}">
          <span class="t">${esc(p.name)}</span>${st.state === 'active' && st.next_step ? '<span class="dotp" title="next step pending"></span>' : ''}
        </button>`;
      }).join('') || '<div class="muted">No projects</div>';

    const chats = S.chats
      .filter((c) => !S.projectId || c.project_id === S.projectId)
      .filter((c) => !q || (c.title || '').toLowerCase().includes(q))
      .sort((a, b) => (b.updated || 0) - (a.updated || 0));
    $('#chatList').innerHTML = chats.map((c) => {
      const p = byId(S.projects, c.project_id);
      return `<button class="nav-item ${S.chatId === c.id ? 'active' : ''}" data-chat="${esc(c.id)}">
        <span class="t">${esc(c.title || 'Untitled')}</span>
        ${!S.projectId && p && p.id !== S.inbox ? `<span class="sub">${esc(p.name)}</span>` : ''}
        <span class="sub">${fmtTime(c.updated)}</span></button>`;
    }).join('') || '<div class="muted" style="padding:6px 8px">No chats yet</div>';
  }

  function renderTop() {
    const p = currentProject();
    const title = S.chat ? (S.chat.title || 'Untitled') : 'New chat';
    $('#crumbs').innerHTML = `${esc(p ? p.name : '')}${p ? ' / ' : ''}<b>${esc(title)}</b>`;

    const e = effective();
    $('#modeSeg').innerHTML = Object.entries(S.modes).map(([id, m]) =>
      `<button role="radio" aria-checked="${e.mode === id}" class="${e.mode === id ? 'on' : ''}" data-mode="${id}">${esc(m.label)}</button>`).join('');

    $('#roleSelect').innerHTML = '<option value="">No role</option>' + S.roles.map((r) =>
      `<option value="${esc(r.id)}" ${e.role_id === r.id ? 'selected' : ''}>${esc(r.name)}</option>`).join('');

    $('#panelToggles').innerHTML = [['canvas', 'Canvas'], ['project', 'Project'], ['context', 'Context'], ['record', 'Record'], ['status', 'Status']]
      .map(([id, label]) => `<button class="${S.panel === id ? 'on' : ''}" data-panel="${id}">${label}</button>`).join('');
  }

  function renderTools() {
    const e = effective();
    const defs = [
      ['workspace', 'Files'], ['status', 'Status'], ['sources', 'Sources'], ['python', 'Python'], ['mcp', 'MCP'],
    ];
    $('#toolChips').innerHTML = defs.map(([g, label]) => {
      const disabled = g === 'python' && !S.pythonEnabled;
      return `<button class="chip ${e.groups.includes(g) ? 'on' : ''}" data-tool="${g}" ${disabled ? 'disabled title="Python disabled on server (NX_ENABLE_PYTHON=1)"' : ''}>${label}</button>`;
    }).join('');
    const skillIds = (S.chat || S.draft).skill_ids || [];
    $('#toolChips').innerHTML += S.skills.map((sk) =>
      `<button class="chip ${skillIds.includes(sk.id) ? 'on' : ''}" data-skill="${esc(sk.id)}" title="Skill">${esc(sk.name)}</button>`).join('');
    $('#attachments').innerHTML = (S.attachments || []).map((a, i) =>
      `<span class="chip on">${esc(a.path)}<button data-detach="${i}" aria-label="Remove">&times;</button></span>`).join('');
  }

  function msgHtml(m, isLast) {
    const isUser = m.role === 'user';
    const meta = m.meta || {};
    const chips = isUser ? '' : [
      ...(meta.tools || []).map((t) => `<span class="tool-chip ${t.ok ? 'ok' : 'bad'}" title="${esc(t.summary)}">${esc(t.name)}</span>`),
      ...(meta.saved || []).map((p) => `<span class="tool-chip">saved ${esc(p)}</span>`),
      meta.model ? `<span title="context ${esc(meta.ctx_hash)}">${esc(meta.model)}${meta.ms ? ' &middot; ' + (meta.ms / 1000).toFixed(1) + 's' : ''}</span>` : '',
    ].join('');
    const pin = m.pinned ? '<span class="pin-badge">&#9679; pinned</span>' : '';
    const acts = [
      `<button class="act" data-act="copy" data-id="${m.id}">Copy</button>`,
      `<button class="act ${m.pinned ? 'on' : ''}" data-act="pin" data-id="${m.id}">${m.pinned ? 'Unpin' : 'Pin'}</button>`,
      `<button class="act" data-act="save" data-id="${m.id}">Save to workspace</button>`,
      !isUser && isLast ? `<button class="act" data-act="regen" data-id="${m.id}">Regenerate</button>` : '',
      isUser ? `<button class="act" data-act="edit" data-id="${m.id}">Edit</button>` : '',
      !isUser ? `<span class="knack-wrap"><button class="act" data-knack-toggle="${m.id}">Knacks</button>` +
        `<div class="knack-menu hidden" role="menu">${NX.KNACKS.map((k) =>
          `<button role="menuitem" data-knack="${k.id}" data-id="${m.id}">${esc(k.label)}</button>`).join('')}</div></span>` : '',
      `<button class="act" data-act="del" data-id="${m.id}">Delete</button>`,
    ].join('');
    const body = isUser ? `<div class="bubble">${esc(meta.display || m.content)}</div>` : `<div class="bubble">${NX.md(m.content)}</div>`;
    const reasoning = !isUser && meta.reasoning
      ? `<details class="reasoning"><summary>Native reasoning · provider output, not the answer</summary><div class="reasoning-text">${esc(meta.reasoning)}</div></details>` : '';
    const truncated = !isUser && meta.finish_reason === 'length'
      ? '<div class="notice">Reply truncated by the output limit (finish_reason=length).</div>' : '';
    return `<div class="msg ${m.role}" data-mid="${m.id}">
      ${reasoning}${truncated}
      ${(pin || chips) ? `<div class="meta">${pin}${chips}</div>` : ''}
      ${body}
      <div class="msg-actions">${acts}</div></div>`;
  }

  function renderEmpty() {
    const e = effective();
    const p = currentProject();
    const st = (p && p.status) || {};
    const suggest = (SUGGEST[e.mode] || []).map((s) => `<button class="chip btn-like" data-suggest="${esc(s)}">${esc(s)}</button>`).join('');
    const resume = p && p.id !== S.inbox && (st.note || st.next_step) ? `
      <div class="resume-card">
        <div class="k">Resume &middot; ${esc(p.name)} &middot; ${esc(st.state || 'active')}</div>
        ${st.note ? `<div class="v">Last verified progress: ${esc(st.note)}</div>` : ''}
        ${st.next_step ? `<div class="v">Next step: ${esc(st.next_step)}</div>` : ''}
        <div class="row"><button class="btn primary" data-resume="1" ${st.next_step ? '' : 'disabled'}>Continue next step</button>
        <button class="btn ghost" data-panel-open="project">Open project</button></div>
      </div>` : '';
    return `${resume}<h1>What should we get done?</h1>
      <p class="lead">${esc(p ? p.name : 'Inbox')} &middot; ${esc(S.modes[e.mode] ? S.modes[e.mode].label : e.mode)} mode. Type <code>/</code> for commands.</p>
      <div class="suggest">${suggest}</div>`;
  }

  function renderMessages() {
    const empty = $('#empty');
    const box = $('#messages');
    if (!S.chat) {
      box.innerHTML = '';
      empty.innerHTML = renderEmpty();
      empty.classList.remove('hidden');
      return;
    }
    const msgs = S.chat.messages || [];
    empty.classList.toggle('hidden', msgs.length > 0 || !!S.live);
    if (!msgs.length && !S.live) empty.innerHTML = renderEmpty();
    const stage = $('#stage');
    const nearBottom = stage.scrollHeight - stage.scrollTop - stage.clientHeight < 120;
    const lastAsst = [...msgs].reverse().find((m) => m.role === 'assistant');
    let html = msgs.map((m) => msgHtml(m, lastAsst && m.id === lastAsst.id)).join('');
    if (S.live) {
      html += `<div class="msg assistant" id="live"><div class="meta">${S.live.tools.map((t) =>
        `<span class="tool-chip ${t.ok ? 'ok' : 'bad'}">${esc(t.name)}</span>`).join('')}</div>
        ${S.live.reasoning ? `<details class="reasoning" open><summary>Native reasoning · provider output, not the answer</summary><div class="reasoning-text">${esc(S.live.reasoning)}</div></details>` : ''}
        <div class="bubble">${S.live.text ? NX.md(S.live.text) : ''}<span class="caret"></span></div></div>`;
    }
    box.innerHTML = html;
    if (nearBottom || S.live) stage.scrollTop = stage.scrollHeight;
  }

  /* ---------- composer ---------- */
  function autosize() {
    const ta = $('#input');
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
  }

  function cmdHelp(text) {
    const box = $('#cmdHelp');
    if (!text.startsWith('/') || text.includes(' ')) { box.classList.add('hidden'); return; }
    const q = text.toLowerCase();
    const rows = COMMANDS.filter(([c]) => c.startsWith(q));
    if (!rows.length) { box.classList.add('hidden'); return; }
    box.innerHTML = rows.map(([c, a, d], i) => `<div class="${i === 0 ? 'sel' : ''}" data-cmd="${c}"><code>${c}</code> ${esc(a)} <span class="muted">${esc(d)}</span></div>`).join('');
    box.classList.remove('hidden');
  }

  async function runCommand(text) {
    const m = text.match(/^\/(\w+)\s*([\s\S]*)$/);
    const cmd = m ? m[1].toLowerCase() : '';
    const arg = (m ? m[2] : '').trim();
    const lastAsst = S.chat && [...S.chat.messages].reverse().find((x) => x.role === 'assistant');
    switch (cmd) {
      case 'mode': {
        if (!S.modes[arg]) return NX.toast('mode must be work|research|code', 'err');
        return setField('mode', arg);
      }
      case 'role': {
        if (!arg || arg === 'none') return setField('role_id', null);
        const r = S.roles.find((x) => x.name.toLowerCase() === arg.toLowerCase() || x.id === arg);
        if (!r) return NX.toast('no role named ' + arg, 'err');
        return setField('role_id', r.id);
      }
      case 'reminder': return setField('reminder', arg);
      case 'chatprompt': return setField('chat_prompt', arg);
      case 'save': {
        if (!S.chat || !lastAsst) return NX.toast('nothing to save yet', 'err');
        const path = arg || `notes/${slug(S.chat.title)}-${(lastAsst.id || '').slice(2, 6)}.md`;
        await NX.api('POST', `/api/chats/${S.chat.id}/messages/${lastAsst.id}/save`, { path });
        NX.toast('Saved ' + path, 'ok');
        return open(S.chat.id);
      }
      case 'pin': {
        if (!S.chat || !lastAsst) return NX.toast('nothing to pin yet', 'err');
        await NX.api('POST', `/api/chats/${S.chat.id}/messages/${lastAsst.id}/pin`, { pinned: !lastAsst.pinned });
        return open(S.chat.id);
      }
      case 'run': {
        const [name, ...rest] = arg.split('|');
        const wf = S.workflows.find((w) => w.name.toLowerCase() === (name || '').trim().toLowerCase() || w.id === (name || '').trim());
        if (!wf) return NX.toast('no workflow named ' + name, 'err');
        return NX.watchRun(await NX.api('POST', `/api/workflows/${wf.id}/run`, {
          input: rest.join('|').trim(), project_id: (S.chat && S.chat.project_id) || S.projectId || S.inbox }));
      }
      case 'ctx': return NX.panels.open('context');
      case 'new': return newChat();
      default: return NX.toast('unknown command: /' + cmd, 'err');
    }
  }

  function composeContent(text) {
    if (!S.attachments.length) return text;
    const parts = S.attachments.map((a) => `--- ${a.path} ---\n${a.content}`);
    return `${parts.join('\n\n')}\n\n${text}`;
  }

  async function send() {
    const ta = $('#input');
    const text = ta.value.trim();
    if (!text || S.busy) return;
    ta.value = '';
    autosize();
    $('#cmdHelp').classList.add('hidden');
    if (text.startsWith('/') && /^\/\w+/.test(text)) {
      try { await runCommand(text); } catch (e) { NX.toast(e.message, 'err'); }
      return;
    }
    if (!S.chat) await newChat();
    const content = composeContent(text);
    S.attachments = [];
    const firstTurn = !(S.chat.messages || []).length;
    await turn(`/api/chats/${S.chat.id}/messages`, content, text, firstTurn);
  }

  async function turn(path, content, displayText, setTitle, regenerate, display) {
    S.busy = true;
    $('#send').disabled = true;
    S.live = { text: '', tools: [], reasoning: '' };
    if (!regenerate) {
      S.chat.messages = (S.chat.messages || []).concat([{ id: 'pending', role: 'user', content: displayText, meta: {} }]);
    } else {
      S.chat.messages = S.chat.messages.slice(0, -1);
    }
    renderAll();
    let frame = 0;
    const paint = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const el = document.querySelector('#live .bubble');
        if (el && S.live) el.innerHTML = (S.live.text ? NX.md(S.live.text) : '') + '<span class="caret"></span>';
        const rt = document.querySelector('#live .reasoning-text');
        if (rt && S.live) rt.textContent = S.live.reasoning;
        const stage = $('#stage');
        stage.scrollTop = stage.scrollHeight;
      });
    };
    let failed = null;
    try {
      await NX.stream(path, regenerate ? {} : (display ? { content, display } : { content }), (ev, data) => {
        if (ev === 'delta') { S.live.text += data.text; paint(); }
        else if (ev === 'reasoning') { S.live.reasoning += data.text; paint(); }
        else if (ev === 'tool') { S.live.tools.push(data); renderMessages(); }
        else if (ev === 'context') { S.ctx = data; }
        else if (ev === 'error') { failed = data.error; }
      });
    } catch (e) {
      failed = e.message;
    }
    S.live = null;
    S.busy = false;
    $('#send').disabled = false;
    if (failed) NX.toast(failed, 'err');
    await open(S.chatId);
    if (setTitle && !failed) {
      const title = displayText.replace(/\s+/g, ' ').slice(0, 56) || 'New chat';
      S.chat = await NX.api('PATCH', '/api/chats/' + S.chatId, { title });
    }
    await refresh(false);
    NX.bus.emit('turn-done', { failed });
  }

  async function regenerate() {
    if (S.busy || !S.chat) return;
    const last = S.chat.messages[S.chat.messages.length - 1];
    if (!last || last.role !== 'assistant') return NX.toast('nothing to regenerate', 'err');
    await turn(`/api/chats/${S.chat.id}/regenerate`, '', '', false, true);
  }

  /* Knack: sends the message body through a fixed prompt as the next user turn. Nothing else is changed. */
  async function runKnack(id, mid) {
    if (!S.chat) return;
    if (S.busy) return NX.toast('Wait for the current reply', 'err');
    const msg = (S.chat.messages || []).find((m) => m.id === mid);
    const k = msg && NX.knackPrompt(id, msg.content);
    if (!k) return;
    const label = `${k.label}: ${msg.content.slice(0, 80)}`;
    await turn(`/api/chats/${S.chat.id}/messages`, k.content, label, false, false, label);
  }

  async function messageAction(act, mid) {
    if (!S.chat) return;
    const msg = (S.chat.messages || []).find((m) => m.id === mid);
    if (!msg) return;
    if (act === 'copy') { await navigator.clipboard.writeText(msg.content); return NX.toast('Copied', 'ok'); }
    if (act === 'pin') { await NX.api('POST', `/api/chats/${S.chat.id}/messages/${mid}/pin`, { pinned: !msg.pinned }); return open(S.chat.id); }
    if (act === 'save') {
      const path = await NX.ask('Save reply to workspace', `notes/${slug(S.chat.title)}-${mid.slice(2, 6)}.md`,
        { hint: 'Relative path inside the project workspace. Existing file is replaced.' });
      if (!path) return;
      await NX.api('POST', `/api/chats/${S.chat.id}/messages/${mid}/save`, { path });
      NX.toast('Saved ' + path, 'ok');
      NX.bus.emit('file-changed');
      return open(S.chat.id);
    }
    if (act === 'regen') return regenerate();
    if (act === 'edit') {
      if (!(await NX.confirm('Edit and resend', 'This message and every later message will be deleted. Each deletion is kept in the record.'))) return;
      const idx = S.chat.messages.findIndex((m) => m.id === mid);
      for (const m of S.chat.messages.slice(idx)) await NX.api('DELETE', `/api/chats/${S.chat.id}/messages/${m.id}`);
      $('#input').value = msg.content;
      autosize();
      $('#input').focus();
      return open(S.chat.id);
    }
    if (act === 'del') {
      if (!(await NX.confirm('Delete message', 'The message is removed from the chat. Its content stays in the record.'))) return;
      await NX.api('DELETE', `/api/chats/${S.chat.id}/messages/${mid}`);
      return open(S.chat.id);
    }
  }

  /* ---------- wiring ---------- */
  NX.chat = { refresh, open, newChat, send, regenerate, selectProject, setField, effective, currentProject, messageAction };

  NX.chatUI = {
    init() {
      $('#newChat').onclick = () => newChat().catch((e) => NX.toast(e.message, 'err'));
      $('#search').oninput = (e) => { S.search = e.target.value; renderSidebar(); };
      $('#projectList').onclick = (e) => {
        const b = e.target.closest('[data-project]');
        if (b) selectProject(b.dataset.project);
      };
      $('#chatList').onclick = (e) => {
        const b = e.target.closest('[data-chat]');
        if (b) { open(b.dataset.chat).catch((err) => NX.toast(err.message, 'err')); if (window.innerWidth < 900) document.getElementById('app').classList.add('side-closed'); }
      };
      $('#chatList').ondblclick = async (e) => {
        const b = e.target.closest('[data-chat]');
        if (!b) return;
        const c = byId(S.chats, b.dataset.chat);
        const t = await NX.ask('Rename chat', c.title);
        if (t && t.trim()) { await NX.api('PATCH', '/api/chats/' + c.id, { title: t.trim() }); refresh(false); }
      };
      $('#newProject').onclick = async () => {
        const name = await NX.ask('New project', '');
        if (!name || !name.trim()) return;
        const p = await NX.api('POST', '/api/doc/project', { name: name.trim(), instructions: '', scope: [],
          status: { state: 'active', note: '', next_step: '' } });
        S.projectId = p.id;
        await refresh(false);
        NX.panels.open('project');
      };
      $('#modeSeg').onclick = (e) => {
        const b = e.target.closest('[data-mode]');
        if (b) setField('mode', b.dataset.mode).catch((err) => NX.toast(err.message, 'err'));
      };
      $('#roleSelect').onchange = (e) => setField('role_id', e.target.value || null).catch((err) => NX.toast(err.message, 'err'));
      $('#panelToggles').onclick = (e) => {
        const b = e.target.closest('[data-panel]');
        if (b) NX.panels.open(b.dataset.panel);
      };
      $('#toolChips').onclick = (e) => {
        const b = e.target.closest('[data-tool]');
        if (!b || b.disabled) return;
        const e2 = effective();
        const groups = e2.groups.includes(b.dataset.tool) ? e2.groups.filter((g) => g !== b.dataset.tool)
          : e2.groups.concat([b.dataset.tool]);
        setField('tool_groups', groups).catch((err) => NX.toast(err.message, 'err'));
      };
      $('#toolChips').addEventListener('click', (e) => {
        const b = e.target.closest('[data-skill]');
        if (!b) return;
        const cur = (S.chat || S.draft).skill_ids || [];
        const next = cur.includes(b.dataset.skill) ? cur.filter((x) => x !== b.dataset.skill) : cur.concat([b.dataset.skill]);
        setField('skill_ids', next).catch((err) => NX.toast(err.message, 'err'));
      });
      $('#attachments').onclick = (e) => {
        const b = e.target.closest('[data-detach]');
        if (b) { S.attachments.splice(+b.dataset.detach, 1); renderTools(); }
      };
      $('#messages').onclick = (e) => {
        const cp = e.target.closest('[data-copy]');
        if (cp) { navigator.clipboard.writeText(cp.closest('.code').querySelector('code').textContent); return NX.toast('Copied code', 'ok'); }
        const kt = e.target.closest('[data-knack-toggle]');
        if (kt) {
          const menu = kt.parentElement.querySelector('.knack-menu');
          const willOpen = menu.classList.contains('hidden');
          document.querySelectorAll('.knack-menu').forEach((x) => x.classList.add('hidden'));
          menu.classList.toggle('hidden', !willOpen);
          return;
        }
        const kn = e.target.closest('[data-knack]');
        if (kn) {
          document.querySelectorAll('.knack-menu').forEach((x) => x.classList.add('hidden'));
          runKnack(kn.dataset.knack, kn.dataset.id).catch((err) => NX.toast(err.message, 'err'));
          return;
        }
        const b = e.target.closest('[data-act]');
        if (b) messageAction(b.dataset.act, b.dataset.id).catch((err) => NX.toast(err.message, 'err'));
      };
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.knack-wrap')) document.querySelectorAll('.knack-menu').forEach((x) => x.classList.add('hidden'));
      });
      $('#empty').onclick = (e) => {
        const s = e.target.closest('[data-suggest]');
        if (s) { $('#input').value = s.dataset.suggest; autosize(); $('#input').focus(); }
        if (e.target.closest('[data-resume]')) {
          const p = currentProject();
          const next = p && p.status && p.status.next_step;
          if (next) { $('#input').value = 'Continue from the last verified state. Next step: ' + next; send(); }
        }
        const po = e.target.closest('[data-panel-open]');
        if (po) NX.panels.open(po.dataset.panelOpen);
      };
      const input = $('#input');
      input.oninput = () => { autosize(); cmdHelp(input.value); };
      input.onkeydown = (e) => {
        const help = $('#cmdHelp');
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
        if (e.key === 'Escape') help.classList.add('hidden');
        if (e.key === 'Tab' && !help.classList.contains('hidden')) {
          const first = help.querySelector('[data-cmd]');
          if (first) { e.preventDefault(); input.value = first.dataset.cmd + ' '; cmdHelp(''); autosize(); }
        }
      };
      $('#cmdHelp').onclick = (e) => {
        const d = e.target.closest('[data-cmd]');
        if (d) { input.value = d.dataset.cmd + ' '; input.focus(); cmdHelp(''); }
      };
      $('#send').onclick = send;
      $('#toggleSidebar').onclick = () => document.getElementById('app').classList.toggle('side-closed');
      NX.bus.on('file-changed', () => NX.panels && NX.panels.render());
    },
  };
})();
