import assert from 'node:assert/strict';
import test from 'node:test';

import { LIVE_COURIERS, courierName, verifiedCapabilities } from '../lib/couriers.ts';
import { activeHref, NAV, QUICK_ACTIONS, allowed } from '../lib/nav.ts';
import { STATUS_MOVES, isEditable, newClientId, paisaToTaka, takaToPaisa } from '../lib/orders.ts';
import { opsBn, opsEn } from '../lib/ops-strings.ts';

test('taka typed by a seller becomes integer paisa with no float in between', () => {
  assert.equal(takaToPaisa('1250'), 125000);
  assert.equal(takaToPaisa('1,250.5'), 125050);
  assert.equal(takaToPaisa('0.05'), 5);
  assert.equal(takaToPaisa(' 99.99 '), 9999);
  for (const bad of ['', 'abc', '1.234', '-5', '1e3', '12.']) {
    assert.equal(takaToPaisa(bad), null, bad);
  }
  assert.equal(paisaToTaka(125050), '1250.50');
  assert.equal(paisaToTaka(125000), '1250');
  assert.equal(paisaToTaka(null), '');
});

test('status buttons mirror the backend transitions and never offer fulfilment by hand', () => {
  assert.deepEqual(STATUS_MOVES.DRAFT, ['CONFIRMED', 'CANCELLED']);
  assert.deepEqual(STATUS_MOVES.CONFIRMED, ['PACKED', 'CANCELLED']);
  assert.ok(!Object.values(STATUS_MOVES).flat().includes('FULFILLMENT_STARTED'));
  assert.ok(!Object.values(STATUS_MOVES).flat().includes('COMPLETED'));
  assert.equal(STATUS_MOVES.COMPLETED, undefined);
  assert.ok(isEditable('PACKED'));
  assert.ok(!isEditable('FULFILLMENT_STARTED'));
});

test('client ids are UUIDs the API accepts', () => {
  assert.match(newClientId(), /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
});

test('the sidebar lights only the most specific destination', () => {
  const hrefs = NAV.flatMap((group) => group.items.map((item) => item.href));
  assert.equal(activeHref('/money/payouts', hrefs), '/money/payouts');
  assert.equal(activeHref('/money', hrefs), '/money');
  assert.equal(activeHref('/procurement/suppliers', hrefs), '/procurement');
  assert.ok(hrefs.includes('/inbox') && hrefs.includes('/expenses'));
});

test('quick actions for daily work follow permissions', () => {
  const operator = new Set(['order.view', 'order.write', 'order.book', 'product.view', 'inventory.adjust', 'customer.view']);
  const ids = allowed(QUICK_ACTIONS, operator).map((action) => action.id);
  assert.ok(ids.includes('new-order') && ids.includes('paste-order'));
  assert.ok(!ids.includes('expense'), 'no money permission, no expense shortcut');
  assert.ok(!ids.includes('add-product'), 'no product.write, no add-product shortcut');
});

test('couriers stay live and only verified capabilities count', () => {
  assert.deepEqual([...LIVE_COURIERS], ['steadfast', 'pathao', 'redx']);
  assert.equal(courierName('redx'), 'RedX');
  assert.equal(courierName('manual', 'Own delivery'), 'Own delivery');
  assert.deepEqual(verifiedCapabilities({ booking: 'true', quote: 'unknown', bulk: 'false' }), ['booking']);
});

test('every new English string has a Bangla translation', () => {
  assert.deepEqual(Object.keys(opsBn).sort(), Object.keys(opsEn).sort());
  for (const [key, value] of Object.entries(opsBn)) {
    assert.ok(value.trim().length > 0, key);
  }
});
