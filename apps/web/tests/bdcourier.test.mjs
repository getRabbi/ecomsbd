import assert from 'node:assert/strict';
import test from 'node:test';

import { courierLabel } from '../lib/courier-history.ts';
import { providerNotice } from '../lib/risk.ts';
import { riskBn, riskEn } from '../lib/risk-strings.ts';

// V4 WP0.6: BD Courier as the first built-in external risk provider.

test('the new-order refusal and the terms label have both languages', () => {
  for (const key of ['risk.err.NEW_ORDER_REQUIRED', 'rp.terms']) {
    assert.ok(riskEn[key] && riskBn[key], key);
    assert.notEqual(riskEn[key], riskBn[key], key);
  }
});

test('the provider terms show in the seller language, English when Bangla is missing', () => {
  const provider = { notice_en: 'Use your own key.', notice_bn: 'নিজের কী ব্যবহার করুন।' };
  assert.equal(providerNotice(provider, 'en'), 'Use your own key.');
  assert.equal(providerNotice(provider, 'bn'), 'নিজের কী ব্যবহার করুন।');
  assert.equal(providerNotice({ notice_en: 'Use your own key.', notice_bn: null }, 'bn'), 'Use your own key.');
  assert.equal(providerNotice({}, 'en'), null);
  assert.equal(providerNotice({ notice_en: null, notice_bn: null }, 'bn'), null);
});

test('BD Courier courier keys read as names', () => {
  assert.equal(courierLabel('parceldex'), 'ParcelDex');
  assert.equal(courierLabel('courrierfast'), 'CourrierFast');
  assert.equal(courierLabel('carrybee'), 'CarryBee');
});
