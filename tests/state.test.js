'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {parseStringPromise} = require('xml2js');
const {StateStore, classify, topologyFrom} = require('../raumkernel/state');
const {evaluate} = require('../raumkernel/arbitration');
const Ajv = require('ajv');
const schema = require('../shared/api.schema.json');
const fixture = fs.readFileSync(path.join(__dirname, 'fixtures/zones.xml'), 'utf8');

async function ready() {
  let now = 10000;
  const store = new StateStore({now: () => now});
  store.hostFound('192.0.2.1');
  store.topology(await parseStringPromise(fixture));
  for (const id of ['uuid:physical-1', 'uuid:physical-2', 'uuid:physical-3', 'uuid:zone-1']) {
    store.observe(id, {AVTransportURI: 'http://example.test/audio', TransportState: 'PLAYING'});
  }
  return {store, advance: ms => now += ms};
}

test('normalizes grouped and unassigned rooms from actual upstream XML shape', async () => {
  const {store} = await ready();
  const state = store.snapshot();
  assert.equal(state.rooms.length, 3);
  assert.equal(state.zones.length, 1);
  assert.equal(state.rooms[2].zoneId, null);
  assert.equal(state.rooms[0].id, 'uuid:room-1');
  const validate = new Ajv().compile(schema);
  assert.ok(validate(state), JSON.stringify(validate.errors));
});

test('Spotify physical renderer protects room even when zone reports another source', async () => {
  const {store} = await ready();
  store.observe('uuid:physical-1', {AVTransportURI: 'spotify://playback', TransportState: 'PAUSED_PLAYBACK'});
  const state = store.snapshot();
  assert.equal(state.rooms[0].source, 'spotify');
  for (const intent of ['repair', 'automatic_takeover']) {
    assert.equal(evaluate(state, {roomId: 'uuid:room-1', intent}).reason, 'spotify_protected');
  }
  const explicit = evaluate(state, {roomId: 'uuid:room-1', intent: 'explicit_takeover'});
  assert.equal(explicit.policyAllowed, true);
  assert.equal(explicit.executable, false);
  assert.equal(evaluate(state, {roomId: 'uuid:room-1', intent: 'repair'}).policyAllowed, false);
});

test('unassigned Spotify room is observed without creating a zone', async () => {
  const {store} = await ready();
  store.observe('uuid:physical-3', {AVTransportURI: 'raumfeld:spotifyconnect'});
  const room = store.snapshot().rooms[2];
  assert.equal(room.source, 'spotify');
  assert.equal(room.zoneId, null);
  assert.equal(room.fresh, true);
});

test('unknown, stale, lost host, removed renderer and unknown room fail closed', async () => {
  const {store, advance} = await ready();
  const check = id => evaluate(store.snapshot(), {roomId: id, intent: 'explicit_takeover'});
  assert.equal(check('missing').policyAllowed, false);
  store.removed('uuid:physical-1');
  assert.equal(check('uuid:room-1').reason, 'state_unavailable');
  advance(30001);
  assert.equal(check('uuid:room-2').policyAllowed, false);
  store.hostLost();
  assert.equal(store.snapshot().topologyFresh, false);
  assert.equal(check('uuid:room-2').policyAllowed, false);
  store.hostFound('192.0.2.1');
  assert.equal(store.snapshot().topologyFresh, false);
});

test('empty URI retains Spotify; positive replacement clears it; Qobuz URL is external', async () => {
  const {store} = await ready();
  store.observe('uuid:physical-3', {AVTransportURI: 'SPOTIFY://playback'});
  store.observe('uuid:physical-3', {AVTransportURI: ''});
  assert.equal(store.snapshot().rooms[2].source, 'spotify');
  store.observe('uuid:physical-3', {AVTransportURI: 'https://qobuz.com/audio'});
  assert.equal(store.snapshot().rooms[2].source, 'external');
  assert.equal(classify({TransportState: 'STOPPED'}), 'unknown');
});

test('topology replacement removes rooms, changes zones, preserves stable room identity', async () => {
  const {store} = await ready();
  store.topology(await parseStringPromise('<zoneConfig><unassignedRooms><room udn="uuid:room-1" name="Renamed"><renderer udn="uuid:physical-1"/></room></unassignedRooms></zoneConfig>'));
  assert.equal(store.snapshot().rooms.length, 1);
  assert.equal(store.snapshot().rooms[0].id, 'uuid:room-1');
  assert.equal(store.snapshot().rooms[0].name, 'Renamed');
  assert.equal(store.snapshot().zones.length, 0);
  assert.throws(() => topologyFrom({bad: true}));
});

test('snapshots redact media URLs and do not expose mutable internal arrays', async () => {
  const {store} = await ready();
  const state = store.snapshot();
  assert.ok(!JSON.stringify(state).includes('example.test'));
  state.rooms[0].rendererIds.length = 0;
  assert.equal(store.snapshot().rooms[0].rendererIds.length, 1);
});
