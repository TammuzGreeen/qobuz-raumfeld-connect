'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Real pinned/patched SDK method; replace only its request/network boundary.
function fixture({status = 200, network = false, host = true} = {}) {
  const source = fs.readFileSync(process.env.ZONE_MANAGER_SOURCE || path.join(
    path.dirname(require.resolve('node-raumkernel/package.json')), 'lib/lib.manager.zoneManager.js'), 'utf8');
  let requests = 0;
  class Base {
    logError() {} logVerbose() {} logDebug() {}
    getSettings() { return {raumfeldHostRequestPort: 47365}; }
  }
  const context = {module: {exports: {}}, setTimeout, clearTimeout, require: name => {
    if (name === './lib.manager.base') return Base;
    if (name === 'request') return (options, callback) => {
      requests++;
      assert.equal(new URL(options.url).pathname, '/connectRoomToZone');
      callback(network ? Object.assign(new Error('synthetic-private-url'), {code: 'ECONNREFUSED'}) : null,
        network ? undefined : {statusCode: status}, 'synthetic-private-response');
    };
    return {};
  }};
  vm.runInNewContext(source, context);
  const manager = new context.module.exports();
  manager.systemHost = host ? 'synthetic-host' : '';
  return {manager, calls: () => requests};
}

test('pinned SDK reports exact renderer HTTP rejection without body or retry', async () => {
  const f = fixture({status: 409});
  await assert.rejects(f.manager.connectRoomToZone('synthetic-room', ''), error => {
    assert.equal(error.rendererRequestOrigin, 'raumfeld_response');
    assert.equal(error.rendererHttpStatus, 409);
    assert.equal(error.rendererRequestAttempted, true);
    assert.ok(!error.message.includes('synthetic-private'));
    return true;
  });
  assert.equal(f.calls(), 1);
});

test('pinned SDK separates network refusal from missing-host local check', async () => {
  for (const host of [false, true]) {
    const f = fixture({network: true, host});
    await assert.rejects(f.manager.connectRoomToZone('synthetic-room', ''), error => {
      assert.equal(error.rendererRequestOrigin, host ? 'network' : 'local_check');
      assert.equal(error.rendererRequestAttempted, host);
      if (host) assert.equal(error.rendererNetworkCode, 'ECONNREFUSED');
      return true;
    });
    assert.equal(f.calls(), host ? 1 : 0);
  }
});

test('pinned SDK successful explicit creation remains one acknowledged request', async () => {
  const f = fixture();
  await f.manager.connectRoomToZone('synthetic-room', '');
  assert.equal(f.calls(), 1);
});
