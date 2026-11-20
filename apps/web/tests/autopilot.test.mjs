import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTOPILOT_COURIER_PROBLEMS,
  AUTOPILOT_REASONS,
  autopilotBn,
  autopilotEn,
  canTurnOn,
  courierName,
  courierProblemKey,
  deliveredPercent,
  homeCard,
  reasonKey,
} from '../lib/autopilot.ts';

const LINES = ['PLAN', 'CONFIRMATIONS', 'COURIER', 'DELIVERY_RULES', 'VOICE', 'HISTORY', 'CONSENT'];

function status(overrides = {}) {
  return {
    on: false,
    consent_at: null,
    paused_until: null,
    manual_queue: false,
    daily_cap: 30,
    ready: false,
    checklist: LINES.map((key) => ({ key, ok: true, reason: `${key}_X`, detail: {} })),
    reason: 'AUTOPILOT_OFF',
    workflow: { installed: false, enabled: false },
    courier_comparison: { default: 'steadfast', days: 180, options: [] },
    ...overrides,
  };
}

const today = (recent = []) => ({
  date: '2026-11-20',
  converted: 0,
  confirmed: 0,
  cancelled: 0,
  waiting: 0,
  held: 0,
  recent,
});

test('every Autopilot string has Bangla', () => {
  assert.deepEqual(Object.keys(autopilotBn).sort(), Object.keys(autopilotEn).sort());
  for (const [key, value] of Object.entries(autopilotBn)) {
    assert.ok(value.trim().length > 0, key);
  }
  for (const key of ['ap.l.HISTORY', 'ap.pausedUntil', 'ap.dailyCap', 'ap.madeOrder', 'ap.heldFor']) {
    const vars = autopilotEn[key].match(/\{\w+\}/g);
    assert.deepEqual(autopilotBn[key].match(/\{\w+\}/g)?.sort(), vars.sort(), key);
  }
});

test('every reason code, checklist line and courier problem has a label', () => {
  for (const code of AUTOPILOT_REASONS) {
    assert.equal(reasonKey(code), `ap.r.${code}`);
    assert.ok(autopilotEn[`ap.r.${code}`], code);
  }
  assert.equal(reasonKey('SOMETHING_NEW'), 'ap.r.unknown');
  assert.equal(reasonKey(null), 'ap.r.unknown');
  for (const line of LINES) assert.ok(autopilotEn[`ap.l.${line}`], line);
  for (const code of AUTOPILOT_COURIER_PROBLEMS) {
    assert.equal(courierProblemKey(code), `ap.c.${code}`);
    assert.ok(autopilotEn[`ap.c.${code}`], code);
  }
  assert.equal(courierProblemKey(undefined), 'ap.r.COURIER_NOT_READY');
});

test('the courier comparison never rounds a rate up', () => {
  assert.equal(deliveredPercent({ delivered: 2, parcels: 3 }), 66);
  assert.equal(deliveredPercent({ delivered: 3, parcels: 3 }), 100);
  assert.equal(deliveredPercent({ delivered: 0, parcels: 0 }), 0);
  assert.equal(courierName('redx'), 'RedX');
  assert.equal(courierName('carrybee'), 'Carrybee');
  for (const key of ['ap.usesDefault', 'ap.compare', 'ap.compareRow']) {
    assert.deepEqual(autopilotBn[key].match(/\{\w+\}/g).sort(), autopilotEn[key].match(/\{\w+\}/g).sort(), key);
  }
});

test('turning on waits for every line; consent may be ticked on the page', () => {
  const noConsent = status({
    checklist: LINES.map((key) => ({ key, ok: key !== 'CONSENT', reason: 'X', detail: {} })),
  });
  assert.equal(canTurnOn(noConsent, false), false);
  assert.equal(canTurnOn(noConsent, true), true);
  const noCourier = status({
    checklist: LINES.map((key) => ({ key, ok: key !== 'COURIER', reason: 'X', detail: {} })),
  });
  assert.equal(canTurnOn(noCourier, true), false);
  assert.equal(canTurnOn(status({ on: true }), true), false);
});

test('the Home card shows while on or after a decision; pause and resume follow permissions', () => {
  const all = () => true;
  const operator = (permission) => permission === 'order.write';
  assert.equal(homeCard(status(), today(), all).show, false);
  const held = { draft_id: 'd', outcome: 'HELD', reason: 'OUT_OF_STOCK', order_id: null, order_number: null, at: 'x' };
  assert.equal(homeCard(status(), today([held]), all).show, true);
  const on = homeCard(status({ on: true }), today(), operator);
  assert.deepEqual(on, { show: true, paused: false, canPause: true, canResume: false });
  const paused = status({ on: true, paused_until: '2026-11-20T08:00:00Z' });
  assert.equal(homeCard(paused, today(), operator).canResume, false);
  assert.equal(homeCard(paused, today(), all).canResume, true);
  assert.equal(homeCard(paused, today(), all).canPause, false);
});
