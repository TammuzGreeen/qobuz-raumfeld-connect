'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {Controller} = require('../raumkernel/control');

function setup({grouped = false, unassigned = false, timeoutMs = 50, handoffMs = 40} = {}) {
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
  device.callAction = async(service, action, params) => {
    assert.equal(params.InstanceID,0);
    const commands = {
      SetAVTransportURI:()=>device.setAvTransportUri(params.CurrentURI,params.CurrentURIMetaData),
      Play:()=>{assert.equal(params.Speed,'1');return device.play();},
      Pause:()=>device.pause(),Stop:()=>device.stop(),
      Seek:()=>device.seek(params.Unit,params.Target),
      SetVolume:()=>{assert.equal(params.Channel,'Master');return device.setVolume(params.DesiredVolume);},
    };
    return commands[action]();
  };
  const observer = {busy:false, devices:new Map([['zone-1',{device}],['physical-1',{device}]]),
    poll:async()=>observe(),kernel:{managerDisposer:{zoneManager:{
      async connectRoomToZone() {
        calls.push('create-zone');
        config.zoneConfig={zones:[{zone:[{$:{udn:'zone-1'},room:[room]}]}]};
      },
    }}}};
  const controller = new Controller(store,observer,{allowedRooms:()=>['room-1'],streamAddress:'192.0.2.2',now:()=>now,timeoutMs,handoffMs,handoffPollMs:5});
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
  assert.equal(loss(s).command,'SetAVTransportURI');
  assert.equal(loss(s).commandCode,'ECOMMANDTIMEOUT');
});

