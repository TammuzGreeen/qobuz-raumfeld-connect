'use strict';

// Synthetic cross-process fixture only. No node-raumkernel init or LAN access.
const readline = require('node:readline');
const {StateStore} = require('../raumkernel/state');
const {Companion, createCompanionApi} = require('../raumkernel/companion');
const store = new StateStore();
store.hostFound('synthetic-host');
const room = {$: {udn: 'synthetic-room', name: 'Example'}, renderer: [{$: {udn: 'synthetic-physical'}}]};
const topology = assigned => store.topology({zoneConfig: assigned
  ? {zones: [{zone: [{$: {udn: 'synthetic-zone'}, room: [room]}]}]}
  : {unassignedRooms: [{room: [room]}]}});
topology(false);
let creations = 0;
const observer = {devices: new Map([['synthetic-zone', {device: {
  udn: () => 'synthetic-zone', upnpClient: {url: process.argv[2]},
}}]]), kernel: {getManager: () => ({zoneManager: {connectRoomToZone: async () => {
  creations++;
  topology(true);
}}})}};
const companion = new Companion(store, observer, {allowedRooms: () => ['synthetic-room']});
const server = createCompanionApi(companion, 's'.repeat(32));
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port: server.address().port})));
const input = readline.createInterface({input: process.stdin});
input.on('line', line => {
  if (line === 'native') store.emit('nativeSpotify', 'synthetic-physical');
  if (line === 'changed') observer.devices.get('synthetic-zone').device.upnpClient.url = process.argv[2].replace('/device.xml', '/changed.xml');
  if (line === 'missing') observer.devices.delete('synthetic-zone');
  console.log(JSON.stringify({creations}));
});
input.on('close', () => server.close(() => process.exit(0)));
