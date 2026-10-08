'use strict';

const {parseStringPromise, processors} = require('xml2js');

function deadline(promise, ms, timeoutError = new Error('Observation timeout')) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(timeoutError), ms);
  })]).finally(() => clearTimeout(timer));
}

async function readAction(device, action) {
  try { return await device.callAction('AVTransport', action, {InstanceID: 0}); }
  catch (error) { throw Object.assign(new Error('Observation failed'), {code: error?.code, observationAction: action}); }
}

async function sourceInfo(device, fetchImpl, timeoutMs) {
  try {
    const media = await device.callAction('AVTransport', 'GetMediaInfo', {InstanceID: 0});
    if (typeof media?.CurrentURI !== 'string') throw new Error('Missing source URI');
    return {AVTransportURI: media.CurrentURI};
  } catch (error) {
    // Raumfeld physical renderers do not implement GetMediaInfo. Their position
    // response also omits TrackURI. Never substitute cached event data: query a
    // new complete LastChange snapshot through the read-only UPnP control API.
    if (error?.code !== 'ENOACTION') {
      throw Object.assign(new Error('Source observation failed'), {code: error?.code, observationAction: 'GetMediaInfo'});
    }
  }
  try {
    const service = Object.values(device.upnpClient?.deviceDescription?.services || {})
      .find(s => /^urn:schemas-upnp-org:service:AVTransport:\d+$/.test(s.serviceType));
    if (!service?.controlURL) throw new Error('Missing AVTransport control URL');
    const response = await fetchImpl(service.controlURL, {
      method: 'POST', signal: AbortSignal.timeout(timeoutMs),
      headers: {'Content-Type': 'text/xml; charset="utf-8"',
        SOAPACTION: '"urn:schemas-upnp-org:control-1-0#QueryStateVariable"'},
      body: '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:QueryStateVariable xmlns:u="urn:schemas-upnp-org:control-1-0"><varName>LastChange</varName></u:QueryStateVariable></s:Body></s:Envelope>',
    });
    if (!response.ok) throw new Error('LastChange unavailable');
    const options = {tagNameProcessors: [processors.stripPrefix]};
    const soap = await parseStringPromise(await response.text(), options);
    const eventText = soap?.Envelope?.Body?.[0]?.QueryStateVariableResponse?.[0]?.LastChange?.[0];
    if (typeof eventText !== 'string') throw new Error('Missing LastChange');
    const event = await parseStringPromise(eventText, options);
    const instances = event?.Event?.InstanceID;
    const matches = Array.isArray(instances) ? instances.filter(i => i.$?.val === '0') : [];
    const instance = matches.length === 1 ? matches[0] : null;
    const uri = instance?.AVTransportURI?.[0]?.$?.val;
    if (typeof uri !== 'string' || instance.AVTransportURI.length !== 1) throw new Error('Missing source URI');
    return {AVTransportURI: uri};
  } catch (error) {
    throw Object.assign(new Error('Source observation failed'), {code: error?.code, observationAction: 'QueryLastChange'});
  }
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
        this.store.observe(device.udn(), state, {refresh: false});
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
            const [source, transport, position] = await deadline(Promise.all([
              sourceInfo(entry.device, this.fetch, this.timeoutMs),
              readAction(entry.device, 'GetTransportInfo'), readAction(entry.device, 'GetPositionInfo'),
            ]), this.timeoutMs);
            if (!valid() || this.devices.get(id) !== entry || entry.version !== version) return;
            let volume = null;
            try { volume = await deadline(entry.device.getVolume(), this.timeoutMs); } catch {}
            if (!valid() || this.devices.get(id) !== entry || entry.version !== version) return;
            this.store.observe(id, {...source,
              TrackURI: position.TrackURI, CurrentTransportState: transport.CurrentTransportState,
              RelTime: position.RelTime, TrackDuration: position.TrackDuration, Volume: volume});
          } catch (error) {
            // Diagnostics contain only bounded codes, never upstream messages,
            // names, transport URLs, or SOAP response bodies.
            if (valid() && this.devices.get(id) === entry) this.store.observationFailed(id, error);
          }
        }));
        if (!valid()) return;
      }
    } catch { if (valid()) this.store.topologyAt = null; }
    finally { this.busy = false; }
  }
  invalidateReads(ids) {
    for (const id of ids) {
      const entry = this.devices.get(id);
      if (entry) entry.version++;
    }
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

module.exports = {RaumkernelObserver, deadline, sourceInfo};
