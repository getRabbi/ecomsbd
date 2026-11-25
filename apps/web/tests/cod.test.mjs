import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { codDiffers, codSetByHand, effectiveCod, orderTotalPaisa } from '../lib/cod.ts';

// Launch run 13 D1: a pasted order "1ta Panjabi, 1200 taka" with a ৳60 delivery
// charge showed invoice total ৳1,260 next to COD ৳1,200.
const PANJABI = [{ unitPricePaisa: 120_000, quantity: 1 }];

test('the total is items plus delivery minus discount', () => {
  assert.equal(orderTotalPaisa(PANJABI, 6_000, 0), 126_000);
  assert.equal(orderTotalPaisa([{ unitPricePaisa: 50_000, quantity: 2 }, ...PANJABI], 6_000, 10_000), 216_000);
  assert.equal(orderTotalPaisa(PANJABI, 0, 500_000), 0, 'never below zero');
});

test('an unknown price, quantity or amount leaves the total unknown', () => {
  assert.equal(orderTotalPaisa([{ unitPricePaisa: null, quantity: 1 }], 6_000, 0), null);
  assert.equal(orderTotalPaisa([{ unitPricePaisa: 120_000, quantity: 0 }], 6_000, 0), null);
  assert.equal(orderTotalPaisa(PANJABI, null, 0), null);
  assert.equal(orderTotalPaisa([], 0, 0), null);
});

test('a chat amount equal to the items follows the total when the delivery charge changes', () => {
  const atPaste = orderTotalPaisa(PANJABI, 0, 0);
  const byHand = codSetByHand(120_000, atPaste);
  assert.equal(byHand, false);
  const afterCharge = orderTotalPaisa(PANJABI, 6_000, 0);
  assert.equal(effectiveCod(byHand, null, afterCharge), 126_000);
  assert.equal(codDiffers(byHand, null, afterCharge), false);
});

test('a COD set by hand is kept and flagged while it differs from the total', () => {
  const total = orderTotalPaisa(PANJABI, 6_000, 0);
  assert.equal(codSetByHand(150_000, orderTotalPaisa(PANJABI, 0, 0)), true, 'a stated total is not the items');
  assert.equal(effectiveCod(true, 150_000, total), 150_000);
  assert.equal(codDiffers(true, 150_000, total), true);
  assert.equal(codDiffers(true, 126_000, total), false);
});

test('with no total to follow, a stated COD is kept', () => {
  assert.equal(codSetByHand(65_000, null), true);
  assert.equal(codSetByHand(null, null), false);
  assert.equal(codDiffers(true, 65_000, null), false, 'nothing to compare with');
});

test('the composer, the order editor and the chat review use the rule', async () => {
  const composer = await readFile(new URL('../components/OrderComposer.tsx', import.meta.url), 'utf8');
  const panel = await readFile(new URL('../components/OrderPanel.tsx', import.meta.url), 'utf8');
  const inbox = await readFile(new URL('../app/(app)/inbox/page.tsx', import.meta.url), 'utf8');
  for (const source of [composer, panel]) {
    assert.match(source, /orderTotalPaisa\(/);
    assert.match(source, /codSetByHand\(/);
    assert.match(source, /oc\.codDiffers/);
  }
  // The composer sends COD only when it was set by hand; otherwise the API's total stands.
  assert.match(composer, /cod_amount_paisa: codByHand/);
  // A chat draft's zone charge arrives as the delivery charge, not inside COD alone.
  assert.match(inbox, /delivery_fee_paisa: draft\.delivery_fee_paisa/);
});
