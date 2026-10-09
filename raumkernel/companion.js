'use strict';

const http = require('node:http');
const {timingSafeEqual} = require('node:crypto');
const {deadline} = require('./adapter');

// Discovery and explicit zone demand only. No Qobuz sessions, leases, source
// ownership, transport commands, or background repair live in this companion.
class Companion {
  constructor(store, observer, {allowedRooms, timeoutMs = 10000}) {
    Object.assign(this, {store, observer, allowedRooms, timeoutMs});
    this.pending = new Set();
    // An uncertain mutation cannot be retried by another HTTP request. Reset
    // only after discovery positively observes an assigned zone.
    this.uncertain = new Set();
  }
  fail(code) { throw Object.assign(new Error(code), {status: 409}); }
  room(roomId) {
    if (!this.allowedRooms().includes(roomId)) this.fail('room_not_configured');
    const state = this.store.snapshot();
    if (!state.topologyFresh) this.fail('topology_unavailable');
    const room = state.rooms.find(r => r.id === roomId);
    if (!room || !room.rendererIds.length) this.fail('room_unavailable');
    if (room.zoneId && state.zones.find(z => z.id === room.zoneId)?.roomIds.length !== 1) {
      this.fail('grouped_room_not_supported');
    }
    return room;
  }
  endpoint(room) {
    if (!room.zoneId) return null;
    const device = this.observer.devices.get(room.zoneId)?.device;
    if (!device || device.udn() !== room.zoneId) return null;
    const url = device.upnpClient?.url;
    let parsed;
    try { parsed = new URL(url); } catch { return null; }
    if (parsed.protocol !== 'http:' || parsed.username || parsed.password || parsed.search || parsed.hash) return null;
    return {roomId: room.id, rendererId: room.zoneId, descriptionUrl: url};
  }
  lookup(roomId) {
    const room = this.room(roomId);
    if (room.zoneId) this.uncertain.delete(roomId);
    return {roomId, assigned: !!room.zoneId, endpoint: this.endpoint(room)};
  }
  catalog() {
    return this.allowedRooms().map(roomId => {
      try { return this.lookup(roomId); }
      catch (error) { return {roomId, endpoint: null, unavailable: error.message}; }
    });
  }
  async createForPlay(roomId) {
    const room = this.room(roomId);
    // Assigned but missing renderer: never drop, recreate or regroup it.
    if (room.zoneId) return this.lookup(roomId);
    if (this.pending.has(roomId) || this.uncertain.has(roomId)) this.fail('creation_pending_or_uncertain');
    this.pending.add(roomId);
    this.uncertain.add(roomId);
    const host = this.store.host;
    const physical = JSON.stringify(room.rendererIds);
    try {
      // No await separates the fresh topology check above from this one mutation.
      await deadline(this.observer.kernel.getManager().zoneManager.connectRoomToZone(roomId, ''), this.timeoutMs);
      const current = this.room(roomId);
      if (this.store.host !== host || JSON.stringify(current.rendererIds) !== physical) this.fail('topology_changed');
      // SOAP completion need not mean a renderer has appeared. The caller waits
      // for discovery; neither this method nor catalog polling repeats creation.
      return this.lookup(roomId);
    } catch { this.fail('creation_pending_or_uncertain'); }
    finally { this.pending.delete(roomId); }
  }
}

function createCompanionApi(companion, token) {
  if (typeof token !== 'string' || token.length < 32) throw new Error('API_TOKEN must contain at least 32 characters');
  const expected = Buffer.from(`Bearer ${token}`);
  const server = http.createServer(async (req, res) => {
    const send = (status, body) => {
      res.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      res.end(JSON.stringify(body));
    };
    if (req.method === 'GET' && req.url === '/healthz') return send(200, {status: 'ok'});
    const given = Buffer.from(req.headers.authorization || '');
    if (given.length !== expected.length || !timingSafeEqual(given, expected)) return send(401, {error: 'unauthorized'});
    if (req.method === 'GET' && req.url === '/v1/endpoints') return send(200, {rooms: companion.catalog()});
    if (req.method !== 'POST' || req.url !== '/v1/zone-for-play') return send(404, {error: 'not_found'});
    if (req.headers['content-type']?.split(';')[0] !== 'application/json') return send(415, {error: 'json_required'});
    let size = 0;
    const chunks = [];
    try {
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 4096) return send(413, {error: 'body_too_large'});
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!body || Array.isArray(body) || Object.keys(body).length !== 1 ||
          typeof body.roomId !== 'string' || !body.roomId.length || body.roomId.length > 256) {
        return send(400, {error: 'invalid_request'});
      }
      try { return send(200, await companion.createForPlay(body.roomId)); }
      catch (error) { return send(error.status || 503, {error: error.status ? error.message : 'zone_unavailable'}); }
    } catch { return send(400, {error: 'invalid_json'}); }
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  return server;
}

module.exports = {Companion, createCompanionApi};
