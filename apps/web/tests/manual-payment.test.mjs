import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  amountPaisa,
  cleanTrx,
  monthChoices,
  formatTaka,
  invalidFields,
  isBdMobile,
  isTrx,
  manualPaymentBn,
  manualPaymentEn,
  refusalKey,
  stateKey,
  submitBody,
} from '../lib/manual-payment.ts';

const OPTIONS = {
  available: true,
  unavailable_reason: null,
  accounts: [{ method: 'BKASH', number: '01700000001', label: 'Personal' }],
  plans: [
    {
      code: 'starter',
      name: 'Starter',
      monthly_price: { amount_paisa: 19900, currency: 'BDT' },
      amounts: [
        { months: 1, amount: { amount_paisa: 19900, currency: 'BDT' } },
        { months: 3, amount: { amount_paisa: 59700, currency: 'BDT' } },
      ],
    },
    { code: 'pro', name: 'Pro', monthly_price: { amount_paisa: 39900, currency: 'BDT' }, amounts: [] },
  ],
  min_months: 1,
  max_months: 12,
};

const FORM = { plan: 'starter', months: 3, method: 'BKASH', payer: '01700000077', trx: '8n7a 6d5c-4b' };

test('every bKash / Nagad payment string has Bangla', () => {
  assert.deepEqual(Object.keys(manualPaymentBn).sort(), Object.keys(manualPaymentEn).sort());
  for (const [key, value] of Object.entries(manualPaymentBn)) {
    assert.ok(value.trim().length > 0, key);
  }
  for (const state of ['SUBMITTED', 'APPROVED', 'REJECTED']) {
    assert.ok(stateKey(state) in manualPaymentEn, state);
  }
});

test('the amount is the server quote for the months, shown from integer paisa', () => {
  assert.equal(amountPaisa(OPTIONS.plans[0], 3), 59700);
  assert.equal(amountPaisa(OPTIONS.plans[0], 6), null);
  assert.equal(amountPaisa(undefined, 3), null);
  assert.deepEqual(monthChoices(OPTIONS.plans[0]), [1, 3]);
  assert.equal(formatTaka(59700), '৳597');
  assert.equal(formatTaka(478800), '৳4,788');
  assert.equal(formatTaka(125050), '৳1,250.50');
});

test('the TrxID and payer number are checked the way the server checks them', () => {
  assert.equal(cleanTrx(' 8n7a 6d5c-4b '), '8N7A6D5C4B');
  assert.equal(cleanTrx('৮N৭A৬D৫C৪B'), '8N7A6D5C4B');
  assert.ok(isTrx('8N7A6D5C4B') && !isTrx('12345') && !isTrx('AB$#12') && !isTrx('A'.repeat(21)));
  assert.ok(isBdMobile('01700000077') && isBdMobile('+8801700000077') && !isBdMobile('0123456789'));
  assert.deepEqual(invalidFields(FORM, OPTIONS), []);
  assert.deepEqual(
    invalidFields({ plan: 'free', months: 13, method: 'NAGAD', payer: '123', trx: 'x' }, OPTIONS),
    ['plan', 'months', 'method', 'payer', 'trx'],
  );
  assert.deepEqual(submitBody(FORM), {
    plan_code: 'starter',
    months: 3,
    method: 'BKASH',
    payer_number: '01700000077',
    trx_id: '8N7A6D5C4B',
  });
});

test('refusals read as plain copy', () => {
  assert.equal(refusalKey({ code: 'TRX_ID_ALREADY_USED' }), 'mp.errUsed');
  assert.equal(refusalKey({ code: 'TRX_ID_INVALID' }), 'mp.errTrx');
  assert.equal(refusalKey({ code: 'MANUAL_PAYMENT_UNAVAILABLE', reason: 'CHANNEL' }), 'mp.errUnavailable');
  assert.equal(refusalKey(null), null);
});

test('the card asks the server first and only the owner asks', async () => {
  const card = await readFile(new URL('../components/ManualPayment.tsx', import.meta.url), 'utf8');
  assert.match(card, /canPay \? '\/billing\/manual-payments\/options' : null/);
  assert.match(card, /shop\.can\('billing\.manage'\)/);
  assert.equal((card.match(/api\.post/g) ?? []).length, 1);
  const page = await readFile(new URL('../app/(app)/plans/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /<ManualPayment /);
});
