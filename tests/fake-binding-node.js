'use strict';
// Loopback-only two-process fixture. No discovery or real zone/renderer writes.
const {StateStore} = require('../raumkernel/state');
const {BindingService} = require('../raumkernel/binding');
const {createApi} = require('../raumkernel/api');
const readline = require('node:readline');
const store = new StateStore();
store.hostFound('127.0.0.1');
store.topology({zoneConfig: {zones: [{zone: [{$: {udn: 'synthetic-zone'},
  room: [{$: {udn: 'synthetic-room'}, renderer: [{$: {udn: 'synthetic-physical'}}]}]}]}]}});
store.observe('synthetic-physical', {AVTransportURI: 'spotify:synthetic-before-selection'});
const observer = {devices: new Map([['synthetic-zone', {device: {udn: () => 'synthetic-zone',
  upnpClient: {url: process.argv[2]}}}]]), kernel: {getManager: () => { throw new Error('No creation in this fixture'); }}};
const binding = new BindingService(store, observer, {allowedRooms: () => ['synthetic-room']});
const server = createApi(store, {token: 'synthetic-integration-token-1234567890', binding});
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
readline.createInterface({input: process.stdin}).on('line', line => {
  if (line === 'loaded') {
    store.observe('synthetic-physical', {AVTransportURI:
      'http://127.0.0.1:55000/synthetic-zone/synthetic-room/synthetic-physical/stream?punch=1'});
  } else if (line === 'paused') {
    store.observe('synthetic-physical', {AVTransportURI: '', CurrentTransportState: 'NO_MEDIA_PRESENT'});
  } else if (line === 'native') {
    store.emit('nativeSpotify', 'synthetic-physical');
    store.observe('synthetic-physical', {AVTransportURI: 'spotify:synthetic-takeover'});
  }
  console.log('observed');
});
