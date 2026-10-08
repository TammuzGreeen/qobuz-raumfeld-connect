'use strict';

const http = require('node:http');
const {timingSafeEqual} = require('node:crypto');
const {evaluate} = require('./arbitration');

function authenticated(header, token) {
  const given = Buffer.from(header || '');
  const expected = Buffer.from(`Bearer ${token}`);
  return given.length === expected.length && timingSafeEqual(given, expected);
}

function createApi(store, {token, controller = null, binding = null}) {
  if (!token || token.length < 32) throw new Error('API_TOKEN must contain at least 32 characters');
  const server = http.createServer(async (req, res) => {
    const send = (status, body) => {
      res.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      res.end(JSON.stringify(body));
    };
    if (req.method === 'GET' && req.url === '/healthz') return send(200, {status: 'ok'});
    if (!authenticated(req.headers.authorization, token)) return send(401, {error: 'unauthorized'});
    if (req.method === 'GET' && req.url === '/readyz') {
      const ready = store.snapshot().topologyFresh;
      return send(ready ? 200 : 503, {ready});
    }
    if (req.method === 'GET' && req.url === '/v1/state') return send(200, controller ? controller.decorate(store.snapshot()) : store.snapshot());
    if (req.method === 'POST' && ['/v1/arbitration/evaluate', '/v1/control', '/v1/binding'].includes(req.url)) {
      if (req.headers['content-type']?.split(';')[0] !== 'application/json') return send(415, {error: 'json_required'});
      let size = 0, chunks = [];
      try {
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 16384) { send(413, {error: 'body_too_large'}); return; }
          chunks.push(chunk);
        }
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (req.url === '/v1/binding') {
          if (!binding) return send(503, {error: 'binding_disabled'});
          if (!body || Array.isArray(body) || typeof body.roomId !== 'string' || !body.roomId.length ||
              body.roomId.length > 256 ||
              !['select','release','lookup','prepare','load','loaded_play','guard'].includes(body.action) ||
              Object.keys(body).some(k => !['roomId','action','selectionId','token'].includes(k)) ||
              (body.action !== 'select' && (typeof body.token !== 'string' || body.token.length !== 64))) {
            return send(400, {error: 'invalid_request'});
          }
          try { return send(200, {apiVersion: '1', ...await binding.dispatch(body)}); }
          catch (error) { return send(error.status || 503, {error: error.status ? error.message : 'binding_unavailable'}); }
        }
        if (req.url === '/v1/control') {
          if (!controller) return send(503, {error: 'control_disabled'});
          if (!body || Array.isArray(body) || typeof body.roomId !== 'string' || body.roomId.length > 256 ||
              !['select','heartbeat','release','play','pause','resume','stop','seek','volume'].includes(body.action) ||
              Object.keys(body).some(k => !['roomId','action','selectionId','token','url','metadata','value'].includes(k)) ||
              (body.action !== 'select' && typeof body.token !== 'string') ||
              (body.metadata && (typeof body.metadata !== 'object' || Array.isArray(body.metadata) ||
                Object.values(body.metadata).some(v => typeof v !== 'string' || v.length > 2000)))) {
            return send(400, {error: 'invalid_request'});
          }
          try { return send(200, {apiVersion: '1', ...await controller.dispatch(body)}); }
          catch (error) { return send(error.status || 503, {error: error.status ? error.message : 'control_unavailable'}); }
        }
        if (!body || typeof body.roomId !== 'string' || !body.roomId.length || body.roomId.length > 256 ||
            !['repair', 'automatic_takeover', 'explicit_takeover'].includes(body.intent) ||
            Object.keys(body).some(k => !['roomId', 'intent'].includes(k))) {
          return send(400, {error: 'invalid_request'});
        }
        return send(200, evaluate(store.snapshot(), body));
      } catch { return send(400, {error: 'invalid_json'}); }
    }
    send(404, {error: 'not_found'});
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  return server;
}

module.exports = {createApi};
