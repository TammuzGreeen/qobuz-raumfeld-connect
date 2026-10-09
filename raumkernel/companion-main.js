'use strict';

// Separate entrypoint: deliberately does not load Controller or BindingService.
const fs = require('node:fs');
const {StateStore} = require('./state');
const {RaumkernelObserver} = require('./adapter');
const {Companion, createCompanionApi} = require('./companion');

const store = new StateStore();
let observer = {devices: new Map()};
if (process.env.DISCOVERY_MODE !== 'off') {
  const {Raumkernel} = require('node-raumkernel');
  const kernel = new Raumkernel();
  kernel.createLogger(1);
  if (process.env.RAUMFELD_HOST) {
    if (!/^[a-zA-Z0-9.-]+$/.test(process.env.RAUMFELD_HOST)) throw new Error('Invalid RAUMFELD_HOST');
    kernel.settings.raumfeldHost = process.env.RAUMFELD_HOST;
  }
  observer = new RaumkernelObserver(kernel, store);
  observer.start();
}
const allowedRooms = () => {
  try {
    const settings = JSON.parse(fs.readFileSync(process.env.CONFIG_PATH || '/data/config.json', 'utf8'));
    return Array.isArray(settings.rooms) ? settings.rooms.map(r => r.id) : [];
  } catch { return []; }
};
const companion = new Companion(store, observer, {allowedRooms});
const server = createCompanionApi(companion, process.env.API_TOKEN);
// Companion mutation API is local-only, independent of speaker-facing discovery.
server.listen(Number(process.env.API_PORT || 8787), '127.0.0.1');
let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  observer.stop?.();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
