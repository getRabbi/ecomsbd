import assert from 'node:assert/strict';
import test from 'node:test';

import { courierHistoryChip, courierLabel, latestCourierHistory } from '../lib/courier-history.ts';
import { riskBn, riskEn } from '../lib/risk-strings.ts';

const HISTORY = {
  provider_counts: [
    { courier: 'steadfast', delivered: 15, cancelled: 2, total: 17 },
    { courier: 'pathao', delivered: 8, cancelled: 1, total: 9 },
  ],
  aggregate: { delivered: 23, cancelled: 3, total: 26 },
  as_of: null,
  state: 'OK',
  success_bps: 8846,
};

test('the chip reads courier by courier', () => {
  assert.equal(courierHistoryChip(HISTORY, 'kom data', 'All'), 'Steadfast 15/17 · Pathao 8/9');
});

test('under three parcels the chip says too little data, never a ratio', () => {
  const thin = { ...HISTORY, provider_counts: [{ courier: 'redx', delivered: 1, cancelled: 1, total: 2 }], aggregate: { delivered: 1, cancelled: 1, total: 2 }, state: 'INSUFFICIENT_DATA', success_bps: null };
  assert.equal(courierHistoryChip(thin, 'kom data', 'All'), 'kom data');
  assert.equal(courierHistoryChip(null, 'kom data', 'All'), null);
});

test('only an aggregate falls back to one total', () => {
  assert.equal(courierHistoryChip({ ...HISTORY, provider_counts: [] }, 'kom data', 'All couriers'), 'All couriers 23/26');
  assert.equal(courierLabel('redx'), 'RedX');
  assert.equal(courierLabel('new_courier'), 'New courier');
});

test('the newest stored history wins', () => {
  const older = { result: { courier_history: { ...HISTORY, aggregate: { delivered: 1, cancelled: 0, total: 3 } }, checked_at: '2026-11-01T00:00:00Z' } };
  const newer = { result: { courier_history: HISTORY, checked_at: '2026-11-10T00:00:00Z' } };
  assert.equal(latestCourierHistory([older, newer, { result: null }]), HISTORY);
});

test('the new risk strings have Bangla', () => {
  for (const key of ['risk.ext.history', 'risk.ext.komData', 'risk.ext.allCouriers']) {
    assert.ok(riskEn[key] && riskBn[key], key);
  }
});
