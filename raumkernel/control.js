'use strict';
const {randomUUID} = require('node:crypto');
const {deadline} = require('./adapter');
const {classify} = require('./state');

class ControlError extends Error {
  constructor(code, status = 409) { super(code); this.status = status; }
}
const fail = code => { throw new ControlError(code); };
const uris = raw => [raw?.AVTransportURI, raw?.TrackURI, raw?.CurrentTrackURI]
  .filter(v => typeof v === 'string' && v);
const signature = room => JSON.stringify([room.zoneId, [...room.rendererIds].sort()]);
const escapeXml = s => String(s || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&apos;'}[c]));

class Controller {
  constructor(store, observer, {allowedRooms = () => [], streamAddress, now = Date.now,
    leaseMs = 15000, selectionMs = 30000, timeoutMs = 4000} = {}) {
    Object.assign(this, {store, observer, allowedRooms, streamAddress, now, leaseMs, selectionMs, timeoutMs});
    this.leases = new Map();
    this.seen = new Map();
    this.queue = Promise.resolve();
    store.on('lost', () => this.leases.clear());
    store.on('removed', id => {
      for (const [roomId, lease] of this.leases) if (lease.ids.includes(id)) this.leases.delete(roomId);
    });
    store.on('source', (id, value) => this.sourceChanged(id, [value]));
    store.on('observation', (id, raw) => this.sourceChanged(id, uris(raw)));
    store.on('topology', () => {
      for (const [id, lease] of this.leases) {
        const room = store.rooms.find(r => r.id === id);
        if (!room || this.grouped(room) || (!lease.transition && signature(room) !== lease.signature)) this.leases.delete(id);
      }
    });
  }
  grouped(room) { return room.zoneId && this.store.zones.find(z => z.id === room.zoneId)?.roomIds.length !== 1; }
  room(id, fresh = true) {
    if (!this.allowedRooms().includes(id)) fail('room_not_enabled');
    const snapshot = this.store.snapshot();
    const room = snapshot.rooms.find(r => r.id === id);
    if (!room) fail('room_not_found');
    if (this.grouped(room)) fail('grouped_room_not_supported');
    if (!snapshot.topologyFresh || (fresh && !room.fresh)) fail('state_unavailable');
    return room;
  }
  sourceChanged(id, values) {
    for (const [roomId, lease] of this.leases) {
      if (!lease.ids.includes(id)) continue;
      const unexpected = values.filter(Boolean).some(value => {
        if (lease.expected.has(value)) return false;
        // Only the initial explicit transition may see the source being replaced.
        if ((lease.pending || lease.transition) && lease.baseline.get(id)?.includes(value)) return false;
        // Physical renderers can expose an internal transport URI while their
        // virtual renderer owns playback. Accept only their observed baseline,
        // and never allow Spotify evidence after transition completion.
        if (id !== lease.target && classify({AVTransportURI: value}) !== 'spotify' && lease.baseline.get(id)?.includes(value)) return false;
        return true;
      });
      if (unexpected) this.leases.delete(roomId);
    }
  }
  current(id, token) {
    const lease = this.leases.get(id);
    if (!lease || lease.token !== token || lease.expires <= this.now()) { this.leases.delete(id); fail('ownership_lost'); }
    this.room(id, !lease.transition);
    if (JSON.stringify(this.room(id, false).rendererIds) !== JSON.stringify(lease.physicalIds)) {
      this.leases.delete(id); fail('room_membership_changed');
    }
    return lease;
  }
  decorate(snapshot) {
    snapshot.capabilities.playback = true;
    snapshot.capabilities.qobuzConnect = true;
    for (const room of snapshot.rooms) {
      const lease = this.leases.get(room.id);
      room.enabled = this.allowedRooms().includes(room.id);
      room.grouped = !!this.grouped(room);
      room.transitioning = !!lease && lease.transition && lease.expires > this.now();
      room.controllable = room.enabled && !this.grouped(room) && room.fresh;
      room.owned = !!lease && lease.expires > this.now() && !lease.pending && room.fresh;
      if (room.owned) { room.source = 'qobuz'; room.protected = false; }
    }
    return snapshot;
  }
  select(id, selectionId) {
    const room = this.room(id);
    if (typeof selectionId !== 'string' || selectionId.length < 8 || selectionId.length > 256) fail('invalid_selection');
    for (const [key, until] of this.seen) if (until <= this.now()) this.seen.delete(key);
    const key = `${id}:${selectionId}`;
    if (this.seen.has(key)) fail('selection_already_used');
    if (this.seen.size >= 4096) fail('selection_limit');
    this.seen.set(key, this.now() + 86400000);
    const ids = [...room.rendererIds, ...(room.zoneId ? [room.zoneId] : [])];
    const lease = {token: randomUUID(), pending: true, transition: false,
      signature: signature(room), ids, physicalIds: [...room.rendererIds], target: room.zoneId || room.rendererIds[0],
      baseline: new Map(ids.map(id => [id, uris(this.store.raw.get(id))])), expected: new Set(),
      expires: this.now() + this.selectionMs};
    this.leases.set(id, lease);
    return {token: lease.token, expiresAt: lease.expires};
  }
  async refresh(id, token) {
    // Wait for an existing observation round, then request a complete new one.
    const until = this.now() + 10000;
    while (this.observer.busy) {
      if (this.now() > until) fail('observation_busy');
      await new Promise(r => setTimeout(r, 25));
    }
    await this.observer.poll();
    return this.current(id, token);
  }
  async action(id, token, device, method, ...args) {
    this.current(id, token);
    try { return await deadline(device[method](...args), this.timeoutMs); }
    catch { this.leases.delete(id); fail('command_failed_or_timed_out'); }
  }
  async dispatch(request) {
    const {roomId, action, token} = request;
    if (action === 'release') {
      if (this.leases.get(roomId)?.token === token) this.leases.delete(roomId);
      return {released: true};
    }
    if (action === 'select') return this.select(roomId, request.selectionId);
    if (action === 'heartbeat') {
      const lease = this.current(roomId, token);
      // Pending selection expires on its original deadline, even with heartbeats.
      if (!lease.pending) lease.expires = this.now() + this.leaseMs;
      return {owned: !lease.pending, expiresAt: lease.expires};
    }
    const job = this.queue.then(() => this.execute(request));
    this.queue = job.catch(() => {});
    return job;
  }
  validateUrl(value) {
    let url;
    try { url = new URL(value); } catch { fail('invalid_stream'); }
    if (url.protocol !== 'http:' || url.hostname !== this.streamAddress || url.username || url.password ||
        +url.port < 8790 || +url.port > 8839 || !/^\/audio\/[a-f0-9]{32}$/.test(url.pathname) || url.search || url.hash) fail('invalid_stream');
    return url.href;
  }
  async execute({roomId: id, token, action, url, metadata = {}, value}) {
    if (!['play', 'pause', 'resume', 'stop', 'seek', 'volume'].includes(action)) fail('invalid_action');
    if (action === 'volume' && (!Number.isInteger(value) || value < 0 || value > 100)) fail('invalid_volume');
    if (action === 'seek' && (!Number.isInteger(value) || value < 0 || value > 86400000)) fail('invalid_position');
    if (action === 'play') url = this.validateUrl(url);
    let lease = await this.refresh(id, token);
    if (lease.pending && action !== 'play') fail('play_required');
    let room = this.room(id);
    if (action === 'play' && lease.pending) {
      lease.transition = true;
      // Only explicit selection reaches this path. No automatic zone repair.
      if (!room.zoneId) {
        await this.action(id, token, this.observer.kernel.managerDisposer.zoneManager,
          'connectRoomToZone', id, '', false);
        const until = this.now() + 12000;
        do {
          await new Promise(r => setTimeout(r, 250));
          await this.refresh(id, token);
          room = this.room(id, false);
          if (room.zoneId && this.observer.devices.has(room.zoneId)) break;
        } while (this.now() < until);
        if (!room.zoneId || !this.observer.devices.has(room.zoneId)) { this.leases.delete(id); fail('zone_unavailable'); }
        lease.ids = [...room.rendererIds, room.zoneId];
        lease.target = room.zoneId;
      }
    }
    const device = this.observer.devices.get(lease.target)?.device;
    if (!device) { this.leases.delete(id); fail('renderer_unavailable'); }
    if (action === 'play') {
      lease.expected.add(url);
      const title = escapeXml(metadata.title), artist = escapeXml(metadata.artist), album = escapeXml(metadata.album);
      const mime = metadata.mime === 'audio/mpeg' ? 'audio/mpeg' : 'audio/flac';
      const didl = `<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="qobuz" parentID="0" restricted="1"><dc:title>${title}</dc:title><upnp:artist>${artist}</upnp:artist><upnp:album>${album}</upnp:album><upnp:class>object.item.audioItem.musicTrack</upnp:class><res protocolInfo="http-get:*:${mime}:*">${escapeXml(url)}</res></item></DIDL-Lite>`;
      await this.action(id, token, device, 'setAvTransportUri', url, didl, false);
      // Verify the new URI before Play; native Spotify must have relinquished.
      await this.refresh(id, token);
      if (classify(this.store.raw.get(lease.target) || {}) === 'spotify' ||
          !uris(this.store.raw.get(lease.target)).includes(url)) { this.leases.delete(id); fail('source_not_confirmed'); }
      for (const physical of room.rendererIds) {
        if (classify(this.store.raw.get(physical) || {}) === 'spotify') { this.leases.delete(id); fail('spotify_still_active'); }
      }
      lease.pending = false;
      lease.transition = false;
      lease.signature = signature(this.room(id));
      lease.expires = this.now() + this.leaseMs;
      await this.action(id, token, device, 'play', false);
      lease.expected = new Set([url]);
    } else {
      const targetRaw = this.store.raw.get(lease.target);
      if (!uris(targetRaw).some(uri => lease.expected.has(uri)) || this.room(id).source === 'spotify') {
        this.leases.delete(id); fail('ownership_lost');
      }
      const methods = {pause: ['pause', false], resume: ['play', false], stop: ['stop', false],
        volume: ['setVolume', value, false], seek: ['seek', 'REL_TIME', [Math.floor((value || 0)/3600000), Math.floor((value || 0)/60000)%60, Math.floor((value || 0)/1000)%60].map(v=>String(v).padStart(2,'0')).join(':')]};
      await this.action(id, token, device, ...methods[action]);
    }
    return {accepted: true};
  }
}
module.exports = {Controller, ControlError};
