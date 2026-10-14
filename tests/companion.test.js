'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {Companion, createCompanionApi} = require('../raumkernel/companion');
const path = require('node:path');
const {Raumkernel} = require('node-raumkernel');
const ManagerDisposer = require(path.join(path.dirname(require.resolve('node-raumkernel/package.json')),
  'lib/lib.managerDisposer'));

function fixture({assigned = false, grouped = false, timeoutMs = 20} = {}) {
  const store = new StateStore();
  store.hostFound('synthetic-host');
  const room = {$: {udn: 'synthetic-room', name: 'Example'}, renderer: [{$: {udn: 'synthetic-physical'}}]};
  const topology = assigned => store.topology({zoneConfig: assigned ? {
    zones: [{zone: [{$: {udn: 'synthetic-zone'}, room: grouped ? [room, {$: {udn: 'synthetic-other'}}] : [room]}]}],
  } : {unassignedRooms: [{room: [room]}]}});
  topology(assigned);
  const calls = [];
  const observer = {devices: new Map(), kernel: {managerDisposer: {zoneManager: {
    connectRoomToZone: async (...args) => { calls.push(args); },
    dropRoomJob: () => assert.fail('No background repair'),
  }}}};
  const companion = new Companion(store, observer, {allowedRooms: () => ['synthetic-room'], timeoutMs});
  return {store, companion, observer, topology, calls};
}

test('catalog polling never creates an unassigned zone or requires physical source proof', () => {
  const f = fixture();
  for (let i = 0; i < 20; i++) assert.deepEqual(f.companion.catalog(), [
    {roomId: 'synthetic-room', assigned: false, endpoint: null, rendererIds: ['synthetic-physical'], spotifyVersion: 0},
  ]);
  assert.deepEqual(f.calls, []);
  assert.equal(f.companion.leases, undefined);
  assert.equal(f.companion.seen, undefined);
});

test('explicit demand uses the real pinned SDK manager shape, not an invented getManager API', async () => {
  const f = fixture();
  const kernel = new Raumkernel();
  assert.equal(typeof kernel.getManager, 'undefined');
  // Mirror the pinned init's manager creation without starting SSDP/network.
  kernel.managerDisposer = new ManagerDisposer();
  kernel.managerDisposer.createZoneManager();
  assert.equal(typeof kernel.managerDisposer.zoneManager.connectRoomToZone, 'function');
  kernel.managerDisposer.zoneManager.connectRoomToZone = async (...args) => f.calls.push(args);
  f.observer.kernel = kernel;
  await f.companion.createForPlay('synthetic-room');
  assert.deepEqual(f.calls, [['synthetic-room', '']]);
});

test('missing manager fails before any mutation or uncertainty latch, with no fallback API', async () => {
  const f = fixture();
  f.observer.kernel = {getManager: () => assert.fail('No invented API fallback')};
  await assert.rejects(f.companion.createForPlay('synthetic-room'), error => {
    assert.equal(error.message, 'renderer_manager_unavailable');
    assert.equal(error.origin, 'local_check');
    assert.equal(error.phase, 'preflight');
    assert.equal(error.requestAttempted, false);
    return true;
  });
  assert.equal(f.calls.length, 0);
  assert.equal(f.companion.pending.size, 0);
  assert.equal(f.companion.uncertain.size, 0);
});

test('assigned missing renderer, disappearance and address change never mutate zones', async () => {
  const f = fixture({assigned: true});
  assert.equal((await f.companion.createForPlay('synthetic-room')).endpoint, null);
  const device = {udn: () => 'synthetic-zone', upnpClient: {url: 'http://renderer.example.test:47365/description.xml'}};
  f.observer.devices.set('synthetic-zone', {device});
  assert.equal(f.companion.catalog()[0].endpoint.rendererId, 'synthetic-zone');
  device.upnpClient.url = 'http://replacement.example.test:47365/description.xml';
  assert.match(f.companion.catalog()[0].endpoint.descriptionUrl, /replacement/);
  f.observer.devices.clear();
  assert.equal(f.companion.catalog()[0].endpoint, null);
  assert.deepEqual(f.calls, []);
});

test('one explicit demand creates once; acknowledgement without topology is not retry permission', async () => {
  const f = fixture();
  assert.equal((await f.companion.createForPlay('synthetic-room')).assigned, false);
  await assert.rejects(f.companion.createForPlay('synthetic-room'), /creation_pending_or_uncertain/);
  f.companion.catalog();
  assert.deepEqual(f.calls, [['synthetic-room', '']]);
  f.topology(true);
  assert.equal((await f.companion.createForPlay('synthetic-room')).assigned, true);
  assert.equal(f.calls.length, 1);
});

