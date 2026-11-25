import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { opsBn, opsEn } from '../lib/ops-strings.ts';

// Launch run 13 D2: a courier account whose saved key is rejected
// (NEEDS_RECONNECT) is announced at the top of the Couriers page, in words.
const KEYS = ['cour.reconnectBannerTitle', 'cour.reconnectBannerBody', 'cour.reconnectNamed', 'cour.reconnectAskOwner'];

test('the reconnect banner has words in English and Bangla', () => {
  for (const key of KEYS) {
    for (const table of [opsEn, opsBn]) {
      assert.ok(table[key] && table[key].trim().length > 0, `${key} has text`);
      assert.ok(!/NEEDS_RECONNECT/.test(table[key]), `${key} shows no raw code`);
    }
  }
  assert.match(opsEn['cour.reconnectBannerTitle'], /\{couriers\}/);
  assert.match(opsBn['cour.reconnectBannerTitle'], /\{couriers\}/);
  assert.match(opsEn['cour.reconnectNamed'], /\{courier\}/);
  assert.match(opsBn['cour.reconnectNamed'], /\{courier\}/);
});

test('the Couriers page shows the banner for any account that needs reconnecting', () => {
  const page = readFileSync(new URL('../app/(app)/couriers/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /needs_reconnect\)/);
  assert.match(page, /data-testid="courier-reconnect-banner"/);
  assert.match(page, /role="alert"/);
  // A member who cannot enter keys is told who can, not shown a dead button.
  assert.match(page, /cour\.reconnectAskOwner/);
});
