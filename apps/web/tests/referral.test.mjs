import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  cleanCode,
  creditKey,
  isCode,
  referralBn,
  referralEn,
  refusalKey,
  stateKey,
} from '../lib/referral.ts';

test('every referral string has Bangla, and the days placeholder stays', () => {
  assert.deepEqual(Object.keys(referralBn).sort(), Object.keys(referralEn).sort());
  for (const [key, value] of Object.entries(referralBn)) {
    assert.ok(value.trim().length > 0, key);
    for (const name of ['{days}', '{code}', '{date}']) {
      assert.equal(value.includes(name), referralEn[key].includes(name), `${key} ${name}`);
    }
  }
  for (const state of ['PENDING', 'REWARDED', 'VOID']) {
    assert.ok(stateKey(state) in referralEn, state);
  }
  for (const state of ['PENDING', 'GRANTED', 'HELD']) {
    assert.ok(creditKey(state) in referralEn, state);
  }
});

test('a code is checked the way the server checks it', () => {
  assert.equal(cleanCode(' abcd-2345 '), 'ABCD2345');
  assert.equal(cleanCode('abcd২৩৪৫'), 'ABCD2345');
  assert.ok(isCode('abcd2345') && isCode('ABCD-2345'));
  for (const bad of ['', 'ABC', 'ABCD23456', 'ABCD0123', 'ABCDIL23', 'ABCD$345']) {
    assert.ok(!isCode(bad), bad);
  }
});

test('every refusal the API names reads as plain copy', () => {
  for (const code of [
    'REFERRAL_CODE_INVALID',
    'REFERRAL_CODE_UNKNOWN',
    'REFERRAL_OWN_SHOP',
    'REFERRAL_SAME_OWNER',
    'REFERRAL_CYCLE',
    'REFERRAL_ALREADY_APPLIED',
    'REFERRAL_AFTER_FIRST_PAYMENT',
    'REFERRALS_UNAVAILABLE',
  ]) {
    const key = refusalKey({ code });
    assert.ok(key && key in referralEn, code);
  }
  assert.equal(refusalKey({ code: 'SOMETHING_ELSE' }), null);
  assert.equal(refusalKey(null), null);
});

test('the card asks the server first, only the owner asks, and it sits on Plans', async () => {
  const card = await readFile(new URL('../components/Referral.tsx', import.meta.url), 'utf8');
  assert.match(card, /canUse \? '\/referrals' : null/);
  assert.match(card, /shop\.can\('billing\.manage'\)/);
  assert.match(card, /!data\.available/);
  assert.equal((card.match(/api\.post/g) ?? []).length, 2);
  const page = await readFile(new URL('../app/(app)/plans/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /<Referral \/>/);
  const i18n = await readFile(new URL('../lib/i18n.ts', import.meta.url), 'utf8');
  assert.match(i18n, /\.\.\.referralEn,/);
  assert.match(i18n, /\.\.\.referralBn,/);
});
