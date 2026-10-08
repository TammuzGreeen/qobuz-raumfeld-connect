'use strict';
const {randomUUID} = require('node:crypto');
const {deadline} = require('./adapter');
const {classify} = require('./state');
const {rendererCommand} = require('./renderer-commands');

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
    leaseMs = 15000, selectionMs = 30000, timeoutMs = 4000,
    handoffMs = 4000, handoffPollMs = 250} = {}) {
    Object.assign(this, {store, observer, allowedRooms, streamAddress, now, leaseMs, selectionMs, timeoutMs, handoffMs, handoffPollMs});
    this.leases = new Map();
    this.ownershipLosses = new Map();
    this.volumeCommands = new Map();
    this.seen = new Map();
    this.queue = Promise.resolve();
    store.on('lost', () => {
      for (const id of this.leases.keys()) this.revoke(id, 'host_lost');
    });
    store.on('removed', id => {
      for (const [roomId, lease] of this.leases) if (lease.ids.includes(id)) this.revoke(roomId, 'renderer_removed', id);
    });
    store.on('source', (id, value) => this.sourceChanged(id, [value]));
    store.on('observation', (id, raw) => this.sourceChanged(id, uris(raw)));
    store.on('topology', () => {
      for (const [id, lease] of this.leases) {
        const room = store.rooms.find(r => r.id === id);
        if (!room || this.grouped(room) || (!lease.transition && signature(room) !== lease.signature)) this.revoke(id, 'topology_changed');
      }
    });
  }
  noteOwnershipLoss(id, reason, rendererId, value) {
    const lease = this.leases.get(id);
    if (!lease) return;
    const loss = {reason, at: this.now(),
      phase: lease.transition ? 'transition' : lease.pending ? 'selection' : 'owned'};
    if (rendererId) loss.rendererRole = lease.physicalIds.includes(rendererId) ? 'physical' : 'virtual';
    if (typeof value === 'string') {
      loss.source = classify({AVTransportURI: value});
      let url;
      try { url = new URL(value); } catch {}
      loss.uriKind = loss.source === 'spotify' ? 'spotify' : !value ? 'empty'
        : url?.hostname === this.streamAddress && +url.port >= 8790 && +url.port <= 8839 && /^\/audio\/[a-f0-9]{32}$/.test(url.pathname) ? 'qobuz_relay'
        : /^(dlna-playcontainer|dlna-playsingle|raumfeld):/i.test(value) ? 'internal_transport' : 'external';
    }
    this.ownershipLosses.set(id, loss);
    while (this.ownershipLosses.size > 50) this.ownershipLosses.delete(this.ownershipLosses.keys().next().value);
  }
  revoke(id, reason, rendererId, value) {
    // Preserve a preceding guard failure when Python subsequently releases the
    // failed session. Diagnostics never retain tokens, IDs or raw transport URIs.
    if (reason !== 'released' || !this.ownershipLosses.has(id)) this.noteOwnershipLoss(id, reason, rendererId, value);
    this.leases.delete(id);
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
  physicalForwarding(roomId, lease, rendererId, value) {
    if (classify({AVTransportURI: value}) === 'spotify') return false;
    if (lease.expires <= this.now() || !lease.physicalIds.includes(rendererId) || rendererId === lease.target || !lease.expected.size) return false;
    const snapshot = this.store.snapshot(), room = snapshot.rooms.find(r => r.id === roomId);
    if (!snapshot.topologyFresh || !room?.fresh || this.grouped(room) || room.zoneId !== lease.target ||
        JSON.stringify(room.rendererIds) !== JSON.stringify(lease.physicalIds)) return false;
    const target = this.store.raw.get(lease.target) || {}, targetURIs = uris(target);
    if (classify(target) === 'spotify' || !targetURIs.length || !targetURIs.every(uri => lease.expected.has(uri))) return false;
    let url;
    try { url = new URL(value); } catch { return false; }
    if (url.protocol !== 'http:' || url.hostname !== this.store.host || +url.port < 49152 || +url.port > 65535 ||
        url.username || url.password || url.hash) return false;
    const normalize = id => id.toLowerCase().replace(/^(?:urn:uuid:|uuid:)/, '');
    let parts;
    try { parts = url.pathname.slice(1).split('/').map(decodeURIComponent); } catch { return false; }
    const ids = [room.zoneId, room.id, rendererId].map(normalize);
    // Observed Raumfeld forwarding format: zone / room / physical / stream.
    // Require exact ordered identities, not substring matches or just a host.
    if (new Set(ids).size !== 3 || parts.length !== 4 ||
        !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(parts[3])) return false;
    const actual = parts.slice(0, 3).map(normalize);
    if (new Set(actual).size !== 3 || !ids.every((id, index) => actual[index] === id)) return false;
    const query = [...url.searchParams.entries()];
    return query.length === 1 && query[0][0] === 'punch' && /^\d{1,20}$/.test(query[0][1]);
  }
  sourceChanged(id, values) {
    for (const [roomId, lease] of this.leases) {
      if (!lease.ids.includes(id)) continue;
      const unexpected = values.filter(Boolean).find(value => {
        if (lease.expected.has(value)) return false;
        // Only the initial explicit transition may see the source being replaced.
        if ((lease.pending || lease.transition) && lease.baseline.get(id)?.includes(value)) return false;
        if (this.physicalForwarding(roomId, lease, id, value)) return false;
        // Physical renderers can expose an internal transport URI while their
        // virtual renderer owns playback. Accept only their observed baseline,
        // and never allow Spotify evidence after transition completion.
        if (id !== lease.target && classify({AVTransportURI: value}) !== 'spotify' && lease.baseline.get(id)?.includes(value)) return false;
        return true;
      });
      if (unexpected) this.revoke(roomId, 'unexpected_source_uri', id, unexpected);
    }
  }
  current(id, token) {
    const lease = this.leases.get(id);
    if (!lease || lease.token !== token || lease.expires <= this.now()) {
      this.revoke(id, lease?.token !== token ? 'token_mismatch' : 'lease_expired');
      fail('ownership_lost');
    }
    try { this.room(id, !lease.transition); }
    catch (error) { this.noteOwnershipLoss(id, 'room_guard_failed'); throw error; }
    if (JSON.stringify(this.room(id, false).rendererIds) !== JSON.stringify(lease.physicalIds)) {
      this.revoke(id, 'physical_membership_changed'); fail('room_membership_changed');
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
      room.lastOwnershipLoss = this.ownershipLosses.has(room.id) ? {...this.ownershipLosses.get(room.id)} : null;
      room.lastVolumeCommand = this.volumeCommands.has(room.id) ? {...this.volumeCommands.get(room.id)} : null;
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
    this.ownershipLosses.delete(id);
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
    const timeout = Object.assign(new Error('Command deadline exceeded'), {code: 'ECOMMANDTIMEOUT'});
    try {
      const command = method === 'connectRoomToZone' ? device[method](...args) : rendererCommand(device, method, args);
      return await deadline(command, this.timeoutMs, timeout);
    }
    catch (error) {
      // A definite unsupported-seek SOAP fault means the position was not
      // applied. It is not an uncertain transport failure or a source takeover.
      if (method === 'seek' && error?.code === 'EUPNP' && String(error.errorCode) === '710') {
        this.current(id, token);
        return {accepted: false, applied: false, error: 'seek_mode_not_supported', upnpErrorCode: 710};
      }
      const hadLease = this.leases.has(id);
      this.revoke(id, 'command_failed_or_timed_out');
      const loss = this.ownershipLosses.get(id);
      if (hadLease && loss) {
        const methods = {setAvTransportUri:'SetAVTransportURI',play:'Play',pause:'Pause',stop:'Stop',seek:'Seek',setVolume:'SetVolume',connectRoomToZone:'connectRoomToZone'};
        loss.command = methods[method] || 'unknown';
        const codes = ['ECOMMANDTIMEOUT','ECONNRESET','ECONNREFUSED','ETIMEDOUT','EHOSTUNREACH','ENOACTION','ENOSERVICE','EUPNP'];
        loss.commandCode = codes.includes(error?.code) ? error.code : 'command_failed';
        if (error?.code === 'EUPNP' && /^[1-9]\d{2}$/.test(String(error.errorCode))) loss.upnpErrorCode = Number(error.errorCode);
      }
      fail('command_failed_or_timed_out');
    }
  }
  volumeEvidence(id, token, lease) {
    if (this.current(id, token) !== lease || lease.pending || lease.transition) fail('ownership_lost');
    const room = this.room(id), snapshot = this.store.snapshot();
    if (room.zoneId !== lease.target || signature(room) !== lease.signature) fail('ownership_lost');
    for (const rendererId of lease.ids) {
      const values = uris(this.store.raw.get(rendererId));
      if (!values.length || values.some(value => classify({AVTransportURI: value}) === 'spotify')) fail('ownership_lost');
      if (values.some(value => !lease.expected.has(value) &&
          !this.physicalForwarding(id, lease, rendererId, value))) fail('ownership_lost');
      if (!snapshot.renderers.find(r => r.id === rendererId)?.fresh) fail('state_unavailable');
    }
  }
  async volume(id, token, device, lease, value) {
    this.volumeEvidence(id, token, lease);
    // A cloud echo or queued slider event must not repeat an uncertain write.
    // Only a genuinely fresh selected lease can clear this write barrier.
    if (lease.volumeUncertain) return {accepted: false, applied: null,
      error: 'volume_command_uncertain', ownershipRetained: true, attempts: 0};
    const diagnostic = {at: this.now(), connection: 'node_to_renderer', endpoint: 'RenderingControl',
      command: 'SetRoomVolume', rendererRole: 'virtual', attempts: 1, outcome: 'pending'};
    this.volumeCommands.set(id, diagnostic);
    while (this.volumeCommands.size > 50) this.volumeCommands.delete(this.volumeCommands.keys().next().value);
    const started = performance.now();
    let commandError = null;
    try {
      await deadline(rendererCommand(device, 'setRoomVolume', [id, value]), this.timeoutMs,
        Object.assign(new Error('Command deadline exceeded'), {code: 'ECOMMANDTIMEOUT'}));
      diagnostic.soapStatus = 200;
    } catch (error) {
      commandError = error;
      const codes = ['ECOMMANDTIMEOUT','ECONNRESET','ECONNREFUSED','ETIMEDOUT','EHOSTUNREACH','ENOACTION','ENOSERVICE','EUPNP'];
      diagnostic.code = codes.includes(error?.code) ? error.code : 'command_failed';
      if (error?.code === 'EUPNP') {
        if (/^[1-9]\d{2}$/.test(String(error.errorCode))) diagnostic.upnpErrorCode = Number(error.errorCode);
        if (Number.isInteger(error.statusCode) && error.statusCode >= 400 && error.statusCode <= 599) diagnostic.soapStatus = error.statusCode;
      }
    }
    diagnostic.elapsedMs = Math.round(performance.now() - started);
    // The mutation is never retried, even when the readback matches. Obtain
    // complete new source evidence, not merely transport PLAYING or a getter.
    const before = new Map(lease.ids.map(rendererId => [rendererId, this.store.raw.get(rendererId)]));
    const observationStart = this.now();
    try {
      await this.refresh(id, token);
      this.volumeEvidence(id, token, lease);
      const snapshot = this.store.snapshot();
      for (const rendererId of lease.ids) {
        if (before.get(rendererId) === this.store.raw.get(rendererId) ||
            snapshot.renderers.find(r => r.id === rendererId)?.observedAt < observationStart) fail('state_unavailable');
      }
      diagnostic.ownership = 'confirmed';
      const readStart = performance.now();
      try {
        const readback = await deadline(rendererCommand(device, 'getRoomVolume', [id]), this.timeoutMs);
        const volume = Number(readback.CurrentVolume);
        if (!/^\d{1,3}$/.test(String(readback.CurrentVolume)) || !Number.isInteger(volume) || volume < 0 || volume > 100) throw new Error('Invalid volume readback');
        diagnostic.observedVolume = volume;
        diagnostic.readback = 'success';
      } catch { diagnostic.readback = 'failed'; }
      diagnostic.readbackElapsedMs = Math.round(performance.now() - readStart);
      this.volumeEvidence(id, token, lease);
    } catch {
      diagnostic.ownership = 'unavailable';
      diagnostic.outcome = 'failed_closed';
      this.revoke(id, 'volume_ownership_unconfirmed');
      fail('command_failed_or_timed_out');
    }
    if (commandError || diagnostic.observedVolume !== value) {
      lease.volumeUncertain = true;
      diagnostic.outcome = commandError?.code === 'EUPNP' ? 'rejected'
        : commandError ? 'uncertain' : 'not_confirmed';
      return {accepted: false, applied: null, error: 'volume_command_uncertain', ownershipRetained: true,
        attempts: 1, ...(diagnostic.observedVolume == null ? {} : {observedVolume: diagnostic.observedVolume})};
    }
    diagnostic.outcome = 'confirmed';
    return {accepted: true, applied: true, observedVolume: diagnostic.observedVolume};
  }
  async dispatch(request) {
    const {roomId, action, token} = request;
    if (action === 'release') {
      if (this.leases.get(roomId)?.token === token) this.revoke(roomId, 'released');
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
        if (!room.zoneId || !this.observer.devices.has(room.zoneId)) { this.revoke(id, 'zone_unavailable'); fail('zone_unavailable'); }
        lease.ids = [...room.rendererIds, room.zoneId];
        lease.target = room.zoneId;
      }
    }
    const device = this.observer.devices.get(lease.target)?.device;
    if (!device) { this.revoke(id, 'renderer_unavailable'); fail('renderer_unavailable'); }
    if (action === 'play') {
      lease.expected.add(url);
      const title = escapeXml(metadata.title), artist = escapeXml(metadata.artist), album = escapeXml(metadata.album);
      const mime = metadata.mime === 'audio/mpeg' ? 'audio/mpeg' : 'audio/flac';
      const didl = `<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="qobuz" parentID="0" restricted="1"><dc:title>${title}</dc:title><upnp:artist>${artist}</upnp:artist><upnp:album>${album}</upnp:album><upnp:class>object.item.audioItem.musicTrack</upnp:class><res protocolInfo="http-get:*:${mime}:*">${escapeXml(url)}</res></item></DIDL-Lite>`;
      await this.action(id, token, device, 'setAvTransportUri', url, didl, false);
      // Loading can return before the virtual URI and physical source settle.
      // Confirm both within the same bounded initial handoff, before Play.
      await this.refresh(id, token);
      this.room(id);
      // Only the first explicit Play may wait for the selected Spotify source
      // to relinquish. sourceChanged still rejects new source/session evidence,
      // and each poll rechecks freshness, membership and the original deadline.
      const deadlineAt = performance.now() + this.handoffMs;
      for (;;) {
        const targetRaw = this.store.raw.get(lease.target) || {};
        const targetConfirmed = classify(targetRaw) !== 'spotify' && uris(targetRaw).includes(url);
        const physical = room.rendererIds.find(renderer => classify(this.store.raw.get(renderer) || {}) === 'spotify');
        if (targetConfirmed && !physical) break;
        if (!lease.pending || !lease.transition || performance.now() >= deadlineAt) {
          if (!targetConfirmed) {
            this.revoke(id, 'source_not_confirmed', lease.target); fail('source_not_confirmed');
          }
          this.revoke(id, 'spotify_still_active', physical); fail('spotify_still_active');
        }
        await new Promise(resolve => setTimeout(resolve, Math.min(this.handoffPollMs, Math.max(0, deadlineAt - performance.now()))));
        await this.refresh(id, token);
        this.room(id);
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
        this.revoke(id, 'owned_source_guard_failed'); fail('ownership_lost');
      }
      if (action === 'volume') return this.volume(id, token, device, lease, value);
      const methods = {pause: ['pause', false], resume: ['play', false], stop: ['stop', false],
        volume: ['setVolume', value, false], seek: ['seek', 'REL_TIME', [Math.floor((value || 0)/3600000), Math.floor((value || 0)/60000)%60, Math.floor((value || 0)/1000)%60].map(v=>String(v).padStart(2,'0')).join(':')]};
      const result = await this.action(id, token, device, ...methods[action]);
      if (action === 'seek' && result?.error === 'seek_mode_not_supported') return result;
    }
    return {accepted: true};
  }
}
module.exports = {Controller, ControlError};
