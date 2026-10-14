'use strict';

const http = require('node:http');
const {timingSafeEqual} = require('node:crypto');
const {deadline} = require('./adapter');

// Discovery and explicit zone demand only. No Qobuz sessions, leases, source
// ownership, transport commands, or background repair live in this companion.
class Companion {
  constructor(store, observer, {allowedRooms, timeoutMs = 10000, diagnostic = () => {}}) {
    Object.assign(this, {store, observer, allowedRooms, timeoutMs, diagnostic});
    this.pending = new Set();
    // An uncertain mutation cannot be retried by another HTTP request. Reset
    // only after discovery positively observes an assigned zone.
    this.uncertain = new Set();
    this.spotifyVersions = new Map();
    const spotify = id => {
      for (const room of this.store.rooms) {
        if (room.rendererIds.includes(id) || room.zoneId === id) {
          this.spotifyVersions.set(room.id, (this.spotifyVersions.get(room.id) || 0) + 1);
        }
      }
    };
    store.on('nativeSpotify', spotify);
    store.on('source', (id, uri) => {
      if (typeof uri === 'string' && /spotify:|spotifyconnect/i.test(uri)) spotify(id);
    });
    const lastSpotify = new Map();
    store.on('observation', (id, raw) => {
      const detected = [raw?.AVTransportURI, raw?.TrackURI, raw?.CurrentTrackURI]
        .some(uri => typeof uri === 'string' && /spotify:|spotifyconnect/i.test(uri));
      if (detected && lastSpotify.get(id) !== true) spotify(id);
      lastSpotify.set(id, detected);
    });
  }
  fail(code, detail = {}) {
    throw Object.assign(new Error(code), {status: 409, origin: 'local_check',
      phase: 'preflight', requestAttempted: false, ...detail});
  }
  trace(phase, detail = {}) {
    try { this.diagnostic({context: 'explicit_play_renderer', phase, ...detail}); }
    catch {} // Diagnostic failure cannot alter authority or command outcome.
  }
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
    return {roomId, assigned: !!room.zoneId, endpoint: this.endpoint(room),
      rendererIds: [...room.rendererIds],
      spotifyVersion: this.spotifyVersions.get(roomId) || 0};
  }
  catalog() {
    return this.allowedRooms().map(roomId => {
      try { return this.lookup(roomId); }
      catch (error) { return {roomId, endpoint: null, unavailable: error.message}; }
    });
  }
  async createForPlay(roomId) {
    this.trace('preflight');
    try { return await this.createForPlayChecked(roomId); }
    catch (error) {
      const detail = rendererError(error);
      this.trace('failed', detail);
      throw Object.assign(new Error(detail.error), {status: 409, ...detail});
    }
  }
  async createForPlayChecked(roomId) {
    const room = this.room(roomId);
    // Assigned but missing renderer: never drop, recreate or regroup it.
    if (room.zoneId) return this.lookup(roomId);
    const manager = this.observer.kernel?.managerDisposer?.zoneManager;
    if (typeof manager?.connectRoomToZone !== 'function') this.fail('renderer_manager_unavailable');
    if (this.pending.has(roomId) || this.uncertain.has(roomId)) this.fail('creation_pending_or_uncertain',
      {phase: 'uncertainty_block'});
    this.pending.add(roomId);
    this.uncertain.add(roomId);
    const host = this.store.host;
    const physical = JSON.stringify(room.rendererIds);
    let acknowledged = false;
    try {
      // No await separates the fresh topology check above from this one mutation.
      this.trace('library_call');
      await deadline(manager.connectRoomToZone(roomId, ''), this.timeoutMs,
        Object.assign(new Error('renderer_request_timeout'), {origin: 'deadline', phase: 'timeout'}));
      acknowledged = true;
      this.trace('acknowledged', {origin: 'raumfeld_response'});
      const current = this.room(roomId);
      if (this.store.host !== host || JSON.stringify(current.rendererIds) !== physical) this.fail('topology_changed',
        {phase: 'postflight', requestAttempted: true});
      // SOAP completion need not mean a renderer has appeared. The caller waits
      // for discovery; neither this method nor catalog polling repeats creation.
      return this.lookup(roomId);
    } catch (error) {
      if (error.status) {
        error.phase = acknowledged ? 'postflight' : error.phase;
        error.requestAttempted = acknowledged || error.requestAttempted;
        throw error;
      }
      throw Object.assign(new Error('creation_pending_or_uncertain'), rendererError(error));
    }
    finally { this.pending.delete(roomId); }
  }
}

const localErrors = new Set(['room_not_configured', 'topology_unavailable', 'room_unavailable',
  'grouped_room_not_supported', 'creation_pending_or_uncertain', 'topology_changed', 'renderer_manager_unavailable']);
const origins = new Set(['local_check', 'raumfeld_response', 'network', 'deadline', 'library_unknown']);
const phases = new Set(['preflight', 'uncertainty_block', 'postflight', 'response', 'network', 'timeout', 'library']);
const networkCodes = new Set(['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'EHOSTUNREACH', 'ENETUNREACH', 'ENOTFOUND']);
function rendererError(error) {
  const origin = origins.has(error.origin) ? error.origin
    : origins.has(error.rendererRequestOrigin) ? error.rendererRequestOrigin : 'library_unknown';
  const phase = phases.has(error.phase) ? error.phase
    : origin === 'raumfeld_response' ? 'response' : origin === 'network' ? 'network'
      : origin === 'local_check' ? 'preflight' : 'library';
  const status = error.rendererHttpStatus;
  const code = error.rendererNetworkCode || error.networkCode;
  return {error: localErrors.has(error.message) ? error.message : 'renderer_request_failed', origin, phase,
    requestAttempted: typeof error.requestAttempted === 'boolean' ? error.requestAttempted
      : typeof error.rendererRequestAttempted === 'boolean' ? error.rendererRequestAttempted : 'unknown',
    ...(Number.isInteger(status) && status >= 100 && status <= 599 ? {rendererHttpStatus: status} : {}),
    ...(networkCodes.has(code) ? {networkCode: code} : {})};
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
      catch (error) { return send(error.status || 503, rendererError(error)); }
    } catch { return send(400, {error: 'invalid_json'}); }
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  return server;
}

module.exports = {Companion, createCompanionApi};
