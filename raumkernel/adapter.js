'use strict';

const {parseStringPromise} = require('xml2js');

function deadline(promise, ms) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('Observation timeout')), ms);
  })]).finally(() => clearTimeout(timer));
}

// Only read actions are exposed here. Neither repair nor renderer creation is
// called on discovery, reconnect, removal, poll failure, or service shutdown.
class RaumkernelObserver {
  constructor(kernel, store, {pollMs = 5000, timeoutMs = 3000, fetchImpl = fetch} = {}) {
    this.kernel = kernel;
    this.store = store;
    this.pollMs = pollMs;
    this.timeoutMs = timeoutMs;
    this.fetch = fetchImpl;
    this.devices = new Map();
    this.listeners = [];
    this.epoch = 0;
    this.busy = false;
    this.stopped = false;
  }
  on(event, handler) {
    this.kernel.on(event, handler);
    this.listeners.push([event, handler]);
  }
  start() {
    this.on('systemHostFound', host => {
      this.epoch++;
      this.devices.clear();
      this.store.hostLost();
      this.store.hostFound(host);
    });
    this.on('systemHostLost', () => {
      this.epoch++;
      this.devices.clear();
      this.store.hostLost();
    });
    this.on('zoneConfigurationChanged', config => {
      try { this.store.topology(config); }
      catch { this.store.topologyAt = null; }
    });
    for (const event of ['mediaRendererRaumfeldAdded', 'mediaRendererRaumfeldVirtualAdded']) {
      this.on(event, (id, device) => this.devices.set(id, {device, version: 0}));
    }
    for (const event of ['mediaRendererRemoved', 'mediaRendererRaumfeldRemoved', 'mediaRendererRaumfeldVirtualRemoved']) {
      this.on(event, id => {
        this.devices.delete(id);
        this.store.removed(id);
      });
    }
    this.on('rendererStateChanged', (device, state) => {
      const entry = this.devices.get(device.udn());
      if (!entry || entry.device !== device) return;
      // A volume event must not renew source freshness. Polls establish full
      // source freshness; event observations may immediately strengthen a
      // Spotify block, but cannot release it or renew it using cached URI data.
      entry.version++;
      const {classify} = require('./state');
      if (classify(state) === 'spotify') {
        const previous = this.store.renderers.get(device.udn());
        this.store.observe(device.udn(), state);
        this.store.renderers.get(device.udn()).observedAt = previous?.observedAt ?? 0;
      }
    });
    this.on('rendererStateKeyValueChanged', (device, key, oldValue, value) => {
      if (this.devices.get(device.udn())?.device !== device) return;
      if (['AVTransportURI', 'CurrentTrackURI', 'TrackURI'].includes(key) && oldValue !== value) {
        this.devices.get(device.udn()).version++;
        this.store.emit('source', device.udn(), value);
      }
    });
    this.kernel.init();
    this.timer = setInterval(() => void this.poll(), this.pollMs);
    void this.poll();
  }
  async poll() {
    if (this.busy || this.stopped || !this.store.host) return;
    this.busy = true;
    const epoch = this.epoch, host = this.store.host;
    const valid = () => !this.stopped && epoch === this.epoch && host === this.store.host;
    try {
      // Periodic full topology refresh recovers missed notifications and bounds
      // freshness even if the upstream host pinger hangs.
      const response = await this.fetch(`http://${host}:47365/getZones`,
        {signal: AbortSignal.timeout(this.timeoutMs)});
      if (!response.ok) throw new Error('Topology unavailable');
      const config = await parseStringPromise(await response.text());
      if (!valid()) return;
      this.store.topology(config);
      // Limit concurrent device observations on larger multiroom systems.
      const entries = [...this.devices.entries()];
      for (let index = 0; index < entries.length; index += 4) {
        await Promise.all(entries.slice(index, index + 4).map(async ([id, entry]) => {
          const version = entry.version;
          try {
            const [media, transport, position] = await deadline(Promise.all([
              entry.device.callAction('AVTransport', 'GetMediaInfo', {}),
              entry.device.getTransportInfo(), entry.device.getPositionInfo(),
            ]), this.timeoutMs);
            if (!valid() || this.devices.get(id) !== entry || entry.version !== version) return;
            let volume = null;
            try { volume = await deadline(entry.device.getVolume(), this.timeoutMs); } catch {}
            if (!valid() || this.devices.get(id) !== entry || entry.version !== version) return;
            this.store.observe(id, {AVTransportURI: media.CurrentURI,
              TrackURI: position.TrackURI, CurrentTransportState: transport.CurrentTransportState,
              RelTime: position.RelTime, TrackDuration: position.TrackDuration, Volume: volume});
          } catch { /* Failed observations never refresh cached state. */ }
        }));
        if (!valid()) return;
      }
    } catch { if (valid()) this.store.topologyAt = null; }
    finally { this.busy = false; }
  }
  stop() {
    this.stopped = true;
    this.epoch++;
    clearInterval(this.timer);
    for (const [event, handler] of this.listeners) this.kernel.off(event, handler);
    this.listeners = [];
    this.store.hostLost();
    // The process supervisor terminates upstream timers/sockets. No Stop or
    // standby commands are sent to speakers during shutdown.
  }
}

module.exports = {RaumkernelObserver, deadline};
