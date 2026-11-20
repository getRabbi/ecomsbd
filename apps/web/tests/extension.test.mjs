import assert from 'node:assert/strict';
import test from 'node:test';

import {
  draftIdFrom,
  draftPrefill,
  extensionBn,
  extensionEn,
  isOpenDraft,
  minutesLeft,
} from '../lib/extension.ts';

const ID = '01a1f9fa-d536-7f34-b7f3-af1bedc3c0d0';

function draft(overrides = {}) {
  return {
    id: ID,
    status: 'READY_FOR_REVIEW',
    capture_source: 'EXTENSION',
    customer_name: 'Rahim',
    phones: ['+8801700000021'],
    selected_phone: '+8801700000021',
    address: 'Mirpur 10, Dhaka',
    items: [{ name: 'Panjabi', quantity: 2, unit_price_paisa: 120000, size: 'XL', color: null }],
    cod_amount_paisa: 240000,
    notes: null,
    missing_fields: [],
    confirmed_order_id: null,
    ...overrides,
  };
}

test('every extension and quick-order string has Bangla with the same placeholders', () => {
  assert.deepEqual(Object.keys(extensionBn).sort(), Object.keys(extensionEn).sort());
  for (const [key, value] of Object.entries(extensionBn)) {
    assert.ok(value.trim().length > 0, key);
    const vars = extensionEn[key].match(/\{\w+\}/g) ?? [];
    assert.deepEqual((value.match(/\{\w+\}/g) ?? []).sort(), vars.sort(), key);
  }
});

test('only a draft id is read from the link', () => {
  assert.equal(draftIdFrom(ID), ID);
  assert.equal(draftIdFrom(` ${ID.toUpperCase()} `), ID);
  for (const bad of [null, undefined, '', '123', `${ID}/../orders`, 'javascript:alert(1)']) {
    assert.equal(draftIdFrom(bad), null, String(bad));
  }
});

test('a draft opens the composer with the Inbox mapping', () => {
  const prefill = draftPrefill(draft());
  assert.equal(prefill.chatDraftId, ID);
  assert.equal(prefill.phone, '+8801700000021');
  assert.equal(prefill.phones, undefined, 'one number needs no choice');
  assert.deepEqual(prefill.items, [{ name: 'Panjabi', quantity: 2, unit_price_paisa: 120000, variant_label: 'XL' }]);
  assert.equal(prefill.cod_amount_paisa, 240000);
  const two = draftPrefill(draft({ phones: ['+8801700000021', '+8801700000022'], items: [{}] }));
  assert.equal(two.phones.length, 2);
  assert.deepEqual(two.items, [{ name: '', quantity: 1, unit_price_paisa: null, variant_label: null }]);
});

test('closed or converted drafts are not offered again', () => {
  assert.equal(isOpenDraft(draft()), true);
  assert.equal(isOpenDraft(draft({ status: 'NEEDS_INFO' })), true);
  for (const status of ['CONFIRMED', 'IGNORED', 'EXPIRED']) {
    assert.equal(isOpenDraft(draft({ status })), false, status);
  }
  assert.equal(isOpenDraft(draft({ confirmed_order_id: 'x' })), false);
});

test('a pairing code counts down in whole minutes', () => {
  const now = Date.parse('2026-11-20T10:00:00Z');
  assert.equal(minutesLeft('2026-11-20T10:10:00Z', now), 10);
  assert.equal(minutesLeft('2026-11-20T10:00:30Z', now), 1);
  assert.equal(minutesLeft('2026-11-20T09:59:59Z', now), 0);
  assert.equal(minutesLeft('not a date', now), 0);
});