test('failed and timed-out creation are never automatically retried', async () => {
  for (const operation of [() => Promise.reject(new Error('private network failure')), () => new Promise(() => {})]) {
    const f = fixture();
    f.observer.kernel.managerDisposer = {zoneManager: {connectRoomToZone: (...args) => {
      f.calls.push(args); return operation();
    }}};
    await assert.rejects(f.companion.createForPlay('synthetic-room'), /creation_pending_or_uncertain/);
    await assert.rejects(f.companion.createForPlay('synthetic-room'), /creation_pending_or_uncertain/);
    assert.equal(f.calls.length, 1);
  }
});

test('concurrent creation and changed host cannot acquire authority', async () => {
  const f = fixture();
  let resolve;
  f.observer.kernel.managerDisposer = {zoneManager: {connectRoomToZone: (...args) => {
    f.calls.push(args); return new Promise(r => { resolve = r; });
  }}};
  const first = f.companion.createForPlay('synthetic-room');
  await assert.rejects(f.companion.createForPlay('synthetic-room'), /creation_pending_or_uncertain/);
  f.store.hostFound('another-synthetic-host');
  f.topology(true);
  resolve();
  await assert.rejects(first, /topology_changed/);
  assert.equal(f.calls.length, 1);
});

test('stale topology, unknown rooms and grouped rooms refuse demand without mutation', async () => {
  const f = fixture();
  await assert.rejects(f.companion.createForPlay('unknown-room'), /room_not_configured/);
  f.store.hostLost();
  await assert.rejects(f.companion.createForPlay('synthetic-room'), /topology_unavailable/);
  const g = fixture({assigned: true, grouped: true});
  await assert.rejects(g.companion.createForPlay('synthetic-room'), /grouped_room_not_supported/);
  assert.equal(f.calls.length + g.calls.length, 0);
});

test('renderer identity and description origin are checked without SOAP', () => {
  const f = fixture({assigned: true});
  for (const url of ['https://example.test/device', 'http://user:secret@example.test/device', 'http://example.test/device?token=private']) {
    f.observer.devices.set('synthetic-zone', {device: {udn: () => 'synthetic-zone', upnpClient: {url}}});
    assert.equal(f.companion.catalog()[0].endpoint, null);
  }
  f.observer.devices.set('synthetic-zone', {device: {udn: () => 'wrong-zone', upnpClient: {url: 'http://example.test/device'}}});
  assert.equal(f.companion.catalog()[0].endpoint, null);
  assert.equal(f.calls.length, 0);
});

test('native source notifications and new Spotify observations increment evidence, not repeated polls', () => {
  const f = fixture();
  assert.equal(f.companion.catalog()[0].spotifyVersion, 0);
  f.store.observe('synthetic-physical', {AVTransportURI: 'spotify:synthetic-source'});
  const version = f.companion.catalog()[0].spotifyVersion;
  f.store.observe('synthetic-physical', {AVTransportURI: 'spotify:synthetic-source'});
  assert.equal(f.companion.catalog()[0].spotifyVersion, version);
  f.store.emit('nativeSpotify', 'synthetic-physical');
  assert.equal(f.companion.catalog()[0].spotifyVersion, version + 1);
  assert.equal(f.calls.length, 0);
});

