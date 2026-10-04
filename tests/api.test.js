'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {StateStore} = require('../raumkernel/state');
const {createApi} = require('../raumkernel/api');
const Ajv = require('ajv');
const schema = require('../shared/api.schema.json');

test('API authentication, readiness, strict input and non-executable decisions', async t => {
  const token = 'test-only-'.repeat(4);
  const server = createApi(new StateStore(), {token});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const url = `http://127.0.0.1:${server.address().port}`;
  const headers = {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'};
  assert.equal((await fetch(url + '/healthz')).status, 200);
  assert.equal((await fetch(url + '/v1/state')).status, 401);
  assert.equal((await fetch(url + '/readyz', {headers})).status, 503);
  for (const body of ['{', '{}', '{"roomId":"r","intent":"repair","force":true}']) {
    assert.equal((await fetch(url + '/v1/arbitration/evaluate', {method: 'POST', headers, body})).status, 400);
  }
  const response = await fetch(url + '/v1/arbitration/evaluate', {method: 'POST', headers,
    body: JSON.stringify({roomId: 'r', intent: 'explicit_takeover'})});
  const decision = await response.json();
  assert.equal(decision.policyAllowed, false);
  assert.equal(decision.executable, false);
  const validate = new Ajv().compile(schema);
  assert.ok(validate(decision), JSON.stringify(validate.errors));
  assert.equal((await fetch(url + '/v1/play', {method: 'POST', headers})).status, 404);
  assert.throws(() => createApi(new StateStore(), {token: 'short'}));
});
