import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { BOOKABLE_REASONS, bookableReasonKey } from '../lib/couriers.ts';
import { opsBn, opsEn } from '../lib/ops-strings.ts';

test('every bookable reason reads as words, in English and Bangla', () => {
  for (const reason of BOOKABLE_REASONS) {
    const key = bookableReasonKey(reason);
    assert.equal(key, `cour.why.${reason}`);
    for (const table of [opsEn, opsBn]) {
      const text = table[key];
      assert.ok(text && text.trim().length > 0, `${key} has text`);
      assert.ok(!text.includes(reason), `${key} does not show the raw code`);
      assert.ok(!/[A-Z]{3,}_[A-Z]/.test(text), `${key} has no enum-like word`);
    }
  }
});

test('no reason and an unknown reason still read as words', () => {
  assert.equal(bookableReasonKey(null), 'cour.why.NOT_CONNECTED');
  assert.equal(bookableReasonKey('SOMETHING_NEW'), 'cour.why.UNKNOWN');
  assert.ok(opsEn['cour.why.UNKNOWN'] && opsBn['cour.why.UNKNOWN']);
});

test('the list matches the backend BookableReason enum', () => {
  const source = readFileSync(new URL('../../../backend/app/couriers/accounts.py', import.meta.url), 'utf8');
  const block = source.slice(source.indexOf('class BookableReason'));
  const body = block.slice(0, block.indexOf('\n\n\n'));
  const backend = [...body.matchAll(/^\s{4}([A-Z_]+) = "([A-Z_]+)"$/gm)].map((match) => match[2]);
  assert.ok(backend.length >= 6, 'parsed the backend enum');
  assert.deepEqual([...backend].sort(), [...BOOKABLE_REASONS].sort());
});