test('companion HTTP exposes no session, binding-lease or playback-control endpoints', async t => {
  const f = fixture();
  const token = 's'.repeat(32);
  const server = createCompanionApi(f.companion, token);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  const headers = {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'};
  assert.equal((await fetch(base + '/v1/endpoints')).status, 401);
  assert.equal((await fetch(base + '/v1/endpoints', {headers})).status, 200);
  for (const path of ['/v1/control', '/v1/binding', '/v1/arbitration/evaluate']) {
    assert.equal((await fetch(base + path, {method: 'POST', headers, body: '{}'})).status, 404);
  }
  assert.equal((await fetch(base + '/v1/zone-for-play', {method: 'POST', headers,
    body: JSON.stringify({roomId: 'synthetic-room', selectionId: 'invented'})})).status, 400);
  assert.equal(f.calls.length, 0);
  assert.equal((await fetch(base + '/v1/zone-for-play', {method: 'POST', headers,
    body: JSON.stringify({roomId: 'synthetic-room'})})).status, 200);
  assert.equal(f.calls.length, 1);
});

test('explicit renderer demand distinguishes our check from renderer response and network failure', async t => {
  for (const kind of ['local', 'response', 'network', 'unknown']) {
    const f = fixture();
    const diagnostics = [];
    f.companion.diagnostic = row => diagnostics.push(row);
    if (kind === 'local') f.store.hostLost();
    else f.observer.kernel.managerDisposer = {zoneManager: {connectRoomToZone: async (...args) => {
      f.calls.push(args);
      throw Object.assign(new Error('synthetic-private-message'), kind === 'response'
        ? {rendererRequestOrigin: 'raumfeld_response', rendererRequestAttempted: true, rendererHttpStatus: 503}
        : kind === 'network'
          ? {rendererRequestOrigin: 'network', rendererRequestAttempted: true, rendererNetworkCode: 'ECONNREFUSED'}
          : {});
    }}};
    const server = createCompanionApi(f.companion, 's'.repeat(32));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const base = `http://127.0.0.1:${server.address().port}`;
    const headers = {Authorization: `Bearer ${'s'.repeat(32)}`, 'Content-Type': 'application/json'};
    const response = await fetch(base + '/v1/zone-for-play', {method: 'POST', headers,
      body: JSON.stringify({roomId: 'synthetic-room'})});
    assert.equal(response.status, 409);
    const body = await response.json();
    assert.equal(body.origin, {local: 'local_check', response: 'raumfeld_response', network: 'network', unknown: 'library_unknown'}[kind]);
    assert.equal(body.requestAttempted, kind === 'local' ? false : kind === 'unknown' ? 'unknown' : true);
    assert.equal(body.rendererHttpStatus, kind === 'response' ? 503 : undefined);
    assert.equal(body.networkCode, kind === 'network' ? 'ECONNREFUSED' : undefined);
    assert.equal(f.calls.length, kind === 'local' ? 0 : 1);
    assert.ok(!JSON.stringify({body, diagnostics}).includes('synthetic-private-message'));
    if (kind !== 'local') {
      const blocked = await fetch(base + '/v1/zone-for-play', {method: 'POST', headers,
        body: JSON.stringify({roomId: 'synthetic-room'})});
      const next = await blocked.json();
      assert.equal(next.origin, 'local_check');
      assert.equal(next.phase, 'uncertainty_block');
      assert.equal(next.requestAttempted, false);
      assert.equal(f.calls.length, 1);
    }
  }
});

test('background catalogs, observations and Spotify evidence never request a renderer', () => {
  const f = fixture();
  const diagnostics = [];
  f.companion.diagnostic = row => diagnostics.push(row);
  f.store.emit('nativeSpotify', 'synthetic-physical');
  f.store.observationFailed('synthetic-physical', {code: 'ECONNREFUSED'});
  for (let i = 0; i < 10; i++) f.companion.catalog();
  assert.equal(f.calls.length, 0);
  assert.equal(diagnostics.length, 0);
});

test('deadline and post-ack topology rejection retain different phases without repair', async () => {
  const timeout = fixture();
  timeout.observer.kernel.managerDisposer = {zoneManager: {connectRoomToZone: (...args) => {
    timeout.calls.push(args); return new Promise(() => {});
  }}};
  await assert.rejects(timeout.companion.createForPlay('synthetic-room'), error => {
    assert.equal(error.origin, 'deadline');
    assert.equal(error.phase, 'timeout');
    assert.equal(error.requestAttempted, 'unknown');
    return true;
  });
  await assert.rejects(timeout.companion.createForPlay('synthetic-room'), error => {
    assert.equal(error.phase, 'uncertainty_block');
    assert.equal(error.requestAttempted, false);
    return true;
  });
  assert.equal(timeout.calls.length, 1);
  const changed = fixture();
  changed.observer.kernel.managerDisposer = {zoneManager: {connectRoomToZone: async (...args) => {
    changed.calls.push(args);
    changed.store.hostFound('changed-synthetic-host');
    changed.topology(false);
  }}};
  await assert.rejects(changed.companion.createForPlay('synthetic-room'), error => {
    assert.equal(error.origin, 'local_check');
    assert.equal(error.phase, 'postflight');
    assert.equal(error.requestAttempted, true);
    return true;
  });
  assert.equal(changed.calls.length, 1);
});
