'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const {StateStore} = require('../raumkernel/state');
const {RaumkernelObserver} = require('../raumkernel/adapter');
const xml = fs.readFileSync(path.join(__dirname, 'fixtures/zones.xml'), 'utf8');

function setup(t, options = {}) {
  const calls = [];
  const kernel = new EventEmitter();
  kernel.init = () => kernel.emit('systemHostFound', '192.0.2.1');
  const store = new StateStore();
  const adapter = new RaumkernelObserver(kernel, store, {
    pollMs: 999999, fetchImpl: async () => ({ok: true, text: async () => xml}), ...options,
  });
  const device = {
    udn: () => 'uuid:physical-3',
    callAction: async (service, action) => {
      calls.push(action);
      assert.equal(action, 'GetMediaInfo');
      return {CurrentURI: 'spotify://playback'};
    },
    getTransportInfo: async () => ({CurrentTransportState: 'PAUSED_PLAYBACK'}),
    getPositionInfo: async () => ({TrackURI: ''}),
    stop: () => assert.fail('Must not stop Spotify'),
    loadUri: () => assert.fail('Must not load audio'),
  };
  adapter.start();
  kernel.emit('mediaRendererRaumfeldAdded', device.udn(), device);
  t.after(() => adapter.stop());
  return {kernel, store, adapter, device, calls};
}

test('real read action shapes detect Spotify and polling recovers missed topology', async t => {
  const {store, adapter, calls} = setup(t);
  await new Promise(resolve => setImmediate(resolve));
  await adapter.poll();
  assert.equal(store.snapshot().rooms[2].source, 'spotify');
  assert.equal(store.snapshot().rooms[2].fresh, true);
  assert.ok(calls.length > 0);
});

test('late poll cannot resurrect devices after host loss', async t => {
  let resolve;
  const response = new Promise(r => { resolve = r; });
  const {kernel, store} = setup(t, {fetchImpl: () => response});
  kernel.emit('systemHostLost');
  resolve({ok: true, text: async () => xml});
  await new Promise(r => setImmediate(r));
  assert.equal(store.snapshot().host, null);
  assert.equal(store.snapshot().topologyFresh, false);
  assert.equal(store.snapshot().renderers.length, 0);
});

test('poll timeout and malformed topology invalidate readiness without mutations', async t => {
  const {store, adapter} = setup(t);
  await new Promise(r => setImmediate(r));
  adapter.fetch = async () => { throw new Error('Offline'); };
  await adapter.poll();
  assert.equal(store.snapshot().topologyFresh, false);
});

test('volume events cannot refresh source observations', async t => {
  const {store, adapter, kernel, device} = setup(t);
  await new Promise(r => setImmediate(r));
  await adapter.poll();
  store.renderers.get(device.udn()).observedAt = 1;
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://playback', Volume: 5});
  assert.equal(store.renderers.get(device.udn()).observedAt, 1);
  assert.equal(store.snapshot().rooms[2].fresh, false);
});

test('upstream-specific removal events invalidate physical and virtual renderers', async t => {
  const {store, adapter, kernel, device} = setup(t);
  await new Promise(r => setImmediate(r));
  await adapter.poll();
  kernel.emit('mediaRendererRaumfeldRemoved', device.udn(), 'Office');
  assert.equal(store.snapshot().rooms[2].fresh, false);
  assert.equal(adapter.devices.has(device.udn()), false);
  const virtual = {...device, udn: () => 'uuid:zone-1'};
  kernel.emit('mediaRendererRaumfeldVirtualAdded', virtual.udn(), virtual);
  await adapter.poll();
  assert.equal(adapter.devices.has(virtual.udn()), true);
  kernel.emit('mediaRendererRaumfeldVirtualRemoved', virtual.udn(), 'Zone');
  assert.equal(store.renderers.has(virtual.udn()), false);
  assert.equal(adapter.devices.has(virtual.udn()), false);
});

test('native Spotify event defeats an older in-flight non-Spotify poll', async t => {
  const {store, adapter, kernel, device} = setup(t);
  await new Promise(r => setImmediate(r));
  let release;
  device.callAction = () => new Promise(resolve => { release = resolve; });
  const pending = adapter.poll();
  await new Promise(r => setImmediate(r));
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://playback'});
  release({CurrentURI: 'https://example.test/old-track'});
  await pending;
  assert.equal(store.snapshot().rooms[2].source, 'spotify');
});
