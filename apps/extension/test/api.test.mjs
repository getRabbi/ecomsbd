import assert from 'node:assert/strict';
import test from 'node:test';

import {
  claimPairing,
  ExtensionApiError,
  failureKind,
  fetchRules,
  sendCapture,
  unlink,
} from '../src/shared/api.ts';

function fakeFetch(respond) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : undefined });
    return respond(url, init);
  };
  return { calls, config: { baseUrl: 'https://api.example.test/', fetch } };
}

const json = (status, body, headers = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });

test('pairing claims the code as an extension, without credentials', async () => {
  const { calls, config } = fakeFetch(() => json(200, { connection_id: 'c', ingest_key: 'k', key_last4: '1234' }));
  const paired = await claimPairing(config, { code: '123456', installId: 'ext-1', appVersion: '4.0.0' });
  assert.equal(paired.key_last4, '1234');
  assert.equal(calls[0].url, 'https://api.example.test/v1/devices/pair/claim');
  assert.deepEqual(calls[0].body, { code: '123456', install_id: 'ext-1', platform: 'extension', app_version: '4.0.0', covered_packages: [] });
  assert.equal(calls[0].init.headers.Authorization, undefined);
  assert.equal(calls[0].init.credentials, 'omit');
});

test('capture sends the key and the Idempotency-Key to the extension route', async () => {
  const answer = { draft_id: 'd', status: 'READY_FOR_REVIEW', missing_fields: [], accepted: 1, duplicates: 0, open_path: '/quick-order?draft=d', replayed: false };
  const { calls, config } = fakeFetch(() => json(200, answer));
  const body = { host: 'web.whatsapp.com', origin: 'SELECTION', messages: [{ text: '01700000021' }] };
  assert.deepEqual(await sendCapture(config, 'key-1', 'ext-abc', body), answer);
  assert.equal(calls[0].url, 'https://api.example.test/v1/intake/captures/extension');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer key-1');
  assert.equal(calls[0].init.headers['Idempotency-Key'], 'ext-abc');
  assert.deepEqual(calls[0].body, body);
});

test('rules: If-None-Match and 304', async () => {
  const { calls, config } = fakeFetch((_, init) =>
    init.headers['If-None-Match'] === '"v1"' ? new Response(null, { status: 304 }) : json(200, { version: 1 }, { ETag: '"v1"' }),
  );
  assert.deepEqual(await fetchRules(config, 'k', null), { changed: true, etag: '"v1"', body: { version: 1 } });
  assert.deepEqual(await fetchRules(config, 'k', '"v1"'), { changed: false });
  assert.equal(calls[1].init.headers['If-None-Match'], '"v1"');
});

test('errors carry the server code and map to what the panel says', async () => {
  const cases = [
    [json(401, { code: 'UNAUTHENTICATED' }), 'UNLINKED'],
    [json(403, { code: 'FEATURE_DISABLED', retryable: false }), 'DISABLED'],
    [json(404, { code: 'NOT_FOUND' }), 'INVALID_CODE'],
    [json(429, { code: 'RATE_LIMITED' }), 'RATE_LIMITED'],
    [new Response('oops', { status: 502 }), 'FAILED'],
  ];
  for (const [response, kind] of cases) {
    const { config } = fakeFetch(() => response);
    const error = await sendCapture(config, 'k', 'ext-1', { host: 'web.whatsapp.com', origin: 'SELECTION', messages: [{ text: 'x' }] }).catch((e) => e);
    assert.ok(error instanceof ExtensionApiError);
    assert.equal(failureKind(error), kind, String(response.status));
  }
  const { config } = fakeFetch(() => {
    throw new TypeError('Failed to fetch');
  });
  const offline = await fetchRules(config, 'k', null).catch((e) => e);
  assert.equal(failureKind(offline), 'OFFLINE');
  assert.equal(offline.retryable, true);
  assert.equal(failureKind(new Error('x')), 'FAILED');
});

test('unlink treats an already revoked key as done', async () => {
  const { calls, config } = fakeFetch(() => json(401, { code: 'UNAUTHENTICATED' }));
  await unlink(config, 'k');
  assert.equal(calls[0].url, 'https://api.example.test/v1/intake/unlink');
  const failing = fakeFetch(() => json(500, { code: 'INTERNAL' }));
  await assert.rejects(unlink(failing.config, 'k'), ExtensionApiError);
});
