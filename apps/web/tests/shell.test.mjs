import assert from 'node:assert/strict';
import { test } from 'node:test';

import { COMING_SOON_PROVIDERS, isComingSoon, PROVIDERS } from '../lib/integrations.ts';
import { workspaceReady } from '../lib/me.ts';
import { allowed, isActive, NAV, QUICK_ACTIONS, safeNextPath, sectionOf, visibleNav } from '../lib/nav.ts';
import {
  createPayload,
  initialShopValues,
  looksLikeBdMobile,
  updatePayload,
  validateShop,
} from '../lib/shop-form.ts';

const hrefs = (groups) => groups.flatMap((group) => group.items.map((item) => item.href));

// The backend matrix (tenants/roles.py) for the roles that see least.
const ORDER_OPERATOR = new Set(['order.view', 'order.write', 'order.book', 'customer.view', 'customer.risk_view', 'product.view']);
const FINANCE = new Set(['order.view', 'money.view', 'money.reconcile', 'product.view', 'data.export', 'payable.manage']);

test('navigation hides only what the API would refuse, and drops empty groups', () => {
  const operator = hrefs(visibleNav(NAV, ORDER_OPERATOR));
  assert.ok(operator.includes('/orders'));
  assert.ok(operator.includes('/couriers'), 'couriers are open to every member');
  assert.ok(!operator.includes('/money'));
  assert.ok(!operator.includes('/reconciliation'));
  assert.ok(!operator.includes('/developers'));
  assert.ok(!visibleNav(NAV, ORDER_OPERATOR).some((group) => group.labelKey === 'nav.moneyGroup'));

  const finance = hrefs(visibleNav(NAV, FINANCE));
  assert.ok(finance.includes('/money'));
  assert.ok(!finance.includes('/customers'), 'finance has no customer.view');

  // Unknown permissions (still loading) never empty the sidebar.
  assert.deepEqual(hrefs(visibleNav(NAV, null)), hrefs(NAV));
});

test('navigation keeps every existing route and no billing destination', () => {
  const all = hrefs(NAV);
  for (const route of ['/dashboard', '/orders', '/customers', '/products', '/couriers', '/money', '/reconciliation',
    '/insights', '/returns', '/forecasting', '/procurement', '/campaigns', '/messaging', '/automation', '/network',
    '/notifications', '/imports', '/integrations', '/order-sources', '/team', '/settings', '/developers']) {
    assert.ok(all.includes(route), route);
  }
  for (const paid of ['/plans', '/pricing', '/subscription', '/upgrade']) {
    assert.ok(!all.includes(paid), `${paid} must stay out of free-launch navigation`);
  }
  assert.equal(new Set(all).size, all.length);
});

test('quick actions follow the same permissions', () => {
  const ids = allowed(QUICK_ACTIONS, ORDER_OPERATOR).map((action) => action.id);
  assert.ok(ids.includes('book'));
  assert.ok(!ids.includes('reconcile'));
  assert.ok(!ids.includes('shop'));
  assert.ok(allowed(QUICK_ACTIONS, FINANCE).some((action) => action.id === 'reconcile'));
});

test('a post-sign-in destination must be a plain path on this site', () => {
  assert.equal(safeNextPath('/orders?order=abc'), '/orders?order=abc');
  assert.equal(safeNextPath('/customers/123'), '/customers/123');
  for (const hostile of [null, '', 'orders', '//evil.example', '/\\evil.example', 'https://evil.example',
    '/x?u=https://evil.example', '/sign-in', '/sign-in?next=/orders', '/a\u0000b']) {
    assert.equal(safeNextPath(hostile), null, String(hostile));
  }
});

test('active links and shop-switch landing', () => {
  assert.ok(isActive('/procurement/suppliers', '/procurement'));
  assert.ok(!isActive('/products', '/procurement'));
  assert.equal(sectionOf('/customers/8f2c'), '/customers');
  assert.equal(sectionOf('/'), '/dashboard');
});

test('exactly the five launch-gated integrations are Coming Soon; couriers never are', () => {
  assert.deepEqual([...COMING_SOON_PROVIDERS].sort(), ['CUSTOM_WEBSITE', 'MESSENGER', 'SHOPIFY', 'WHATSAPP', 'WOOCOMMERCE']);
  assert.ok(PROVIDERS.every(isComingSoon));
  for (const courier of ['STEADFAST', 'PATHAO', 'REDX', 'MANUAL']) {
    assert.equal(isComingSoon(courier), false, courier);
  }
});

test('shop form: validation mirrors what the API would silently mishandle', () => {
  const blank = initialShopValues(null);
  assert.deepEqual(validateShop(blank), ['name']);
  assert.deepEqual(validateShop({ ...blank, name: 'Rafi Fashion' }), []);
  assert.deepEqual(validateShop({ ...blank, name: 'Rafi Fashion', pickup_phone: '12345' }), ['phone']);
  for (const phone of ['01712345678', '+8801712345678', '8801712345678', '017-1234 5678']) {
    assert.ok(looksLikeBdMobile(phone), phone);
  }
  for (const phone of ['0171234567', '01212345678', '+14155550100']) {
    assert.ok(!looksLikeBdMobile(phone), phone);
  }
});

test('shop form: create sends filled fields only; update sends only changes', () => {
  const values = { ...initialShopValues(null), name: '  Rafi Fashion ', pickup_district: 'Dhaka' };
  assert.deepEqual(createPayload(values), { name: 'Rafi Fashion', business_category: 'OTHER', pickup_district: 'Dhaka' });

  const shop = {
    id: 't1', name: 'Rafi Fashion', business_category: 'CLOTHING', status: 'ACTIVE', timezone: 'Asia/Dhaka',
    currency: 'BDT', order_number_prefix: 'RF', pickup_contact_name: 'Rafi', pickup_address_raw: 'House 1',
    pickup_district: 'Dhaka', pickup_area: 'Mirpur', onboarding_step: 'COMPLETE', onboarding_completed_at: null,
  };
  const initial = initialShopValues(shop);
  assert.equal(initial.pickup_phone, '', 'the saved number is never prefilled');
  assert.deepEqual(updatePayload(initial, initial), {});
  assert.deepEqual(updatePayload(initial, { ...initial, pickup_area: '', name: 'Rafi Fashion House' }), {
    name: 'Rafi Fashion House',
    pickup_area: '',
  });
  assert.deepEqual(updatePayload(initial, { ...initial, pickup_phone: '01712345678' }), { pickup_phone: '01712345678' });
});

test('workspace opens only for a selected, set-up shop', () => {
  const shop = { id: 't1', name: 'A', role: 'OWNER', onboarding_complete: true };
  const base = { user_id: 'u', display_name: null, masked_phone: null, locale: 'bn', role: 'OWNER',
    permissions: ['settings.manage'], needs_onboarding: false };
  assert.equal(workspaceReady({ ...base, tenant_id: 't1', tenants: [shop] }), true);
  assert.equal(workspaceReady({ ...base, tenant_id: null, tenants: [] }), false, 'no shop: create one');
  assert.equal(workspaceReady({ ...base, tenant_id: null, tenants: [shop, { ...shop, id: 't2' }] }), false, 'pick a shop');
  const unfinished = { ...shop, onboarding_complete: false };
  assert.equal(workspaceReady({ ...base, tenant_id: 't1', tenants: [unfinished] }), false, 'owner finishes setup');
  assert.equal(
    workspaceReady({ ...base, role: 'VIEWER', permissions: ['order.view'], tenant_id: 't1', tenants: [unfinished] }),
    true,
    'a member who cannot edit is not blocked',
  );
});
