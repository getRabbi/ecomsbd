import assert from 'node:assert/strict';
import test from 'node:test';

import { smsBn, smsEn, smsOptionLabel, smsState, smsText, usesPhone } from '../lib/messaging-sms.ts';

test('every tracking SMS string has Bangla', () => {
  assert.deepEqual(Object.keys(smsBn).sort(), Object.keys(smsEn).sort());
  for (const [key, value] of Object.entries(smsBn)) {
    assert.ok(value.trim().length > 0, key);
  }
  assert.equal(smsText('bn', 'sms.number'), 'মোবাইল নম্বর');
  assert.equal(smsText('en', 'sms.number'), 'Mobile number');
});

test('SMS is off until the server says available, ready only with the shop switch on', () => {
  assert.equal(smsState(undefined), 'off');
  assert.equal(smsState([{ kind: 'EMAIL', available: true, enabled: true }]), 'off');
  assert.equal(smsState([{ kind: 'SMS', available: false, enabled: true }]), 'off');
  assert.equal(smsState([{ kind: 'SMS', available: true, enabled: false }]), 'available');
  assert.equal(smsState([{ kind: 'SMS', available: true, enabled: true }]), 'ready');
});

test('the channel picker says why SMS cannot be chosen', () => {
  assert.equal(smsOptionLabel('en', 'off', false), smsEn['sms.off']);
  assert.equal(smsOptionLabel('bn', 'off', true), smsBn['sms.off']);
  assert.equal(smsOptionLabel('en', 'available', true), smsEn['sms.turnOn']);
  assert.equal(smsOptionLabel('en', 'available', false), 'SMS');
  assert.equal(smsOptionLabel('bn', 'ready', true), 'SMS');
  assert.ok(usesPhone('SMS') && usesPhone('WHATSAPP') && !usesPhone('EMAIL'));
});
