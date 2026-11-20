// What the shipped extension may touch, and that every panel string has Bangla.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { baseUrl, buildManifest, CHAT_HOSTS } from '../scripts/manifest.mjs';
import { HOSTS } from '../src/shared/rules.ts';
import { bn, en, translate } from '../src/shared/strings.ts';

const template = JSON.parse(readFileSync(new URL('../static/manifest.json', import.meta.url), 'utf8'));
const manifest = buildManifest(template, { version: '4.0.0', apiBaseUrl: 'https://api.example.test' });

test('content scripts run on the three chat hosts only', () => {
  assert.deepEqual([...CHAT_HOSTS].sort(), [...HOSTS].sort());
  const matches = manifest.content_scripts.flatMap((script) => script.matches);
  assert.deepEqual(matches.sort(), HOSTS.map((host) => `https://${host}/*`).sort());
  for (const entry of manifest.web_accessible_resources) {
    assert.deepEqual([...entry.matches].sort(), HOSTS.map((host) => `https://${host}/*`).sort());
  }
});

test('the fewest permissions: no tabs, scripting, history or all-urls', () => {
  assert.deepEqual([...manifest.permissions].sort(), ['alarms', 'sidePanel', 'storage']);
  assert.deepEqual(manifest.host_permissions, ['https://api.example.test/*']);
  assert.ok(!JSON.stringify(manifest).includes('<all_urls>'));
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, '4.0.0');
});

test('API and web bases are plain https origins', () => {
  assert.equal(baseUrl('https://api.ecomsbd.com', 'X'), 'https://api.ecomsbd.com');
  for (const bad of ['http://api.ecomsbd.com', 'https://u:p@api.ecomsbd.com', 'https://api.ecomsbd.com/v1', 'nope']) {
    assert.throws(() => baseUrl(bad, 'X'), undefined, bad);
  }
  assert.throws(() => buildManifest(template, { version: 'v4', apiBaseUrl: 'https://a.test' }));
});

test('every panel string has Bangla with the same placeholders', () => {
  assert.deepEqual(Object.keys(bn).sort(), Object.keys(en).sort());
  for (const [key, value] of Object.entries(bn)) {
    assert.ok(value.trim().length > 0, key);
    assert.deepEqual((value.match(/\{\w+\}/g) ?? []).sort(), (en[key].match(/\{\w+\}/g) ?? []).sort(), key);
  }
  assert.equal(translate('en', 'linked', { last4: '1234' }), 'Linked to your shop (key ••••1234)');
});

test('the selector-failure notice is the spec text in both languages', () => {
  const spec = "Ei page ekhon support na; text select kore 'Capture' korun";
  assert.equal(en.unsupported, spec);
  assert.equal(bn.unsupported, spec);
});
