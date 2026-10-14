'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {Companion} = require('../raumkernel/companion');

function fixture() {
  const store = new StateStore();
  store.hostFound('synthetic-host');
  const room = id => ({$: {udn: id, name: 'Synthetic'}, renderer: [{$: {udn: id + '-physical'}}]});
  const topology = assigned => store.topology({zoneConfig: {zones: assigned
    ? [{zone: [{$: {udn: 'synthetic-zone-a'}, room: [room('a')]}]}] : [],
    unassignedRooms: [{room: assigned ? [room('b')] : [room('a'), room('b')]}]}});
  topology(false);
  const logs = [], calls = [];
  const observer = {devices: new Map(), kernel: {managerDisposer: {zoneManager: {
    connectRoomToZone: async (...args) => { calls.push(args); topology(true); },
  }}}};
  const companion = new Companion(store, observer, {allowedRooms: () => ['a', 'b'], diagnostic: r => logs.push(r)});
  const observe = (id, uri, transport, refresh = true) => store.observe(id,
    {AVTransportURI: uri, CurrentTransportState: transport}, {refresh, sourceRead: 'queried_last_change'});
  return {store, companion, topology, logs, calls, observe};
}

test('both notification orders reproduce paused-source projection without revoking explicit activation', async () => {
  for (const reverse of [false, true]) {
    const f = fixture();
    f.observe('a-physical', 'spotify:synthetic-existing', 'PAUSED_PLAYBACK');
    const baseline = f.companion.lookup('a').spotifyVersion;
    await f.companion.createForPlay('a');
    for (const id of reverse ? ['synthetic-zone-a', 'a-physical'] : ['a-physical', 'synthetic-zone-a']) {
      for (let i = 0; i < 3; i++) {
        f.store.emit('nativeSpotify', id);
        f.store.emit('source', id, 'spotify:synthetic-existing');
        f.observe(id, 'spotify:synthetic-existing', 'PAUSED_PLAYBACK', false);
        f.observe(id, 'spotify:synthetic-existing', 'PAUSED_PLAYBACK');
      }
    }
    assert.equal(f.companion.lookup('a').spotifyVersion, baseline);
    assert.equal(f.calls.length, 1);
    assert.ok(f.logs.some(r => r.freshness === 'cached_event' && r.incremented === false));
    assert.ok(!JSON.stringify(f.logs).includes('synthetic-existing'));
  }
});

test('new fresh Spotify resume or source replacement increments once; cached receipt does not', () => {
  const f = fixture();
  f.observe('a-physical', 'spotify:synthetic-existing', 'PAUSED_PLAYBACK');
  f.observe('a-physical', 'spotify:synthetic-existing', 'PLAYING', false);
  assert.equal(f.companion.lookup('a').spotifyVersion, 0);
  f.observe('a-physical', 'spotify:synthetic-existing', 'PLAYING');
  f.observe('a-physical', 'spotify:synthetic-existing', 'PLAYING');
  assert.equal(f.companion.lookup('a').spotifyVersion, 1);
  assert.equal(f.companion.lookup('a').spotifyEvidence.origin, 'fresh_read');
  f.observe('a-physical', 'https://synthetic.example.test/qobuz', 'PLAYING');
  f.observe('a-physical', 'spotify:synthetic-new', 'PLAYING');
  assert.equal(f.companion.lookup('a').spotifyVersion, 2);
});

test('room A source evidence never increments room B; inventory preserves logical rooms', () => {
  const f = fixture();
  f.observe('a-physical', 'spotify:synthetic-existing', 'PAUSED_PLAYBACK');
  f.observe('b-physical', 'spotify:synthetic-other', 'PAUSED_PLAYBACK');
  f.observe('a-physical', 'spotify:synthetic-existing', 'PLAYING');
  assert.equal(f.companion.lookup('a').spotifyVersion, 1);
  assert.equal(f.companion.lookup('b').spotifyVersion, 0);
  assert.equal(f.companion.inventory().length, 2);
  assert.equal(f.calls.length, 0);
});
