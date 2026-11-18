import assert from 'node:assert/strict';
import test from 'node:test';

import { deliveryBn, deliveryEn } from '../lib/delivery-strings.ts';
import { paisaToTaka, setCallSwitch, takaToPaisa, toForm, toPatch } from '../lib/delivery-rules.ts';
import * as orders from '../lib/orders.ts';

const RULES = {
  configured: true,
  inside_dhaka_charge_paisa: 6000,
  dhaka_suburb_charge_paisa: 9000,
  outside_dhaka_charge_paisa: 12050,
  default_courier: 'steadfast',
  max_auto_cod_paisa: 500000,
  risk_low_bps: 5000,
  trusted_network_bps: 8500,
  trusted_repeat_min: 2,
  quiet_start: '21:00',
  quiet_end: '09:00',
  autopilot_daily_cap: 30,
  call_everyone: false,
  call_nobody: false,
};

test('every delivery-rules string has Bangla', () => {
  assert.deepEqual(Object.keys(deliveryBn).sort(), Object.keys(deliveryEn).sort());
  for (const [key, value] of Object.entries(deliveryBn)) {
    assert.ok(value.trim().length > 0, key);
  }
});

test('money conversion matches the order form exactly', () => {
  for (const text of ['60', '120.5', '1,250.05', '0', '', '-5', '1.234', 'abc']) {
    assert.equal(takaToPaisa(text), orders.takaToPaisa(text), text);
  }
  for (const paisa of [0, 5, 6000, 12050, 500000]) {
    assert.equal(paisaToTaka(paisa), orders.paisaToTaka(paisa), String(paisa));
  }
});

test('the form round-trips to the same PATCH body', () => {
  const result = toPatch(toForm(RULES));
  assert.ok('body' in result);
  const { configured, ...expected } = RULES;
  assert.equal(configured, true);
  assert.deepEqual(result.body, expected);
});

test('invalid values are named and nothing is sent', () => {
  const form = toForm(RULES);
  form.text.quiet_start = '25:00';
  form.text.risk_low_bps = '101';
  form.text.inside_dhaka_charge_paisa = '60.555';
  const result = toPatch(form);
  assert.ok('invalid' in result);
  assert.deepEqual(result.invalid.sort(), ['inside_dhaka_charge_paisa', 'quiet_start', 'risk_low_bps']);
});

test('the two call switches are never both on', () => {
  let form = setCallSwitch(toForm(RULES), 'call_everyone', true);
  form = setCallSwitch(form, 'call_nobody', true);
  assert.equal(form.call_everyone, false);
  assert.equal(form.call_nobody, true);
  const result = toPatch({ ...form, call_everyone: true });
  assert.ok('body' in result);
  assert.equal(result.body.call_nobody, false);
});
