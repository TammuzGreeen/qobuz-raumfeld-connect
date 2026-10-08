'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function render(status) {
  const elements = new Map();
  const node = () => ({children: [], textContent: '',
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; }});
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: node, createTextNode: text => ({textContent: text}),
  };
  const context = vm.createContext({document, sessionStorage: {getItem: () => ''},
    fetch: async () => ({ok: true, json: async () => status})});
  const html = fs.readFileSync('qobuz/index.html', 'utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  await vm.runInContext('refresh()', context);
  return elements;
}

const status = {auth: 'connected', lanAddress: '192.0.2.2', lanAddressLocal: true,
  raumfeldReady: true, selectedRooms: ['r'], advertised: [], quality: 6,
  rooms: [{id: 'r', name: '<script>not executable</script>', source: 'spotify',
    rendererIds: ['p'], zoneId: null, fresh: false,
    unavailableReasons: ['physical_observation_missing']}],
  observationErrors: [{id: 'p', action: 'QueryLastChange', code: 'ECONNRESET'}]};

test('setup explains freshness failure independently of the Spotify label using text nodes', async () => {
  const elements = await render(status);
  const text = elements.get('rooms').children[0].children[1].textContent;
  assert.match(text, /spotify/);
  assert.match(text, /waiting for a complete physical renderer observation/);
  assert.match(text, /QueryLastChange read failed \(ECONNRESET\)/);
  assert.match(text, /<script>not executable<\/script>/);
  assert.equal(elements.get('message').textContent, '0 speaker(s) advertised in Qobuz.');
});

test('setup reports non-local LAN address and receiver port conflicts', async () => {
  const elements = await render({...status, lanAddressLocal: false,
    receiverErrors: {r: 'receiver_port_in_use'}});
  assert.match(elements.get('discovery').textContent, /not assigned to this Docker host/);
  assert.match(elements.get('rooms').children[0].children[1].textContent, /receiver port is already in use/);
});

test('fresh advertised Spotify room is not labelled unavailable', async () => {
  const elements = await render({...status, advertised: ['r'], observationErrors: [],
    rooms: [{...status.rooms[0], fresh: true, unavailableReasons: []}]});
  const text = elements.get('rooms').children[0].children[1].textContent;
  assert.match(text, /spotify/);
  assert.doesNotMatch(text, /unavailable/);
  assert.equal(elements.get('message').textContent, '1 speaker(s) advertised in Qobuz.');
});

test('setup distinguishes cloud activation failure from fresh speaker availability', async () => {
  const elements = await render({...status, advertised: ['r'], observationErrors: [],
    rooms: [{...status.rooms[0], fresh: true, unavailableReasons: []}],
    receiverConnections: {r: {stage: 'session_started', cloudConnected: true, cloudActive: false}},
    connectEvents: [{event: 'cloud_server_error', code: 1003}]});
  const text = elements.get('rooms').children[0].children[1].textContent;
  assert.match(text, /cloud has not activated this receiver/);
  assert.doesNotMatch(text, /unavailable/);
  assert.match(elements.get('message').textContent, /cloud_server_error \(1003\)/);
});
