'use strict';
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const token = 'smoke-test-only-'.repeat(3);
const child = spawn(process.execPath, ['raumkernel/main.js'], {
  env: {...process.env, API_TOKEN: token, DISCOVERY_MODE: 'off', API_PORT: '18787'}, stdio: 'inherit',
});
(async () => {
  try {
    let ready = false;
    for (let i = 0; i < 40; i++) {
      try { ready = (await fetch('http://127.0.0.1:18787/healthz')).ok; } catch {}
      if (ready) break;
      await new Promise(r => setTimeout(r, 100));
    }
    assert.ok(ready);
    const state = await (await fetch('http://127.0.0.1:18787/v1/state', {
      headers: {Authorization: `Bearer ${token}`},
    })).json();
    assert.equal(state.capabilities.playback, false);
    assert.equal(state.topologyFresh, false);
    assert.equal(state.rooms.length, 0);
    console.log('Process smoke test passed');
  } finally { child.kill('SIGTERM'); }
})().catch(error => { console.error(error); process.exitCode = 1; });
