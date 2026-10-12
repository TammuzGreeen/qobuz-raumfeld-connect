'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {parseStringPromise, processors} = require('xml2js');
const DeviceClient = require('node-raumkernel/lib/lib.external.upnp-device-client');
const {rendererCommand} = require('../raumkernel/renderer-commands');

test('actual pinned UPnP client sends declared instance and speed on command HTTP requests', async t => {
  const received = [];
  const server = http.createServer(async(req,res)=>{
    const chunks=[];
    for await(const chunk of req)chunks.push(chunk);
    const body=Buffer.concat(chunks);
    const parsed=await parseStringPromise(body.toString(),{tagNameProcessors:[processors.stripPrefix]});
    const soap=parsed.Envelope.Body[0];
    const action=Object.keys(soap)[0],params=soap[action][0];
    received.push({action,params,soapAction:req.headers.soapaction});
    res.writeHead(200,{'Content-Type':'text/xml'});
    res.end('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><Response/></s:Body></s:Envelope>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const client=new DeviceClient('http://127.0.0.1/unused');
  client.deviceDescription={services:{}};
  const definitions={AVTransport:['SetAVTransportURI','Play','Pause','Stop','Seek'],RenderingControl:['SetVolume','SetRoomVolume','GetRoomVolume']};
  for(const [service,actions] of Object.entries(definitions)){
    const id='urn:upnp-org:serviceId:'+service;
    client.deviceDescription.services[id]={serviceType:`urn:schemas-upnp-org:service:${service}:1`,controlURL:`http://127.0.0.1:${server.address().port}/control`};
    client.serviceDescriptions[id]={actions:Object.fromEntries(actions.map(action=>[action,{outputs:[]}]))};
  }
  const device={callAction:(...args)=>new Promise((resolve,reject)=>client.callAction(...args,(error,result)=>error?reject(error):resolve(result)))};
  for(const [method,args] of [
    ['setAvTransportUri',['http://192.0.2.2:8790/audio/'+'a'.repeat(32),'<DIDL-Lite/>']],
    ['play',[]],['pause',[]],['stop',[]],['seek',['REL_TIME','00:01:05']],['setVolume',[20]],
    ['setRoomVolume',['uuid:test-room',21]],['getRoomVolume',['uuid:test-room']],
  ])await rendererCommand(device,method,args);
  assert.deepEqual(received.map(r=>r.action),['SetAVTransportURI','Play','Pause','Stop','Seek','SetVolume','SetRoomVolume','GetRoomVolume']);
  for(const r of received){
    assert.deepEqual(r.params.InstanceID,['0']);
    assert.ok(r.soapAction.endsWith('#'+r.action+'"'));
  }
  assert.deepEqual(received[0].params.CurrentURIMetaData,['<DIDL-Lite/>']);
  assert.deepEqual(received[1].params.Speed,['1']);
  assert.deepEqual(received[4].params.Unit,['REL_TIME']);
  assert.deepEqual(received[4].params.Target,['00:01:05']);
  assert.deepEqual(received[5].params.Channel,['Master']);
  assert.deepEqual(received[5].params.DesiredVolume,['20']);
  assert.deepEqual(received[6].params.Room,['uuid:test-room']);
  assert.deepEqual(received[6].params.DesiredVolume,['21']);
  assert.equal(received[6].params.Channel,undefined);
  assert.deepEqual(received[7].params.Room,['uuid:test-room']);
});

test('actual pinned SOAP connection reset is surfaced once without fallback setter',async t=>{
  let requests=0;
  const server=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const parsed=await parseStringPromise(Buffer.concat(chunks).toString(),{tagNameProcessors:[processors.stripPrefix]});
    assert.ok(parsed.Envelope.Body[0].SetRoomVolume);
    requests++;
    req.socket.destroy();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const client=new DeviceClient('http://127.0.0.1/unused'),id='urn:upnp-org:serviceId:RenderingControl';
  client.deviceDescription={services:{[id]:{serviceType:'urn:schemas-upnp-org:service:RenderingControl:1',controlURL:`http://127.0.0.1:${server.address().port}/control`}}};
  client.serviceDescriptions[id]={actions:{SetRoomVolume:{outputs:[]}}};
  const device={callAction:(...args)=>new Promise((resolve,reject)=>client.callAction(...args,(error,result)=>error?reject(error):resolve(result)))};
  await assert.rejects(rendererCommand(device,'setRoomVolume',['uuid:test-room',36]),error=>error.code==='ECONNRESET');
  assert.equal(requests,1);
});
