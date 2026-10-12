'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {BindingService} = require('../raumkernel/binding');
const {createApi} = require('../raumkernel/api');

function fixture({assigned = true, spotify = false} = {}) {
  let now = 1000, commands = 0;
  const store = new StateStore({now: () => now});
  store.hostFound('192.0.2.1');
  const config = zone => ({zoneConfig: {
    zones: [{zone: zone ? [{$: {udn: zone}, room: [{$: {udn: 'room'}, renderer: [{$: {udn: 'physical'}}]}]}] : []}],
    unassignedRooms: [{room: zone ? [] : [{$: {udn: 'room'}, renderer: [{$: {udn: 'physical'}}]}]}]}});
  store.topology(config(assigned ? 'zone' : null));
  store.observe('physical', {AVTransportURI: spotify ? 'spotify:synthetic' : 'https://example.test/synthetic'});
  const observer = {devices: new Map([['zone', {device: {udn: () => 'zone',
    upnpClient: {url: 'http://192.0.2.1:55001/device.xml'}}}]]),
    kernel: {getManager: () => ({zoneManager: {connectRoomToZone: async (room, zone) => {
      commands++; assert.equal(room, 'room'); assert.equal(zone, ''); store.topology(config('zone'));
    }}})}};
  const binding = new BindingService(store, observer, {allowedRooms: () => ['room'], now: () => now});
  return {store, observer, binding, config, commands: () => commands, advance: ms => { now += ms; }};
}
const request = (binding, action, token, extra = {}) => binding.dispatch({roomId: 'room', action, token, ...extra});
const select = binding => request(binding, 'select', undefined, {selectionId: 'a'.repeat(64)});

