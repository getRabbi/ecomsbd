import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { HOSTS, hostSelectors, isHost, parseRules, refreshDue } from '../src/shared/rules.ts';

const seed = JSON.parse(readFileSync(new URL('./fixtures/rules_v1.json', import.meta.url), 'utf8'));
const clone = () => JSON.parse(JSON.stringify(seed));

function withSelectors(host, selectors, verified_at = '2026-11-20') {
  const body = clone();
  body.rules.extension.hosts[host] = { selectors, verified_at };
  return parseRules(body);
}

const FULL = { conversation: '[data-chat]', message: '[data-msg]', message_text: '.text', sender: 'header h2' };

test('the seed parses and ships no selectors: every host captures the selection', () => {
  const body = parseRules(seed);
  assert.ok(body);
  assert.equal(body.version, seed.version);
  for (const host of HOSTS) assert.equal(hostSelectors(body, host), null, host);
});

test('only the three chat hosts', () => {
  assert.deepEqual([...HOSTS].sort(), ['business.facebook.com', 'web.whatsapp.com', 'www.messenger.com']);
  for (const host of ['facebook.com', 'm.me', 'whatsapp.com', 'evil.web.whatsapp.com.example']) {
    assert.equal(isHost(host), false, host);
  }
});

test('verified selectors are used only as a complete set', () => {
  assert.deepEqual(hostSelectors(withSelectors('web.whatsapp.com', FULL), 'web.whatsapp.com'), FULL);
  const noText = withSelectors('www.messenger.com', { ...FULL, message_text: null });
  assert.equal(hostSelectors(noText, 'www.messenger.com').message_text, null);
  for (const missing of ['conversation', 'message', 'sender']) {
    const body = withSelectors('web.whatsapp.com', { ...FULL, [missing]: null });
    assert.equal(hostSelectors(body, 'web.whatsapp.com'), null, missing);
  }
  const blank = withSelectors('web.whatsapp.com', { ...FULL, sender: '  ' });
  assert.equal(hostSelectors(blank, 'web.whatsapp.com'), null);
});

test('selectors without verified_at are never used', () => {
  const body = withSelectors('web.whatsapp.com', FULL, null);
  assert.equal(hostSelectors(body, 'web.whatsapp.com'), null);
  assert.equal(hostSelectors(withSelectors('web.whatsapp.com', FULL), 'www.messenger.com'), null, 'per host');
});

test('a body this build does not understand is refused (the last good rules stay)', () => {
  assert.equal(parseRules(null), null);
  assert.equal(parseRules({ ...clone(), schema_version: 2 }), null);
  const broken = clone();
  broken.rules.phone.patterns = ['(unclosed'];
  assert.equal(parseRules(broken), null);
  const noPhone = clone();
  delete noPhone.rules.phone;
  assert.equal(parseRules(noPhone), null);
});

test('rules are re-checked after refresh_after_seconds, never more than hourly', () => {
  const body = parseRules(seed);
  const stored = { body, etag: '"v1"', fetched_at: 1_000_000 };
  assert.equal(refreshDue(null), true);
  assert.equal(refreshDue(stored, 1_000_000 + body.refresh_after_seconds * 1000 - 1), false);
  assert.equal(refreshDue(stored, 1_000_000 + body.refresh_after_seconds * 1000), true);
  assert.equal(parseRules({ ...clone(), refresh_after_seconds: 5 }).refresh_after_seconds, 3600);
});
