// node --test sdk/js/ecomsbd.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { Client, EcomsbdError, ReplayGuard, sign, verifyWebhook } from './ecomsbd.mjs';

function fakeFetch(status, body, headers = {}) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(body), { status, headers });
  };
  return { fn, calls };
}

const KEY = 'ec_live_0123456789abcdef0123456789abcdef.placeholder-secret-value-for-tests-only-xx';

test('source order sends bearer, idempotency key and the documented body', async () => {
  const fake = fakeFetch(201, { order_id: 'o1', created: true });
  const client = new Client({ apiKey: KEY, baseUrl: 'https://api.example.test/public/v1/', fetch: fake.fn });
  const result = await client.sendSourceOrder('src-1', 'WEB-1', { phone: '01700000000', items: [{ name: 'x', unit_price_paisa: 100 }] });
  assert.equal(result.order_id, 'o1');
  const [call] = fake.calls;
  assert.equal(call.url, 'https://api.example.test/public/v1/sources/src-1/orders');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.headers.Authorization, `Bearer ${KEY}`);
  assert.equal(call.init.headers['Idempotency-Key'], 'order-WEB-1');
  assert.deepEqual(JSON.parse(call.init.body), { external_order_id: 'WEB-1', payload: { phone: '01700000000', items: [{ name: 'x', unit_price_paisa: 100 }] } });
});

test('errors carry code and retry-after; a booked cancel is a conflict result', async () => {
  const limited = fakeFetch(429, { code: 'RATE_LIMITED', message_en: 'slow down' }, { 'retry-after': '12' });
  const client = new Client({ apiKey: KEY, baseUrl: 'https://api.example.test/public/v1', fetch: limited.fn });
  await assert.rejects(client.me(), (error) => error instanceof EcomsbdError && error.code === 'RATE_LIMITED' && error.retryAfter === 12);
  const conflict = fakeFetch(409, { result: 'CONFLICT', code: 'CANCELLED_AFTER_BOOKING' });
  const other = new Client({ apiKey: KEY, baseUrl: 'https://api.example.test/public/v1', fetch: conflict.fn });
  assert.equal((await other.setOrderStatus('o1', 'CANCELLED')).result, 'CONFLICT');
});

test('webhook verification rejects tampering and replays', () => {
  const secret = 'placeholder-signing-secret';
  const body = '{"id":"e1","type":"order.delivered"}';
  const header = sign(secret, body, 1000);
  assert.equal(verifyWebhook(secret, body, header, { now: 1000 }), true);
  assert.equal(verifyWebhook(secret, body + ' ', header, { now: 1000 }), false);
  assert.equal(verifyWebhook('wrong', body, header, { now: 1000 }), false);
  assert.equal(verifyWebhook(secret, body, header, { now: 1000 + 301 }), false);
  assert.equal(verifyWebhook(secret, body, null), false);
  const guard = new ReplayGuard();
  assert.equal(guard.firstTime('e1'), true);
  assert.equal(guard.firstTime('e1'), false);
});

test('a non-ecomsbd key is refused before any request', () => {
  assert.throws(() => new Client({ apiKey: 'sk_other', baseUrl: 'https://x.test' }));
});
