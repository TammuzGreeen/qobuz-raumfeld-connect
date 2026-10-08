'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const {StateStore} = require('../raumkernel/state');
const {RaumkernelObserver, sourceInfo} = require('../raumkernel/adapter');
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
    callAction: async (service, action, params) => {
      calls.push(action);
      assert.equal(service, 'AVTransport');
      assert.deepEqual(params, {InstanceID: 0});
      if (action === 'GetMediaInfo') return {CurrentURI: 'spotify://playback'};
      if (action === 'GetTransportInfo') return {CurrentTransportState: 'PAUSED_PLAYBACK'};
      if (action === 'GetPositionInfo') return {TrackURI: ''};
      assert.fail('Unexpected action');
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
  const original = device.callAction;
  device.callAction = (service, action, params) => action === 'GetMediaInfo'
    ? new Promise(resolve => { release = resolve; }) : original(service, action, params);
  const pending = adapter.poll();
  await new Promise(r => setImmediate(r));
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://playback'});
  release({CurrentURI: 'https://example.test/old-track'});
  await pending;
  assert.equal(store.snapshot().rooms[2].source, 'spotify');
});

function lastChangeResponse(uri = 'spotify://playback', instance = '0') {
  const event = `<Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/"><InstanceID val="${instance}"><AVTransportURI val="${uri}"/></InstanceID></Event>`;
  const escaped = event.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  return `<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:QueryStateVariableResponse xmlns:u="urn:schemas-upnp-org:control-1-0"><LastChange>${escaped}</LastChange></u:QueryStateVariableResponse></s:Body></s:Envelope>`;
}

function physicalDevice(device) {
  const original = device.callAction;
  device.upnpClient = {deviceDescription: {services: {
    AVTransport: {serviceType: 'urn:schemas-upnp-org:service:AVTransport:1',
      controlURL: 'http://192.0.2.10/TransportControl'},
  }}};
  device.callAction = async (service, action, params) => {
    if (action === 'GetMediaInfo') throw Object.assign(new Error('Not implemented'), {code: 'ENOACTION'});
    // Physical position responses contain no TrackURI and require InstanceID.
    if (action === 'GetPositionInfo') {
      assert.deepEqual(params, {InstanceID: 0});
      return {RelTime: '0:00:01', TrackDuration: '0:03:00'};
    }
    return original(service, action, params);
  };
}

test('physical Raumfeld without GetMediaInfo becomes fresh only from a new LastChange query', async t => {
  const {store, adapter, device, kernel} = setup(t);
  await new Promise(r => setImmediate(r));
  physicalDevice(device);
  store.renderers.clear();
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://cached'});
  assert.equal(store.snapshot().rooms[2].fresh, false);
  let queries = 0;
  adapter.fetch = async (url, options) => {
    if (url.endsWith('/getZones')) return {ok: true, text: async () => xml};
    queries++;
    assert.equal(url, 'http://192.0.2.10/TransportControl');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.SOAPACTION, '"urn:schemas-upnp-org:control-1-0#QueryStateVariable"');
    assert.ok(options.body.includes('<varName>LastChange</varName>'));
    return {ok: true, text: async () => lastChangeResponse()};
  };
  await adapter.poll();
  const room = store.snapshot().rooms[2];
  assert.equal(room.fresh, true);
  assert.equal(room.source, 'spotify');
  assert.deepEqual(room.unavailableReasons, []);
  assert.equal(queries, 1);
});

test('failed LastChange queries never renew freshness or clear Spotify, and expose safe diagnostics', async t => {
  const {store, adapter, device, kernel} = setup(t);
  await new Promise(r => setImmediate(r));
  physicalDevice(device);
  store.renderers.get(device.udn()).observedAt = 1;
  adapter.fetch = async url => url.endsWith('/getZones')
    ? {ok: true, text: async () => xml}
    : {ok: false, text: async () => 'private SOAP body'};
  await adapter.poll();
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://private'});
  const state = store.snapshot();
  assert.equal(state.rooms[2].fresh, false);
  assert.equal(state.rooms[2].source, 'spotify');
  assert.deepEqual(state.rooms[2].unavailableReasons, ['physical_observation_stale']);
  assert.equal(state.observationErrors[0].action, 'QueryLastChange');
  assert.equal(JSON.stringify(state).includes('private'), false);
});

test('LastChange rejects malformed, absent, duplicate or wrong-instance URI evidence', async () => {
  const device = {callAction: async () => { throw {code: 'ENOACTION'}; }};
  physicalDevice(device);
  for (const text of ['bad XML', lastChangeResponse('', '1'),
    lastChangeResponse().replaceAll('AVTransportURI', 'OtherField'),
    lastChangeResponse().replace('&lt;AVTransportURI', '&lt;AVTransportURI val="duplicate"/&gt;&lt;AVTransportURI')]) {
    await assert.rejects(sourceInfo(device, async () => ({ok: true, text: async () => text}), 100),
      {observationAction: 'QueryLastChange'});
  }
  assert.deepEqual(await sourceInfo(device, async () => ({ok: true, text: async () => lastChangeResponse('')}), 100),
    {AVTransportURI: ''});
});

test('GetMediaInfo network failures do not fall back to weaker or cached evidence', async () => {
  const device = {callAction: async () => { throw {code: 'ECONNRESET'}; }};
  await assert.rejects(sourceInfo(device, () => assert.fail('No fallback on network failure'), 100),
    {observationAction: 'GetMediaInfo', code: 'ECONNRESET'});
});

test('Spotify event also defeats an older in-flight LastChange response', async t => {
  const {store, adapter, device, kernel} = setup(t);
  await new Promise(r => setImmediate(r));
  physicalDevice(device);
  let release;
  adapter.fetch = async url => url.endsWith('/getZones') ? {ok: true, text: async () => xml}
    : new Promise(resolve => { release = resolve; });
  const pending = adapter.poll();
  await new Promise(r => setImmediate(r));
  kernel.emit('rendererStateChanged', device, {AVTransportURI: 'spotify://new'});
  release({ok: true, text: async () => lastChangeResponse('http://192.0.2.10/old')});
  await pending;
  assert.equal(store.snapshot().rooms[2].source, 'spotify');
  assert.equal(store.raw.get(device.udn()).AVTransportURI, 'spotify://new');
});
