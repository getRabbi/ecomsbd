import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

import { COURIERS, copy, links, sectionIds } from '../app/ecomsbd/content.ts';
import { courierName, LIVE_COURIERS } from '../lib/couriers.ts';

function shape(value) {
  if (Array.isArray(value)) return value.map(shape);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, shape(value[key])]));
  }
  return typeof value;
}

function strings(value) {
  if (typeof value === 'string') return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (value && typeof value === 'object') return Object.values(value).flatMap(strings);
  return [];
}

test('English and Bangla say the same things', () => {
  assert.deepEqual(shape(copy.bn), shape(copy.en));
});

test('every product CTA goes to the workspace origin, through its one auth page', () => {
  for (const key of ['start', 'signIn', 'app', 'dashboard']) {
    assert.match(links[key], /^https:\/\/app\.ecomsbd\.com(\/|$)/, key);
  }
  // Sign-in and create-account are two tabs of the same page; there is no
  // separate sign-up route to link to.
  assert.equal(links.start, 'https://app.ecomsbd.com/sign-in');
  assert.equal(links.signIn, 'https://app.ecomsbd.com/sign-in');
  assert.equal(links.play, 'https://play.google.com/store/apps/details?id=com.ecomsbd.app');
});

test('the couriers on the site are the couriers the workspace can book', () => {
  assert.deepEqual([...COURIERS], LIVE_COURIERS.map(courierName));
});

test('gated sales channels are only ever shown as coming soon', () => {
  assert.deepEqual(
    copy.en.soon.channels.map((channel) => channel.name),
    ['Shopify', 'WooCommerce', 'Custom website', 'Messenger', 'WhatsApp'],
  );
  assert.equal(copy.en.soon.chip, 'Coming soon');
  assert.equal(copy.bn.soon.chip, 'শীঘ্রই আসছে');
  for (const locale of ['en', 'bn']) {
    assert.match(copy[locale].soon.badge, /Coming soon · শীঘ্রই আসছে/);
  }
});

test('no claims the business cannot back', () => {
  const text = strings(copy).join('\n');
  assert.doesNotMatch(text, /official partner|partnered with|ISO ?27001|SOC ?2|PCI|rating|testimonial|\/month|per month/i);
});

test('every header anchor has a section to land on', async () => {
  const page = await readFile(new URL('../app/ecomsbd/home.tsx', import.meta.url), 'utf8');
  for (const key of Object.keys(sectionIds)) {
    assert.ok(page.includes(`id={sectionIds.${key}}`), `section ${key} is rendered`);
  }
});

test('the App Store is shown as coming soon and never linked', async () => {
  for (const locale of ['en', 'bn']) {
    assert.match(copy[locale].stores.appStoreTop, /Coming soon|শীঘ্রই আসছে/);
  }
  const component = await readFile(new URL('../app/ecomsbd/store-links.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(component, /apps\.apple\.com|itunes\.apple\.com/);
  assert.match(component, /href=\{links\.play\}/);
});

test('support and legal pages name ecomsbd.com as canonical', async () => {
  const pages = {
    'ecomsbd-support/page.tsx': ['https://ecomsbd.com/ecomsbd-support'],
    'privacy-policy/page.tsx': ['https://ecomsbd.com/privacy-policy', 'https://ecomsbd.com/privacy-policy/bn'],
    'privacy-policy/bn/page.tsx': ['https://ecomsbd.com/privacy-policy/bn', 'https://ecomsbd.com/privacy-policy'],
    'account-deletion/page.tsx': ['https://ecomsbd.com/account-deletion', 'https://ecomsbd.com/account-deletion/bn'],
    'account-deletion/bn/page.tsx': ['https://ecomsbd.com/account-deletion/bn', 'https://ecomsbd.com/account-deletion'],
  };
  for (const [file, urls] of Object.entries(pages)) {
    const source = await readFile(new URL(`../app/${file}`, import.meta.url), 'utf8');
    assert.match(source, new RegExp(`canonical: '${urls[0]}'`), file);
    for (const url of urls) assert.ok(source.includes(`'${url}'`), `${file} links ${url}`);
    assert.doesNotMatch(source, /scalemyprints\.com/, file);
  }
});
