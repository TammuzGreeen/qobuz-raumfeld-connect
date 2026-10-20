'use strict';
// Experiment-only, read-only topology/source observer. No Companion/Controller.
const http = require('node:http');
const {timingSafeEqual} = require('node:crypto');
const {StateStore} = require('../../raumkernel/state');
const {RaumkernelObserver} = require('../../raumkernel/adapter');
if (process.env.CAST_EXPERIMENT !== '1') process.exit(0);
const token = process.env.CAST_API_TOKEN;
if (!token || token.length < 32) throw new Error('experimental_token_required');
const expected = Buffer.from('Bearer ' + token);
const store = new StateStore();
let observer;
if (process.env.CAST_OBSERVE_ROOMS === '1') {
  const {Raumkernel} = require('node-raumkernel');
  const kernel = new Raumkernel();
  kernel.createLogger(1);
  observer = new RaumkernelObserver(kernel, store);
  observer.start();
}
const server = http.createServer((req, res) => {
  const given = Buffer.from(req.headers.authorization || '');
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    res.writeHead(401); return res.end();
  }
  if (req.method !== 'GET' || req.url !== '/snapshot') { res.writeHead(405); return res.end(); }
  const {host, ...snapshot} = store.snapshot(); // No address or raw URI evidence.
  res.writeHead(200, {'Content-Type':'application/json', 'Cache-Control':'no-store'});
  res.end(JSON.stringify(snapshot));
});
server.listen(8817, '127.0.0.1');
function stop() { observer?.stop(); server.close(() => process.exit(0)); }
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
