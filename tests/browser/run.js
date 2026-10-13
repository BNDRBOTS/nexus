// Runs all browser tests against NX_URL with owner token NX_TOKEN. Exit 1 on any failure.
const tests = [require('./skills'), require('./plugins'), require('./project_tokens')];
(async () => {
  const base = process.env.NX_URL;
  const token = process.env.NX_TOKEN;
  if (!base || !token) { console.error('NX_URL and NX_TOKEN required'); process.exit(2); }
  let failed = 0;
  for (const t of tests) {
    try { await t.run({ base, token }); console.log('ok   ' + t.name); }
    catch (e) { failed++; console.log('FAIL ' + t.name + ': ' + e.message); }
  }
  process.exit(failed ? 1 : 0);
})();
