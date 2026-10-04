'use strict';

const {StateStore} = require('./state');
const {createApi} = require('./api');
const {RaumkernelObserver} = require('./adapter');
const {Controller} = require('./control');
const fs = require('node:fs');

const store = new StateStore();
let observer;
let controller;
if (process.env.DISCOVERY_MODE !== 'off') {
  const {Raumkernel} = require('node-raumkernel');
  const kernel = new Raumkernel();
  kernel.createLogger(1);
  if (process.env.RAUMFELD_HOST) {
    if (!/^[a-zA-Z0-9.-]+$/.test(process.env.RAUMFELD_HOST)) throw new Error('Invalid RAUMFELD_HOST');
    kernel.settings.raumfeldHost = process.env.RAUMFELD_HOST;
  }
  observer = new RaumkernelObserver(kernel, store);
  controller = new Controller(store, observer, {streamAddress: process.env.LAN_ADDRESS,
    allowedRooms: () => {
      try { return JSON.parse(fs.readFileSync(process.env.CONFIG_PATH || '/data/config.json', 'utf8')).rooms.map(r => r.id); }
      catch { return []; }
    }});
  observer.start();
}
const server = createApi(store, {token: process.env.API_TOKEN, controller});
server.listen(Number(process.env.API_PORT || 8787), process.env.API_BIND || '127.0.0.1', () => {
  console.log('Raumfeld API ready; playback requires explicit room selection and current ownership');
});
function shutdown() {
  observer?.stop();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
