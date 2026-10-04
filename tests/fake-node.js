'use strict';
// Test-only renderer harness. Never imported by production code.
const {StateStore} = require('../raumkernel/state');
const {Controller} = require('../raumkernel/control');
const {createApi} = require('../raumkernel/api');
const store = new StateStore();
let uri='spotify://playback', mode='PAUSED_PLAYBACK', volume=35;
const topology={zoneConfig:{zones:[{zone:[{$:{udn:'zone'},room:[{$:{udn:'room',name:'Room'},renderer:[{$:{udn:'physical'}}]}]}]}]}};
store.hostFound('127.0.0.1');
const observe=()=>{store.topology(topology);for(const id of ['zone','physical'])store.observe(id,{AVTransportURI:uri,TransportState:mode,Volume:volume});};
observe();
const device={
  setAvTransportUri:async value=>{uri=value;}, play:async()=>{mode='PLAYING';},
  pause:async()=>{mode='PAUSED_PLAYBACK';}, stop:async()=>{mode='STOPPED';},
  seek:async()=>{}, setVolume:async value=>{volume=value;},
};
const observer={busy:false,poll:async()=>observe(),devices:new Map([['zone',{device}],['physical',{device}]])};
const controller=new Controller(store,observer,{allowedRooms:()=>['room'],streamAddress:'127.0.0.1'});
const server=createApi(store,{token:'integration-test-token-12345678901234567890',controller});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
process.stdin.setEncoding('utf8');
process.stdin.on('data',()=>{uri='spotify://external';observe();console.log('native');});
