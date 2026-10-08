'use strict';

const {randomBytes} = require('node:crypto');
const {forwardingShape} = require('./forwarding');
const uris = raw => [raw?.AVTransportURI, raw?.TrackURI, raw?.CurrentTrackURI]
  .filter(v => typeof v === 'string' && v);

// Direct mode grants selection authority, not transport ownership. Python's
// upstream DLNA source reads decide virtual transport ownership. No SOAP here.
class BindingService {
  constructor(store, observer, {allowedRooms = () => [], now = Date.now, ttlMs = 30000,
    handoffMs = 10000, timeoutMs = 10000} = {}) {
    Object.assign(this, {store, observer, allowedRooms, now, ttlMs, handoffMs, timeoutMs});
    this.leases = new Map();
    this.seen = new Map();
    store.on('lost', () => this.leases.clear());
    store.on('source', (id, uri) => {
      if (typeof uri === 'string' && /spotify:|spotifyconnect/i.test(uri)) this.revokeRenderer(id);
      else this.physicalEvent(id, [uri]);
    });
    store.on('nativeSpotify', id => this.revokeRenderer(id));
    store.on('observation', (id, raw) => this.physicalEvent(id, uris(raw)));
    store.on('topology', () => {
      for (const [roomId, lease] of this.leases) {
        const room = this.store.rooms.find(r => r.id === roomId);
        if (lease.creating && !lease.zoneId && room?.zoneId &&
            JSON.stringify(room.rendererIds) === JSON.stringify(lease.physical) &&
            this.store.zones.find(z => z.id === room.zoneId)?.roomIds.length === 1) {
          lease.zoneId = room.zoneId;
          lease.creating = false;
        }
        try { this.room(roomId, lease); } catch { this.leases.delete(roomId); }
      }
    });
  }
  fail(message, status = 409) { throw Object.assign(new Error(message), {status}); }
  revokeRenderer(id) {
    for (const [roomId, lease] of this.leases) {
      if (lease.physical.includes(id) || lease.zoneId === id) this.leases.delete(roomId);
    }
  }
  physicalEvent(id, values) {
    for (const [roomId, lease] of this.leases) {
      if (!lease.physical.includes(id)) continue;
      const room = this.store.rooms.find(r => r.id === roomId);
      if (values.filter(Boolean).some(value => !forwardingShape(this.store, room || {}, id, value) &&
          !(lease.initialUntil > this.now() && lease.baseline.get(id)?.includes(value)))) {
        this.leases.delete(roomId);
      }
    }
  }
  room(roomId, lease = null) {
    const allowed = this.allowedRooms();
    if (allowed.length !== 1 || allowed[0] !== roomId) this.fail('one_room_required');
    const state = this.store.snapshot();
    if (!state.topologyFresh) this.fail('state_unavailable');
    const room = state.rooms.find(r => r.id === roomId);
    if (!room || !room.rendererIds.length) this.fail('room_not_found');
    if (room.zoneId && state.zones.find(z => z.id === room.zoneId)?.roomIds.length !== 1) {
      this.fail('grouped_room_not_supported');
    }
    if (lease && (JSON.stringify(room.rendererIds) !== JSON.stringify(lease.physical) ||
        room.zoneId !== lease.zoneId)) this.fail('room_membership_changed');
    return {room, state};
  }
  validate(roomId, token, {initial = false} = {}) {
    const lease = this.leases.get(roomId);
    if (!lease || lease.token !== token || lease.expires <= this.now()) this.fail('ownership_lost');
    try {
      const {room, state} = this.room(roomId, lease);
      for (const id of room.rendererIds) {
        const renderer = state.renderers.find(r => r.id === id);
        if (!renderer?.fresh) this.fail('state_unavailable');
        if (renderer.source === 'spotify' && !(initial && lease.initialUntil > this.now() &&
            lease.baselineSpotify.includes(id))) this.fail('ownership_lost');
        const values = uris(this.store.raw.get(id));
        if (!initial && values.some(value => !forwardingShape(this.store, room, id, value))) {
          this.fail('source_not_confirmed');
        }
      }
      return {lease, room, state};
    } catch (error) {
      // No later observation/rebind can resurrect this admitted selection.
      this.leases.delete(roomId);
      throw error;
    }
  }
  description(room) {
    if (!room.zoneId) return null;
    const device = this.observer.devices.get(room.zoneId)?.device;
    const url = device?.upnpClient?.url;
    if (!url || device.udn() !== room.zoneId) return null;
    let parsed;
    try { parsed = new URL(url); } catch { return null; }
    if (parsed.protocol !== 'http:' || parsed.username || parsed.password || parsed.search || parsed.hash) return null;
    return {descriptionUrl: url, rendererId: room.zoneId, roomId: room.id,
      rendererIds: [...room.rendererIds]};
  }
  async dispatch(body) {
    const {roomId, action, token} = body;
    if (action === 'select') {
      if (!/^[a-f0-9]{64}$/.test(body.selectionId || '')) this.fail('invalid_selection', 400);
      for (const [id, expires] of this.seen) if (expires <= this.now()) this.seen.delete(id);
      if (this.seen.has(body.selectionId)) this.fail('selection_already_used');
      if (this.seen.size >= 4096) this.fail('selection_limit');
      const {room, state} = this.room(roomId);
      if (room.rendererIds.some(id => !state.renderers.find(r => r.id === id)?.fresh)) this.fail('state_unavailable');
      this.seen.set(body.selectionId, this.now() + 86400000);
      const lease = {token: randomBytes(32).toString('hex'), expires: this.now() + this.ttlMs,
        initialUntil: this.now() + this.handoffMs, physical: [...room.rendererIds], zoneId: room.zoneId,
        baselineSpotify: room.rendererIds.filter(id => state.renderers.find(r => r.id === id)?.source === 'spotify'),
        baseline: new Map(room.rendererIds.map(id => [id, uris(this.store.raw.get(id))])),
        loadUsed: false, creationUsed: false};
      this.leases.set(roomId, lease);
      return {token: lease.token};
    }
    if (action === 'release') {
      if (this.leases.get(roomId)?.token === token) this.leases.delete(roomId);
      return {released: true};
    }
    const initial = ['lookup', 'prepare', 'load', 'loaded_play'].includes(action);
    const {lease, room} = this.validate(roomId, token, {initial});
    if (['prepare', 'load', 'loaded_play'].includes(action) && !lease.loadUsed &&
        lease.initialUntil <= this.now()) {
      this.leases.delete(roomId);
      this.fail('ownership_lost');
    }
    if (action === 'prepare' && !room.zoneId) {
      if (lease.loadUsed) this.fail('zone_unavailable');
      if (lease.creationUsed) return {binding: null, initial: true, physicalReady: false};
      lease.creationUsed = true;
      lease.creating = true;
      // Exactly one explicit demand mutation. Never drop/regroup or retry.
      const operation = this.observer.kernel.getManager().zoneManager.connectRoomToZone(roomId, '');
      let timer;
      try {
        await Promise.race([operation, new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('zone_unavailable')), this.timeoutMs);
        })]);
      } catch {
        if (this.leases.get(roomId) === lease) this.leases.delete(roomId);
        this.fail('zone_unavailable');
      }
      finally { clearTimeout(timer); }
      // Creation may change topology while the command is pending. Only this
      // exact unassigned lease may adopt its one new standalone zone.
      const current = this.leases.get(roomId);
      if (current !== lease) this.fail('ownership_lost');
    }
    if (action === 'load') {
      if (lease.loadUsed) this.fail('play_required');
      lease.loadUsed = true;
    }
    if (action === 'loaded_play' && (!lease.loadUsed || lease.initialUntil <= this.now())) {
      this.leases.delete(roomId);
      this.fail('ownership_lost');
    }
    if (!['lookup', 'prepare', 'load', 'loaded_play', 'guard'].includes(action)) this.fail('invalid_request', 400);
    lease.expires = this.now() + this.ttlMs;
    return {binding: this.description(room), initial: lease.initialUntil > this.now(),
      physicalReads: room.rendererIds.map(id => this.store.completeReads.get(id) || 0),
      physicalReady: room.rendererIds.every(id => {
        const values = uris(this.store.raw.get(id));
        return values.length > 0 && values.every(value => forwardingShape(this.store, room, id, value));
      })};
  }
}

module.exports = {BindingService};
