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
    this.sourceReads = new Map();
    this.spotifyEvidence = new Map();
    this.lastTransition = new Map();
    const hint = (id, origin) => this.sourceEvidence(id, null, {refresh: false, origin});
    store.on('nativeSpotify', id => hint(id, 'cached_renderer_state'));
    store.on('source', (id, uri) => {
      if (typeof uri === 'string' && /spotify:|spotifyconnect/i.test(uri)) hint(id, 'cached_uri_event');
    });
    store.on('observation', (id, raw, meta) => this.sourceEvidence(id, raw, meta));
    store.on('lost', () => { this.sourceReads.clear(); this.lastTransition.clear(); });
  }
  sourceEvidence(id, raw, meta = {}) {
    const {classify} = require('./state');
    const source = raw ? classify(raw) : 'spotify';
    const transport = /^(PLAYING|PAUSED_PLAYBACK|STOPPED|NO_MEDIA_PRESENT|TRANSITIONING)$/.test(
      raw?.CurrentTransportState || raw?.TransportState || '')
      ? (raw.CurrentTransportState || raw.TransportState) : 'UNKNOWN';
    const fresh = meta.refresh === true;
    const uri = raw?.AVTransportURI || raw?.CurrentTrackURI || raw?.TrackURI || '';
    const previous = this.sourceReads.get(id);
    // An initial read establishes a baseline, not a subsequent takeover. Cached
    // notifications never change that baseline, regardless of receipt time.
    const changed = fresh && previous && source === 'spotify' &&
      (previous.source !== 'spotify' || previous.uri !== uri ||
       (transport === 'PLAYING' && previous.transport !== 'PLAYING'));
    if (fresh && source !== 'unknown') this.sourceReads.set(id, {source, transport, uri});
    for (const room of this.store.rooms) {
      if (!room.rendererIds.includes(id) && room.zoneId !== id) continue;
      const kind = room.rendererIds.includes(id) ? 'physical' : 'virtual';
      const signature = JSON.stringify([uri, transport]); // Private, never emitted.
      const increment = changed && this.lastTransition.get(room.id) !== signature;
      if (fresh && source === 'external' && kind === 'virtual') this.lastTransition.delete(room.id);
      const evidence = {origin: fresh ? 'fresh_read' : meta.origin === 'cached_uri_event'
        ? 'cached_uri_event' : 'cached_renderer_state', freshness: fresh ? 'complete_read' : 'cached_event',
        source, transport, kind, change: increment ? 'new_spotify_transition'
          : !fresh ? 'hint_only' : !previous ? 'initial_baseline' : 'equivalent_observation',
        sourceRead: meta.sourceRead === 'media_info' ? 'media_info'
          : meta.sourceRead === 'queried_last_change' ? 'queried_last_change' : 'unspecified',
        readOrigin: meta.origin === 'explicit_source_read' ? 'explicit_source_read'
          : meta.origin === 'poll_complete' ? 'poll_complete' : 'unspecified'};
      if (increment) {
        this.lastTransition.set(room.id, signature);
        this.spotifyVersions.set(room.id, (this.spotifyVersions.get(room.id) || 0) + 1);
        this.spotifyEvidence.set(room.id, evidence);
      }
      if (source === 'spotify' || increment) this.trace('source_evidence', {room: this.alias(room.id),
        context: 'source_observation', ...evidence, incremented: !!increment,
        creationOutstanding: this.pending.has(room.id) || this.uncertain.has(room.id)});
    }
  }
  alias(roomId) { return 'room_' + String(this.allowedRooms().indexOf(roomId) + 1).padStart(2, '0'); }
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
      spotifyVersion: this.spotifyVersions.get(roomId) || 0,
      spotifyEvidence: this.spotifyEvidence.get(roomId) || null};
  }
  catalog() {
    return this.allowedRooms().map(roomId => {
      try { return this.lookup(roomId); }
      catch (error) { return {roomId, endpoint: null, unavailable: error.message}; }
    });
  }
  inventory() {
    const state = this.store.snapshot();
    return state.rooms.map(room => {
      const grouped = !!room.zoneId && state.zones.find(z => z.id === room.zoneId)?.roomIds.length !== 1;
      const discovered = room.rendererIds.filter(id => this.observer.devices.has(id)).length;
      return {roomId: room.id, name: room.name, rendererIds: [...room.rendererIds],
        supported: !grouped && room.rendererIds.length > 0,
        limitation: grouped ? 'grouped_room_not_supported' : !room.rendererIds.length ? 'physical_renderer_missing'
          : discovered !== room.rendererIds.length ? 'physical_renderer_offline_or_undiscovered' : null,
        physicalDiscovered: discovered, topologyFresh: state.topologyFresh};
    });
  }
  async confirmForPlay(roomId) {
    const room = this.room(roomId);
    try { await this.observer.confirmSources(room); }
    catch { this.fail('source_confirmation_unavailable'); }
    const current = this.room(roomId);
    if (JSON.stringify(current.rendererIds) !== JSON.stringify(room.rendererIds)) this.fail('topology_changed');
    return this.lookup(roomId);
  }
  async createForPlay(roomId) {
    this.trace('preflight', {room: this.alias(roomId)});
    try { return await this.createForPlayChecked(roomId); }
    catch (error) {
      const detail = rendererError(error);
      this.trace('failed', {room: this.alias(roomId), ...detail});
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
      this.trace('library_call', {room: this.alias(roomId)});
      await deadline(manager.connectRoomToZone(roomId, ''), this.timeoutMs,
        Object.assign(new Error('renderer_request_timeout'), {origin: 'deadline', phase: 'timeout'}));
      acknowledged = true;
      this.trace('acknowledged', {room: this.alias(roomId), origin: 'raumfeld_response'});
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
  'grouped_room_not_supported', 'creation_pending_or_uncertain', 'topology_changed', 'renderer_manager_unavailable',
  'source_confirmation_unavailable']);
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
    if (req.method === 'GET' && req.url === '/v1/inventory') return send(200, {rooms: companion.inventory()});
    if (req.method === 'POST' && req.url === '/v1/source-snapshot') {
      try {
        let body = '';
        for await (const chunk of req) { body += chunk; if (Buffer.byteLength(body) > 4096) return send(413, {error: 'body_too_large'}); }
        const parsed = JSON.parse(body);
        if (!parsed || Object.keys(parsed).length !== 1 || typeof parsed.roomId !== 'string') return send(400, {error: 'invalid_request'});
        return send(200, await companion.confirmForPlay(parsed.roomId));
      } catch (error) { return send(error.status || 503, rendererError(error)); }
    }
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
