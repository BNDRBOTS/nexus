// Unit test: knack catalog and prompt builder (js/05-knacks.js). Run: node tests/test_knacks.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'js', '05-knacks.js'), 'utf8'), ctx);
const NX = ctx.window.NX;

const ids = NX.KNACKS.map((k) => k.id);
assert.strictEqual(new Set(ids).size, ids.length, 'knack ids must be unique');
assert.ok(NX.KNACKS.every((k) => k.label && k.prompt), 'every knack needs label and prompt');

assert.strictEqual(NX.knackPrompt('nope', 'x'), null, 'unknown id returns null');

const p = NX.knackPrompt('shorter', 'Alpha 77 beta');
assert.strictEqual(p.label, 'Shorter');
assert.ok(p.content.startsWith(NX.KNACKS.find((k) => k.id === 'shorter').prompt + '\n\n---\n'));
assert.ok(p.content.endsWith('Alpha 77 beta'));

const q = NX.knackPrompt('continue', 'x'.repeat(9000));
assert.ok(q.content.includes('[truncated to 8000 chars]'));
assert.ok(q.content.length < 8000 + 400);

assert.doesNotThrow(() => NX.knackPrompt('table', undefined));

// user-defined knacks: appended after built-ins; built-in id wins on a clash; applied like built-ins
const user = [{ id: 'u-ab12', label: 'Tighten', prompt: 'Tighten the text.' }, { id: 'next', label: 'Hijack', prompt: 'x' }];
const cat = NX.knackCatalog(user);
assert.strictEqual(cat.length, NX.KNACKS.length + 1, 'clashing user id is dropped');
assert.strictEqual(cat[cat.length - 1].id, 'u-ab12');
assert.strictEqual(NX.knackCatalog(undefined).length, NX.KNACKS.length);
const u = NX.knackPrompt('u-ab12', 'body', user);
assert.strictEqual(u.label, 'Tighten');
assert.ok(u.content.startsWith('Tighten the text.\n\n---\n'));
assert.strictEqual(NX.knackPrompt('u-ab12', 'body'), null, 'user knack unknown without catalog');
assert.strictEqual(NX.knackPrompt('next', 'body', user).label, 'Next step', 'built-in wins');
console.log('knacks: ok (' + NX.KNACKS.length + ' knacks)');
