'use strict';

const list = value => Array.isArray(value) ? value : [];
const {EventEmitter} = require('node:events');

// Parse the actual xml2js shape emitted by node-raumkernel. Replace whole
// snapshots so removed rooms/zones do not survive a topology update.
function topologyFrom(config) {
  if (!config?.zoneConfig || typeof config.zoneConfig !== 'object') {
    throw new Error('Invalid zone configuration');
  }
  const rooms = [], zones = [], seen = new Set();
  function addRoom(room, zoneId) {
    const id = room?.$?.udn;
    if (!id || seen.has(id)) throw new Error('Missing or duplicate room UDN');
    seen.add(id);
    const rendererIds = list(room.renderer).map(r => r?.$?.udn);
    if (rendererIds.some(id => !id)) throw new Error('Missing renderer UDN');
    rooms.push({id, name: room.$.name || id, zoneId, rendererIds});
  }
  for (const root of list(config.zoneConfig.zones)) {
    for (const zone of list(root.zone)) {
      const id = zone?.$?.udn;
      if (!id || zones.some(z => z.id === id)) throw new Error('Invalid zone UDN');
      zones.push({id, roomIds: list(zone.room).map(r => r?.$?.udn)});
      list(zone.room).forEach(r => addRoom(r, id));
    }
  }
  for (const root of list(config.zoneConfig.unassignedRooms)) {
    list(root.room).forEach(r => addRoom(r, null));
  }
  return {rooms, zones};
}

function classify(state) {
  const uris = [state.AVTransportURI, state.CurrentTrackURI, state.TrackURI]
    .filter(v => typeof v === 'string').map(v => v.toLowerCase());
  // Spotify markers match upstream ha-raumkernel's source detection. Never
  // infer Qobuz ownership from a URL: native Qobuz is also an external source.
  if (uris.some(u => u.startsWith('spotify:') || u.includes('spotifyconnect'))) return 'spotify';
  if (uris.some(u => u.length)) return 'external';
  return 'unknown';
}

class StateStore extends EventEmitter {
  constructor({now = Date.now, staleMs = 30000} = {}) {
    super();
    this.now = now;
    this.staleMs = staleMs;
    this.host = null;
    this.topologyAt = null;
    this.rooms = [];
    this.zones = [];
    this.renderers = new Map();
    this.revision = 0;
    this.raw = new Map();
  }
  hostFound(host) {
    if (host !== this.host) this.hostLost();
    this.host = host;
    this.revision++;
  }
  hostLost() {
    this.host = null;
    this.topologyAt = null;
    this.renderers.clear();
    this.raw.clear();
    this.revision++;
    this.emit('lost');
  }
  topology(config) {
    const next = topologyFrom(config);
    this.rooms = next.rooms;
    this.zones = next.zones;
    this.topologyAt = this.now();
    this.revision++;
    this.emit('topology');
  }
  observe(id, state) {
    if (!this.host) return;
    const previous = this.renderers.get(id);
    const detected = classify(state);
    // Empty URIs occur during transitions: retain Spotify evidence until a
    // positive replacement source is observed. Paused Spotify remains Spotify.
    const source = detected === 'unknown' && previous?.source === 'spotify' ? 'spotify' : detected;
    this.renderers.set(id, {
      id, source, observedAt: this.now(),
      transport: String(state.TransportState || state.CurrentTransportState || 'UNKNOWN'),
      volume: Number.isFinite(Number(state.Volume)) && state.Volume !== '' && state.Volume != null
        ? Math.max(0, Math.min(100, Number(state.Volume))) : null,
      positionMs: timeMs(state.RelTime), durationMs: timeMs(state.TrackDuration),
    });
    this.raw.set(id, {...state});
    this.revision++;
    this.emit('observation', id, state);
  }
  removed(id) { this.renderers.delete(id); this.raw.delete(id); this.revision++; this.emit('removed', id); }
  snapshot() {
    const at = this.now();
    const topologyFresh = !!this.host && this.topologyAt !== null && at - this.topologyAt <= this.staleMs;
    const renderers = [...this.renderers.values()].map(r => ({...r,
      fresh: topologyFresh && at - r.observedAt <= this.staleMs}));
    const byId = new Map(renderers.map(r => [r.id, r]));
    const rooms = this.rooms.map(room => {
      const ids = [...room.rendererIds, ...(room.zoneId ? [room.zoneId] : [])];
      const evidence = ids.map(id => byId.get(id));
      const fresh = topologyFresh && room.rendererIds.length > 0 &&
        room.rendererIds.every(id => byId.get(id)?.fresh) &&
        (!room.zoneId || !!byId.get(room.zoneId)?.fresh);
      const source = evidence.some(r => r?.source === 'spotify') ? 'spotify'
        : evidence.length && evidence.every(r => r?.source === 'external') ? 'external' : 'unknown';
      return {...room, rendererIds: [...room.rendererIds], source, fresh, protected: true};
    });
    return {apiVersion: '1', revision: this.revision, observedAt: at,
      host: this.host, topologyFresh, topologyAt: this.topologyAt,
      capabilities: {discovery: true, playback: false, qobuzConnect: false},
      rooms, zones: this.zones.map(z => ({...z, roomIds: [...z.roomIds]})), renderers};
  }
}

function timeMs(value) {
  if (typeof value !== 'string' || !/^\d+:\d{2}:\d{2}(\.\d+)?$/.test(value)) return 0;
  const parts = value.split(':').map(Number);
  return Math.round((parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000);
}
module.exports = {StateStore, topologyFrom, classify, timeMs};