test('idle discovery and missing assigned renderer do not repair zones', async () => {
  const f = fixture();
  f.observer.devices.clear();
  const {token} = await select(f.binding);
  assert.equal((await request(f.binding, 'lookup', token)).binding, null);
  assert.equal((await request(f.binding, 'prepare', token)).binding, null);
  assert.equal(f.commands(), 0);
});
test('only an admitted Play prepare creates a truly unassigned zone, once', async () => {
  const f = fixture({assigned: false});
  const {token} = await select(f.binding);
  assert.equal(f.commands(), 0);
  assert.equal((await request(f.binding, 'lookup', token)).binding, null);
  await request(f.binding, 'prepare', token);
  assert.equal((await request(f.binding, 'lookup', token)).binding.rendererId, 'zone');
  await request(f.binding, 'prepare', token);
  assert.equal(f.commands(), 1);
});
test('same renderer address changes only its read-only binding', async () => {
  const f = fixture(); const {token} = await select(f.binding);
  f.observer.devices.get('zone').device.upnpClient.url = 'http://192.0.2.1:55002/device.xml';
  assert.match((await request(f.binding, 'lookup', token)).binding.descriptionUrl, /55002/);
  assert.equal(f.commands(), 0);
});
test('initial Spotify baseline is bounded; new native evidence revokes during loading', async () => {
  const f = fixture({spotify: true}); const {token} = await select(f.binding);
  await request(f.binding, 'load', token);
  await request(f.binding, 'loaded_play', token);
  f.store.emit('nativeSpotify', 'physical');
  await assert.rejects(request(f.binding, 'loaded_play', token), /ownership_lost/);
  f.store.observe('physical', {AVTransportURI: 'https://example.test/synthetic'});
  await assert.rejects(request(f.binding, 'lookup', token), /ownership_lost/);
  assert.equal(f.commands(), 0);
});
test('old release cannot revoke a newer selection; replay cannot replace it', async () => {
  const f = fixture(); const first = await select(f.binding);
  const second = await request(f.binding, 'select', undefined, {selectionId: 'b'.repeat(64)});
  await request(f.binding, 'release', first.token);
  await assert.rejects(select(f.binding), /selection_already_used/);
  assert.ok((await request(f.binding, 'lookup', second.token)).binding);
});
test('topology mismatch and stale evidence are terminal; pending load expires', async () => {
  const f = fixture(); const {token} = await select(f.binding);
  f.store.topology(f.config('other-zone'));
  await assert.rejects(request(f.binding, 'lookup', token), /ownership_lost/);
  const g = fixture(); const next = await select(g.binding);
  g.advance(10001);
  await assert.rejects(request(g.binding, 'load', next.token), /ownership_lost/);
  const h = fixture(); const last = await select(h.binding);
  h.advance(30001);
  await assert.rejects(request(h.binding, 'lookup', last.token), /ownership_lost/);
});
test('uncertain zone creation is single shot and cannot authorize later playback', async () => {
  const f = fixture({assigned: false});
  let calls = 0;
  f.observer.kernel.getManager = () => ({zoneManager: {connectRoomToZone: async () => {
    calls++; throw new Error('synthetic reset');
  }}});
  const {token} = await select(f.binding);
  await assert.rejects(request(f.binding, 'prepare', token), /zone_unavailable/);
  await assert.rejects(request(f.binding, 'prepare', token), /ownership_lost/);
  assert.equal(calls, 1);
});
test('late creation failure never releases a newer selection', async () => {
  const f = fixture({assigned: false}); let reject;
  f.observer.kernel.getManager = () => ({zoneManager: {connectRoomToZone: () =>
    new Promise((_, r) => { reject = r; })}});
  const first = await select(f.binding);
  const pending = request(f.binding, 'prepare', first.token);
  const newer = await request(f.binding, 'select', undefined, {selectionId: 'b'.repeat(64)});
  reject(new Error('synthetic late reset'));
  await assert.rejects(pending, /zone_unavailable/);
  assert.equal((await request(f.binding, 'lookup', newer.token)).binding, null);
});
test('physical-first forwarding is incomplete, not terminal; foreign physical sources are terminal', async () => {
  const f = fixture(); const {token} = await select(f.binding);
  f.store.observe('physical', {AVTransportURI: 'http://192.0.2.1:55001/zone/room/physical/stream?punch=1'});
  assert.equal((await request(f.binding, 'guard', token)).physicalReady, true);
  // No virtual observation was needed for the shape check. The final Python
  // transport gate must separately read the exact virtual URI before sending.
  f.store.emit('source', 'physical', 'https://example.test/competing-source');
  await assert.rejects(request(f.binding, 'guard', token), /ownership_lost/);
});
test('unknown source cannot confirm physical ownership; wrong forwarding identity is terminal', async () => {
  const f = fixture(); const {token} = await select(f.binding);
  f.store.observe('physical', {AVTransportURI: ''});
  assert.equal((await request(f.binding, 'lookup', token)).physicalReady, false);
  assert.equal((await request(f.binding, 'guard', token)).physicalReady, false);
  const g = fixture(); const next = await select(g.binding);
  g.store.emit('source', 'physical', 'http://192.0.2.1:55001/zone/other-room/physical/stream?punch=1');
  await assert.rejects(request(g.binding, 'lookup', next.token), /ownership_lost/);
});
test('paused idle is explicitly complete physical evidence, never forwarding ownership or an event-only proof', async () => {
  const f = fixture(); const {token} = await select(f.binding);
  f.store.observe('physical', {AVTransportURI: '', CurrentTransportState: 'NO_MEDIA_PRESENT'});
  const idle = await request(f.binding, 'guard', token);
  assert.equal(idle.physicalReady, false);
  assert.equal(idle.physicalIdle, true);
  assert.equal(idle.physicalReads[0], 2);
  f.store.observe('physical', {AVTransportURI: '', CurrentTransportState: 'NO_MEDIA_PRESENT'}, {refresh: false});
  assert.equal((await request(f.binding, 'guard', token)).physicalIdle, false);
});
test('binding API requires authentication/strict input and never exposes custom control', async t => {
  const f = fixture(); const apiToken = 'synthetic-api-token'.repeat(3);
  const server = createApi(f.store, {token: apiToken, binding: f.binding});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const url = `http://127.0.0.1:${server.address().port}`;
  const headers = {Authorization: `Bearer ${apiToken}`, 'Content-Type': 'application/json'};
  assert.equal((await fetch(url + '/v1/binding', {method: 'POST'})).status, 401);
  assert.equal((await fetch(url + '/v1/binding', {method: 'POST', headers, body: '{}'})).status, 400);
  assert.equal((await fetch(url + '/v1/control', {method: 'POST', headers, body: '{}'})).status, 503);
  const response = await fetch(url + '/v1/binding', {method: 'POST', headers,
    body: JSON.stringify({roomId: 'room', action: 'select', selectionId: 'c'.repeat(64)})});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).apiVersion, '1');
});
