/* NX knacks: per-message prompt transforms. Data only, plus one pure builder (NX.knackPrompt).
   Add a knack by appending to NX.KNACKS. id is stable (used by the UI and by tests). */
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

  /* Pure: returns {label, content} for a knack applied to a message body, or null if id unknown. */
  NX.knackPrompt = function (id, text) {
    const k = NX.KNACKS.find((x) => x.id === id);
    if (!k) return null;
    const raw = String(text || '');
    const body = raw.length > MAX_CHARS ? raw.slice(0, MAX_CHARS) + '\n[truncated to ' + MAX_CHARS + ' chars]' : raw;
    return { label: k.label, content: k.prompt + '\n\n---\n' + body };
  };
})();
