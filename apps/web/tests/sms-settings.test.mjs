import assert from 'node:assert/strict';
import test from 'node:test';

import { canToggle, orderUpdateSwitch, smsSettingsBn, smsSettingsEn } from '../lib/sms-settings.ts';

test('every SMS settings string has Bangla, and both say what it costs', () => {
  assert.deepEqual(Object.keys(smsSettingsBn).sort(), Object.keys(smsSettingsEn).sort());
  for (const [key, value] of Object.entries(smsSettingsBn)) {
    assert.ok(value.trim().length > 0, key);
  }
  assert.equal(smsSettingsEn['ss.cost'], 'Costs from your SMS balance.');
  assert.match(smsSettingsBn['ss.cost'], /SMS ব্যালেন্স/);
});

test('the order update switch follows the SMS channel row', () => {
  assert.deepEqual(orderUpdateSwitch(undefined), { available: false, enabled: false });
  assert.deepEqual(orderUpdateSwitch([{ kind: 'EMAIL', available: true, enabled: true }]), {
    available: false,
    enabled: false,
  });
  assert.deepEqual(orderUpdateSwitch([{ kind: 'SMS', available: true, enabled: false }]), {
    available: true,
    enabled: false,
  });
  assert.deepEqual(
    orderUpdateSwitch([{ kind: 'SMS', available: true, enabled: false, shop_blocker: 'X' }]),
    { available: false, enabled: false },
  );
});

test('a switch can always be turned off, and on only when available', () => {
  assert.equal(canToggle({ available: false, enabled: false }), false);
  assert.equal(canToggle({ available: false, enabled: true }), true);
  assert.equal(canToggle({ available: true, enabled: false }), true);
});
