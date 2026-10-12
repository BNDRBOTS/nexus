/* NX knacks: per-message prompt transforms. Built-ins are data here; user knacks come from settings.knacks
   (validated server-side). NX.knackCatalog / NX.knackPrompt are pure. Built-in ids are stable (tests check them). */
(function () {
  'use strict';
  const NX = (window.NX = window.NX || {});
  const MAX_CHARS = 8000;

  NX.KNACKS = [
    { id: 'continue', label: 'Continue', prompt: 'Continue the message below from where it stops. Output only the continuation.' },
    { id: 'next', label: 'Next step', prompt: 'Turn the message below into the single next action. Call project_set_status with state "active" and next_step set to that action. Reply with the tool result only.' },
    { id: 'checklist', label: 'Checklist', prompt: 'Convert the message below into a numbered checklist. One verifiable action per line. No preamble.' },
    { id: 'shorter', label: 'Shorter', prompt: 'Rewrite the message below at half its length. Keep every fact, number and name.' },
    { id: 'critique', label: 'Critique', prompt: 'Check the message below. List unsupported claims, errors and missing steps as bullets. Give the fix for each.' },
    { id: 'table', label: 'To table', prompt: 'If the message below contains structured data, convert it to a Markdown table. Otherwise reply NO_TABLE.' },
  ];

  /* Pure: built-ins first, then user knacks (ids are unique server-side; built-ins win on a clash). */
  NX.knackCatalog = function (userKnacks) {
    const builtin = NX.KNACKS;
    const ids = new Set(builtin.map((k) => k.id));
    const extra = (Array.isArray(userKnacks) ? userKnacks : []).filter((k) => k && !ids.has(k.id));
    return builtin.concat(extra);
  };

  /* Pure: returns {label, content} for a knack applied to a message body, or null if id unknown. */
  NX.knackPrompt = function (id, text, userKnacks) {
    const k = NX.knackCatalog(userKnacks).find((x) => x.id === id);
    if (!k) return null;
    const raw = String(text || '');
    const body = raw.length > MAX_CHARS ? raw.slice(0, MAX_CHARS) + '\n[truncated to ' + MAX_CHARS + ' chars]' : raw;
    return { label: k.label, content: k.prompt + '\n\n---\n' + body };
  };
})();
