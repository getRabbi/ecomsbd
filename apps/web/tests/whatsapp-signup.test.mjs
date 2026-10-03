import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { appLink, completionBody, loginOptions, metaMessage, signupRequest, signupState } from '../lib/whatsapp-signup.ts';

const id = '12345678-1234-1234-1234-123456789012';
const session = { connection_id: id, browser_session: 'short-lived-browser-capability', config_id: '123' };

test('fragment bootstrap accepts only a bounded opaque ticket', () => {
  assert.equal(signupState(`#state=${'a'.repeat(43)}`), 'a'.repeat(43));
  assert.equal(signupState('#state=bad<script>'), null);
  assert.equal(signupState(''), null);
});

test('v4 launch requests a code with the backend configuration', () => {
  assert.deepEqual(loginOptions('123'), { config_id: '123', response_type: 'code', override_default_response_type: true, extras: { setup: {} } });
});

test('only exact Meta origins and supported events are accepted', () => {
  const message = JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH', data: { waba_id: '123', phone_number_id: '456', access_token: 'never-copy-this' } });
  assert.deepEqual(metaMessage('https://www.facebook.com', message), { event: 'FINISH', assets: { waba_id: '123', phone_number_id: '456' } });
  for (const origin of ['https://evilfacebook.com', 'https://www.facebook.com.evil.test', 'http://www.facebook.com', 'null']) assert.equal(metaMessage(origin, message), null);
  assert.equal(metaMessage('https://www.facebook.com', 'invalid'), null);
  assert.deepEqual(metaMessage('https://www.facebook.com', { type: 'WA_EMBEDDED_SIGNUP', event: 'CANCEL' }), { event: 'CANCEL', assets: {} });
  assert.deepEqual(metaMessage('https://www.facebook.com', { type: 'WA_EMBEDDED_SIGNUP', event: 'ERROR', data: { error_message: 'secret' } }), { event: 'ERROR', assets: {} });
});

test('success and cancellation carry only the scoped capability and selected assets', () => {
  assert.deepEqual(completionBody(session, 'one-time-code', { waba_id: '123', phone_number_id: '456' }), { browser_session: session.browser_session, connection_id: id, authorization_code: 'one-time-code', waba_id: '123', phone_number_id: '456' });
  assert.deepEqual(completionBody(session, null, {}), { browser_session: session.browser_session, connection_id: id, cancelled: true });
});

test('deep links cannot carry an arbitrary scheme, redirect or credential', () => {
  assert.equal(appLink(id, 'CONNECTED'), `com.ecomsbd.app://integrations/return?connection=${id}&result=CONNECTED`);
  assert.equal(appLink('javascript:alert(1)', 'CONNECTED'), null);
  assert.equal(appLink(id, 'CONNECTED&token=secret'), null);
});

test('bootstrap and completion POST bodies never become query strings or stored credentials', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify(session)); };
  try {
    await signupRequest('https://api.example/v1', 'bootstrap', { signup_state: 'launch-ticket' });
    await signupRequest('https://api.example/v1', 'complete', completionBody(session, 'code', {}));
    for (const { url, options } of calls) {
      assert.equal(new URL(url).search, '');
      assert.equal(options.method, 'POST');
      assert.equal(options.credentials, 'omit');
      assert.equal(options.cache, 'no-store');
      assert.equal(options.referrerPolicy, 'no-referrer');
    }
  } finally { globalThis.fetch = original; }
});

test('page has public signup and app fallback, with no manual credentials or browser persistence', async () => {
  const page = await readFile(new URL('../app/ecomsbd/whatsapp-connect/page.tsx', import.meta.url), 'utf8');
  const detail = await readFile(new URL('../app/(app)/integrations/[id]/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /WhatsApp দিয়ে যুক্ত করুন/);
  assert.match(page, /ecomsbd খুলুন/);
  assert.match(page, /history.replaceState/);
  assert.doesNotMatch(page, /localStorage|sessionStorage|console\.|app_secret|access_token/);
  assert.doesNotMatch(detail, /phone_number_id|waba_id|access_token/);
});
