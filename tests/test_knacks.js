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
console.log('knacks: ok (' + NX.KNACKS.length + ' knacks)');
