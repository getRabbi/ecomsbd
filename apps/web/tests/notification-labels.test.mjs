import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

// Run 14: the centre showed the raw key `notif.cat.ORDERS` (no label for the
// ORDERS category). Read from source: `lib/i18n.ts` uses extensionless imports
// that the node test runner cannot load.
const backend = readFileSync(new URL('../../../backend/app/notifications/models.py', import.meta.url), 'utf8');
const start = backend.indexOf('class NotificationCategory');
const block = backend.slice(start, backend.indexOf('\n\n\n', start));
const categories = [...block.matchAll(/^\s+([A-Z_]+) = "([A-Z_]+)"/gm)].map((m) => m[2]);

const libDir = new URL('../lib/', import.meta.url);
const strings = readdirSync(libDir)
  .filter((name) => name.endsWith('.ts'))
  .map((name) => readFileSync(new URL(name, libDir), 'utf8'))
  .join('\n');

function definitions(key) {
  return [...strings.matchAll(new RegExp(`['"]${key.replaceAll('.', '\\.')}['"]\\s*:\\s*['"]([^'"]+)['"]`, 'g'))].map((m) => m[1]);
}

test('every notification category the backend sends has a label in English and Bangla', () => {
  assert.ok(categories.includes('ORDERS') && categories.length >= 8, categories.join(','));
  for (const category of categories) {
    const labels = definitions(`notif.cat.${category}`);
    assert.equal(labels.length, 2, `notif.cat.${category}: ${labels.length} definitions`);
    assert.ok(labels.every((label) => label.trim()), category);
  }
  assert.deepEqual(definitions('notif.cat.ORDERS'), ['Orders', 'অর্ডার']);
});

test('the centre filters by every category, and the priority filter says "all priorities"', () => {
  const source = readFileSync(new URL('../lib/notifications.ts', import.meta.url), 'utf8');
  const listed = source.slice(source.indexOf('NOTIFICATION_CATEGORIES = ['), source.indexOf('] as const'));
  for (const category of categories.filter((c) => c !== 'SUMMARY')) {
    assert.ok(listed.includes(`'${category}'`), category);
  }
  assert.deepEqual(definitions('notif.sev.all'), ['All priorities', 'সব গুরুত্ব']);
  const page = readFileSync(new URL('../app/(app)/notifications/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /notif\.severity[\s\S]{0,300}notif\.sev\.all/);
});
