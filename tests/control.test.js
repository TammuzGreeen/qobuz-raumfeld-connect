'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {Controller} = require('../raumkernel/control');

function setup({grouped = false, unassigned = false, timeoutMs = 50} = {}) {
  let now = Date.now(), uri = 'spotify://playback';
  const calls = [];
  const store = new StateStore({now: () => now});
  store.hostFound('192.0.2.1');
  const room = {$: {udn:'room-1',name:'Living'},renderer:[{$:{udn:'physical-1'}}]};
  const config = unassigned ? {zoneConfig:{unassignedRooms:[{room:[room]}]}}
    : {zoneConfig:{zones:[{zone:[{$:{udn:'zone-1'},room:grouped?[room,{$:{udn:'room-2'},renderer:[{$:{udn:'physical-2'}}]}]:[room]}]}]}};
  const observe = () => {
    store.topology(config);
    for (const id of ['physical-1','zone-1','physical-2']) store.observe(id,{AVTransportURI:uri,CurrentTransportState:'PLAYING'});
  };
  observe();
  const device = {
    async setAvTransportUri(value, metadata) { calls.push('load'); assert.ok(metadata.includes('&amp;')); uri=value; },
    async play() {calls.push('play');}, async stop() {calls.push('stop');}, async pause() {calls.push('pause');},
    async seek(unit, target) {calls.push(['seek',unit,target]);}, async setVolume(value) {calls.push(['volume',value]);},
  };
  const observer = {busy:false, devices:new Map([['zone-1',{device}],['physical-1',{device}]]),
    poll:async()=>observe(),kernel:{managerDisposer:{zoneManager:{
      async connectRoomToZone() {
        calls.push('create-zone');
        config.zoneConfig={zones:[{zone:[{$:{udn:'zone-1'},room:[room]}]}]};
      },
    }}}};
  const controller = new Controller(store,observer,{allowedRooms:()=>['room-1'],streamAddress:'192.0.2.2',now:()=>now,timeoutMs});
  const select = async id => controller.dispatch({roomId:'room-1',action:'select',selectionId:id||'selection-0001'});
  const command = (token,action,extra={}) => controller.dispatch({roomId:'room-1',token,action,...extra});
  const play = token => command(token,'play',{url:'http://192.0.2.2:8790/audio/'+'a'.repeat(32),metadata:{title:'A & B'}});
  return {controller,store,observer,device,calls,select,command,play,advance:ms=>now+=ms,
    native:()=>{uri='spotify://new-session';store.emit('source','physical-1',uri);}};
}

test('no commands without selection; initial pause/stop/volume cannot disturb Spotify', async()=>{
  const s=setup();
  await assert.rejects(s.play('missing'),/ownership_lost/);
  const {token}=await s.select();
  for(const action of ['pause','stop','volume']) await assert.rejects(s.command(token,action,{value:20}),/play_required/);
  assert.deepEqual(s.calls,[]);
});
test('explicit Spotify selection loads then confirms source before play, controls only owned stream', async()=>{
  const s=setup(); const {token}=await s.select(); await s.play(token);
  await s.command(token,'pause'); await s.command(token,'resume');
  await s.command(token,'seek',{value:65000}); await s.command(token,'volume',{value:0});
  assert.deepEqual(s.calls,['load','play','pause','play',['seek','REL_TIME','00:01:05'],['volume',0]]);
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].source,'qobuz');
});
test('native takeover revokes all commands and cannot be repaired by replaying selection',async()=>{
  const s=setup(); const {token}=await s.select(); await s.play(token); s.native();
  for(const action of ['stop','pause','resume','heartbeat']) await assert.rejects(s.command(token,action),/ownership_lost/);
  await assert.rejects(s.select(),/selection_already_used/);
  assert.deepEqual(s.calls,['load','play']);
});
test('pending permission expires even with heartbeats',async()=>{
  const s=setup(); const {token}=await s.select();s.advance(29000);await s.command(token,'heartbeat');s.advance(1001);
  await assert.rejects(s.play(token),/ownership_lost/);assert.deepEqual(s.calls,[]);
});
test('grouped rooms never receive single-room takeover',async()=>{
  const s=setup({grouped:true});await assert.rejects(s.select(),/grouped_room_not_supported/);assert.deepEqual(s.calls,[]);
});
test('unassigned room creates zone only during explicit first play',async()=>{
  const s=setup({unassigned:true});const {token}=await s.select();assert.deepEqual(s.calls,[]);await s.play(token);
  assert.deepEqual(s.calls,['create-zone','load','play']);
});
test('explicit transition is visible only while its short-lived lease exists',async()=>{
  const s=setup();const {token}=await s.select();
  s.controller.leases.get('room-1').transition=true;
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].transitioning,true);
  s.advance(30001);
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].transitioning,false);
  await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
});
test('Spotify arriving during load prevents the following Play command',async()=>{
  const s=setup();const {token}=await s.select();s.device.setAvTransportUri=async()=>{s.calls.push('load');s.native();};
  await assert.rejects(s.play(token),/ownership_lost/);assert.deepEqual(s.calls,['load']);
});
test('host loss invalidates pending ownership without issuing Stop',async()=>{
  const s=setup();const {token}=await s.select();s.store.hostLost();await assert.rejects(s.play(token),/ownership_lost/);assert.deepEqual(s.calls,[]);
});
test('arbitrary stream URLs and invalid command values fail before mutation',async()=>{
  const s=setup();const {token}=await s.select();
  await assert.rejects(s.command(token,'play',{url:'http://127.0.0.1/admin'}),/invalid_stream/);
  await assert.rejects(s.command(token,'volume',{value:101}),/invalid_volume/);
  await assert.rejects(s.command(token,'seek',{value:-1}),/invalid_position/);
  assert.deepEqual(s.calls,[]);
});
test('uncertain mutation timeouts revoke ownership and do not retry',async()=>{
  const s=setup();const {token}=await s.select();s.device.setAvTransportUri=()=>new Promise(()=>{});
  await assert.rejects(s.play(token),/command_failed_or_timed_out/);
  await assert.rejects(s.play(token),/ownership_lost/);
});
