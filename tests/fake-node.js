'use strict';
// Test-only renderer harness. Never imported by production code.
const {StateStore} = require('../raumkernel/state');
const {Controller} = require('../raumkernel/control');
const {createApi} = require('../raumkernel/api');
const store = new StateStore();
let uri='spotify://playback', mode='PAUSED_PLAYBACK', volume=35;
let seekUnsupported=false, volumeReset=false;
let forwarding=false;
const topology={zoneConfig:{zones:[{zone:[{$:{udn:'zone'},room:[{$:{udn:'room',name:'Room'},renderer:[{$:{udn:'physical'}}]}]}]}]}};
store.hostFound('127.0.0.1');
const observe=()=>{store.topology(topology);for(const id of ['zone','physical'])store.observe(id,{AVTransportURI:forwarding&&id==='physical'?'http://127.0.0.1:55001/zone/room/physical/audio.flac?punch=1':uri,TransportState:mode,Volume:volume});};
observe();
const device={
  setAvTransportUri:async value=>{uri=value;}, play:async()=>{mode='PLAYING';},
  pause:async()=>{mode='PAUSED_PLAYBACK';}, stop:async()=>{mode='STOPPED';},
  seek:async()=>{if(seekUnsupported)throw Object.assign(new Error('Unsupported seek'),{code:'EUPNP',errorCode:'710'});}, setVolume:async value=>{volume=value;},
};
device.callAction=async(service,action,params)=>{
  if(params.InstanceID!==0)throw new Error('Missing test instance');
  const actions={SetAVTransportURI:()=>device.setAvTransportUri(params.CurrentURI),
    Play:()=>{if(params.Speed!=='1')throw new Error('Missing test speed');return device.play();},
    Pause:()=>device.pause(),Stop:()=>device.stop(),Seek:()=>device.seek(),
    SetVolume:()=>device.setVolume(params.DesiredVolume),
    SetRoomVolume:()=>{if(params.Room!=='room')throw new Error('Wrong room');if(volumeReset)throw Object.assign(new Error('reset'),{code:'ECONNRESET'});return device.setVolume(params.DesiredVolume);},
    GetRoomVolume:()=>{if(params.Room!=='room')throw new Error('Wrong room');return {CurrentVolume:String(volume)};}};
  return actions[action]();
};
const observer={busy:false,poll:async()=>observe(),devices:new Map([['zone',{device}],['physical',{device}]])};
const controller=new Controller(store,observer,{allowedRooms:()=>['room'],streamAddress:'127.0.0.1'});
const server=createApi(store,{token:'integration-test-token-12345678901234567890',controller});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
process.stdin.setEncoding('utf8');
process.stdin.on('data',data=>{
  if(data.trim()==='forwarding'){forwarding=true;observe();console.log('forwarding');return;}
  if(data.trim()==='volume-reset'){volumeReset=true;console.log('volume-reset');return;}
  if(data.trim()==='seek-unsupported'){seekUnsupported=true;console.log('seek-unsupported');return;}
  forwarding=false;uri='spotify://external';observe();console.log('native');
});
