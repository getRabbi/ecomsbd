import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  closedMessage,
  confirmBn,
  confirmEn,
  confirmText,
  formatCod,
  isToken,
  itemLine,
} from '../lib/confirm-link.ts';

const BLANK = {
  status: 'UNAVAILABLE',
  result: null,
  shop_name: null,
  items: [],
  item_count: 0,
  cod_amount_paisa: null,
  phone_masked: null,
  expires_at: null,
};

test('every confirm-page string has Bangla', () => {
  assert.deepEqual(Object.keys(confirmBn).sort(), Object.keys(confirmEn).sort());
  for (const [key, value] of Object.entries(confirmBn)) {
    assert.ok(value.trim().length > 0, key);
  }
});

test('placeholders survive in both languages', () => {
  assert.equal(confirmText('en', 'cl.from', { shop: 'Rupkotha' }), 'Order from Rupkotha');
  assert.equal(confirmText('bn', 'cl.from', { shop: 'Rupkotha' }), 'Rupkotha থেকে অর্ডার');
  assert.equal(confirmText('bn', 'cl.more', { count: 3 }), 'আরো 3টি');
});

test('COD is shown from integer paisa', () => {
  assert.equal(formatCod(125000), '৳1,250');
  assert.equal(formatCod(125050), '৳1,250.50');
  assert.equal(formatCod(99), '৳0.99');
  assert.equal(formatCod(123456789), '৳1,234,567.89');
});

test('items, closed states and tokens read plainly', () => {
  assert.equal(itemLine({ name: 'Abaya', variant: 'XL', quantity: 2 }), 'Abaya (XL) × 2');
  assert.equal(itemLine({ name: 'Abaya', variant: null, quantity: 1 }), 'Abaya × 1');
  assert.equal(closedMessage({ ...BLANK, status: 'ANSWERED', result: 'CONFIRM' }), 'cl.confirmed');
  assert.equal(closedMessage({ ...BLANK, status: 'ANSWERED', result: 'CANCEL' }), 'cl.cancelled');
  assert.equal(closedMessage(BLANK), 'cl.unavailable');
  assert.ok(isToken('AbCdEfGhIjKlMnOpQr_-12'));
  assert.ok(!isToken('short') && !isToken('A'.repeat(23)) && !isToken('AbCdEfGhIjKlMnOpQr/=12'));
});

test('the page only reads on open and answers through the public API', async () => {
  const page = await readFile(new URL('../app/c/[token]/page.tsx', import.meta.url), 'utf8');
  // Opening the link must change nothing: the only POST is the button's.
  assert.equal((page.match(/publicApi\.post/g) ?? []).length, 1);
  assert.match(page, /onClick=\{\(\) => void answer\('confirm'\)\}/);
  const effect = page.slice(page.indexOf('useEffect('), page.indexOf('}, [path, token]);'));
  assert.ok(/publicApi\s*\.get/.test(effect) && !effect.includes('publicApi.post'));
  const layout = await readFile(new URL('../app/c/[token]/layout.tsx', import.meta.url), 'utf8');
  assert.match(layout, /referrer: 'no-referrer'/);
  assert.match(layout, /index: false/);
  const api = await readFile(new URL('../lib/api.ts', import.meta.url), 'utf8');
  assert.match(api, /if \(response\.status === 401 && !options\.anonymous\)/);
});