const loss = s => s.controller.decorate(s.store.snapshot()).rooms[0].lastOwnershipLoss;
function delayedHandoff(s, afterPoll) {
  const poll=s.observer.poll;
  let loaded=false, rounds=0;
  const load=s.device.setAvTransportUri;
  s.device.setAvTransportUri=async(...args)=>{await load(...args);loaded=true;};
  s.observer.poll=async()=>{
    await poll();
    if(loaded){
      rounds++;
      s.store.observe('physical-1',{AVTransportURI:'spotify://playback',CurrentTransportState:'PAUSED_PLAYBACK'});
      await afterPoll(rounds);
    }
  };
}
test('explicit Play waits for fresh departure of selected Spotify without replaying commands',async()=>{
  const s=setup();const {token}=await s.select();
  delayedHandoff(s,async round=>{
    if(round>=3)s.store.observe('physical-1',{AVTransportURI:'http://192.0.2.2:8790/audio/'+'a'.repeat(32),CurrentTransportState:'PLAYING'});
  });
  await s.play(token);
  assert.deepEqual(s.calls,['load','play']);
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].owned,true);
  assert.equal(loss(s),null);
  s.native();await assert.rejects(s.command(token,'pause'),/ownership_lost/);
});
test('persistent Spotify fails within bounded handoff without Play, Stop or retries',async()=>{
  const s=setup();const {token}=await s.select();delayedHandoff(s,async()=>{});
  await assert.rejects(s.play(token),/spotify_still_active/);
  assert.equal(loss(s).reason,'spotify_still_active');
  assert.deepEqual(s.calls,['load']);
});
function delayedVirtualHandoff(s, afterPoll) {
  const poll=s.observer.poll,load=s.device.setAvTransportUri;
  let loaded=false,rounds=0;
  s.device.setAvTransportUri=async(...args)=>{await load(...args);loaded=true;};
  s.observer.poll=async()=>{
    await poll();
    if(loaded){
      rounds++;
      s.store.observe('zone-1',{AVTransportURI:'spotify://playback',CurrentTransportState:'PAUSED_PLAYBACK'});
      await afterPoll(rounds);
    }
  };
}
test('initial handoff waits for delayed virtual URI before issuing one Play',async()=>{
  const s=setup();const {token}=await s.select();
  delayedVirtualHandoff(s,async round=>{
    if(round>=3)s.store.observe('zone-1',{AVTransportURI:'http://192.0.2.2:8790/audio/'+'a'.repeat(32),CurrentTransportState:'PLAYING'});
  });
  await s.play(token);
  assert.deepEqual(s.calls,['load','play']);
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].owned,true);
});
test('missing virtual confirmation fails closed without Play or retry',async()=>{
  const s=setup();const {token}=await s.select();delayedVirtualHandoff(s,async()=>{});
  await assert.rejects(s.play(token),/source_not_confirmed/);
  assert.equal(loss(s).rendererRole,'virtual');
  assert.deepEqual(s.calls,['load']);
});
test('unexpected virtual source during initial handoff is not tolerated',async()=>{
  const s=setup();const {token}=await s.select();
  delayedVirtualHandoff(s,async()=>s.store.observe('zone-1',{AVTransportURI:'https://example.test/other-source'}));
  await assert.rejects(s.play(token),/ownership_lost/);
  assert.equal(loss(s).reason,'unexpected_source_uri');
  assert.deepEqual(s.calls,['load']);
});
test('new Spotify session during handoff immediately revokes ownership',async()=>{
  const s=setup();const {token}=await s.select();delayedHandoff(s,async()=>s.native());
  await assert.rejects(s.play(token),/ownership_lost/);
  assert.equal(loss(s).reason,'unexpected_source_uri');
  assert.deepEqual(s.calls,['load']);
});
test('handoff does not extend selection expiry or ignore failed fresh observations',async()=>{
  const expired=setup();const a=await expired.select();
  delayedHandoff(expired,async()=>expired.advance(30001));
  await assert.rejects(expired.play(a.token),/ownership_lost/);
  assert.equal(loss(expired).reason,'lease_expired');
  assert.deepEqual(expired.calls,['load']);
  const stale=setup();const b=await stale.select();
  delayedHandoff(stale,async()=>{stale.store.renderers.get('physical-1').observedAt=0;});
  await assert.rejects(stale.play(b.token),/state_unavailable/);
  assert.deepEqual(stale.calls,['load']);
});
test('ownership diagnostics preserve takeover reason across release and rejected retries',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);s.native();
  assert.deepEqual(loss(s),{reason:'unexpected_source_uri',at:loss(s).at,phase:'owned',rendererRole:'physical',source:'spotify',uriKind:'spotify'});
  const original=loss(s);
  await s.command(token,'release');
  await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
  await assert.rejects(s.select(),/selection_already_used/);
  assert.deepEqual(loss(s),original);
  await s.select('fresh-selection-0002');
  assert.equal(loss(s),null);
  assert.deepEqual(s.calls,['load','play']);
});
test('unexpected internal physical URI remains rejected and records only safe categories',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);
  const uri='dlna-playcontainer://private-device/path?token=private-secret';
  s.store.emit('source','physical-1',uri);
  assert.equal(loss(s).reason,'unexpected_source_uri');
  assert.equal(loss(s).uriKind,'internal_transport');
  assert.equal(loss(s).rendererRole,'physical');
  assert.ok(!JSON.stringify(loss(s)).includes('private'));
  await assert.rejects(s.command(token,'pause'),/ownership_lost/);
  assert.deepEqual(s.calls,['load','play']);
});
test('ownership diagnostics distinguish expiry, host loss and command timeout',async()=>{
  const expired=setup();const a=await expired.select();expired.advance(30001);
  await assert.rejects(expired.command(a.token,'heartbeat'),/ownership_lost/);
  assert.equal(loss(expired).reason,'lease_expired');
  assert.equal(loss(expired).phase,'selection');
  const lost=setup();await lost.select();lost.store.hostLost();
  assert.equal(loss(lost).reason,'host_lost');
  const timeout=setup();const b=await timeout.select();timeout.device.setAvTransportUri=()=>new Promise(()=>{});
  await assert.rejects(timeout.play(b.token),/command_failed_or_timed_out/);
  assert.equal(loss(timeout).reason,'command_failed_or_timed_out');
  assert.equal(loss(timeout).phase,'transition');
});
test('ownership diagnostic storage is bounded and snapshots do not expose mutable entries',async()=>{
  const s=setup();await s.select();s.controller.revoke('room-1','released');
  const snapshot=loss(s);snapshot.reason='changed';
  assert.equal(loss(s).reason,'released');
  for(let i=0;i<60;i++){
    const id=`diagnostic-room-${i}`;
    s.controller.leases.set(id,{pending:true,physicalIds:[]});
    s.controller.revoke(id,'released');
  }
  assert.equal(s.controller.ownershipLosses.size,50);
  assert.equal(s.controller.leases.size,0);
});
test('command diagnostics retain only approved action and code, never fault text or arguments',async()=>{
  for(const error of [
    Object.assign(new Error('private URI and credentials'),{code:'EUPNP',errorCode:'714'}),
    Object.assign(new Error('private URI and credentials'),{code:'ECONNRESET'}),
    Object.assign(new Error('private URI and credentials'),{code:'private-secret',errorCode:'private-secret'}),
  ]){
    const s=setup();const {token}=await s.select();
    s.device.setAvTransportUri=async()=>{throw error;};
    await assert.rejects(s.play(token),/command_failed_or_timed_out/);
    const record=loss(s);
    assert.equal(record.command,'SetAVTransportURI');
    assert.equal(record.commandCode,error.code==='private-secret'?'command_failed':error.code);
    assert.equal(record.upnpErrorCode,error.code==='EUPNP'?714:undefined);
    assert.ok(!JSON.stringify(record).includes('private'));
    await s.command(token,'release');
    assert.deepEqual(loss(s),record);
    assert.deepEqual(s.calls,[]);
  }
});
test('Play fault is distinguished from URI loading without retry or Stop',async()=>{
  const s=setup();const {token}=await s.select();
  s.device.play=async()=>{throw Object.assign(new Error('private fault'),{code:'EUPNP',errorCode:'701'});};
  await assert.rejects(s.play(token),/command_failed_or_timed_out/);
  assert.equal(loss(s).command,'Play');
  assert.equal(loss(s).phase,'owned');
  assert.equal(loss(s).upnpErrorCode,701);
  assert.deepEqual(s.calls,['load']);
});
test('definite Seek 710 rejection preserves ownership and reports not applied',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);
  s.device.seek=async()=>{throw Object.assign(new Error('private fault'),{code:'EUPNP',errorCode:'710'});};
  assert.deepEqual(await s.command(token,'seek',{value:65000}),{accepted:false,applied:false,error:'seek_mode_not_supported',upnpErrorCode:710});
  assert.equal(loss(s),null);
  await s.command(token,'heartbeat');await s.command(token,'pause');
  assert.deepEqual(s.calls,['load','play','pause']);
  s.native();await assert.rejects(s.command(token,'resume'),/ownership_lost/);
});
test('seek resets and other SOAP faults still revoke without retry',async()=>{
  for(const error of [Object.assign(new Error('fault'),{code:'EUPNP',errorCode:'701'}),Object.assign(new Error('reset'),{code:'ECONNRESET'})]){
    const s=setup();const {token}=await s.select();await s.play(token);
    s.device.seek=async()=>{throw error;};
    await assert.rejects(s.command(token,'seek',{value:65000}),/command_failed_or_timed_out/);
    await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
    assert.deepEqual(s.calls,['load','play']);
  }
});
test('Seek 710 cannot preserve a lease revoked concurrently by Spotify',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);
  s.device.seek=async()=>{s.native();throw Object.assign(new Error('fault'),{code:'EUPNP',errorCode:'710'});};
  await assert.rejects(s.command(token,'seek',{value:65000}),/ownership_lost/);
  assert.equal(loss(s).reason,'unexpected_source_uri');
});
const forwardingURI = 'http://192.0.2.1:55001/zone-1/room-1/physical-1/audio.flac?punch=1';
test('topology-linked physical forwarding preserves only freshly confirmed virtual ownership',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);
  s.store.observe('physical-1',{AVTransportURI:forwardingURI,TransportState:'PLAYING'});
  assert.equal(loss(s),null);
  assert.equal(s.controller.decorate(s.store.snapshot()).rooms[0].owned,true);
  assert.equal(s.controller.physicalForwarding('room-1',s.controller.leases.get('room-1'),'zone-1',forwardingURI),false);
  s.store.emit('source','zone-1','https://example.test/native');
  await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
  assert.equal(loss(s).rendererRole,'virtual');
});
test('forwarding recognition rejects unrelated hosts, identifiers and ambiguous URL shapes',async()=>{
  for(const value of [
    forwardingURI.replace('192.0.2.1','192.0.2.9'),
    forwardingURI.replace('55001','47365'),
    forwardingURI.replace('zone-1/','wrong-zone/'),
    forwardingURI.replace('room-1/','wrong-room/'),
    forwardingURI.replace('physical-1/','wrong-physical/'),
    forwardingURI.replace('physical-1/','zone-1/'),
    forwardingURI.replace('room-1/','prefix-room-1/'),
    forwardingURI+'#fragment',forwardingURI+'&other=2',
    forwardingURI.replace('punch=1','punch=secret'),
    forwardingURI.replace('punch=1','sequence=1'),
    forwardingURI.split('?')[0],
    forwardingURI.replace('zone-1/room-1/','room-1/zone-1/'),
    forwardingURI.replace('http://','http://user:password@'),
    forwardingURI.replace('http:','https:'),
    forwardingURI.replace('physical-1/','physical-1%2fextra/'),
    forwardingURI.replace('audio.flac','spotifyconnect'),
  ]){
    const s=setup();const {token}=await s.select();await s.play(token);
    s.store.emit('source','physical-1',value);
    await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
    assert.equal(loss(s).reason,'unexpected_source_uri');
    assert.deepEqual(s.calls,['load','play']);
  }
});
test('forwarding recognition cannot bridge stale virtual state, expired topology or grouping',async()=>{
  for(const invalidate of [
    s=>{s.store.renderers.get('zone-1').observedAt=0;},
    s=>{s.store.topologyAt=0;},
    s=>{s.store.zones[0].roomIds.push('another-room');},
    s=>{s.store.raw.set('zone-1',{AVTransportURI:'https://example.test/other'});},
    s=>{s.store.raw.set('zone-1',{});},
    s=>{s.store.raw.set('zone-1',{AVTransportURI:'spotify://new'});},
  ]){
    const s=setup();const {token}=await s.select();await s.play(token);invalidate(s);
    s.store.emit('source','physical-1',forwardingURI);
    await assert.rejects(s.command(token,'heartbeat'),/ownership_lost/);
    assert.equal(loss(s).reason,'unexpected_source_uri');
  }
});
test('physical Spotify still revokes after a recognized forwarding stream',async()=>{
  const s=setup();const {token}=await s.select();await s.play(token);
  s.store.observe('physical-1',{AVTransportURI:forwardingURI,TransportState:'PLAYING'});
  s.native();await assert.rejects(s.command(token,'pause'),/ownership_lost/);
  assert.equal(loss(s).source,'spotify');
  assert.deepEqual(s.calls,['load','play']);
});
