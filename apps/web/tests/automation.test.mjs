/**
 * Automation builder logic and copy (V3.4). Run: npm test
 *
 * No framework: Node's own runner, importing the TypeScript sources directly.
 * The backend's closed lists are read from its schema file, so a trigger,
 * action or condition added there without English and Bangla copy fails here.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { defaultConfig, emptyGroup, flatten, newId, provides } from '../lib/automation.ts';
import { automationBn, automationEn } from '../lib/automation-strings.ts';

const schemas = readFileSync(new URL('../../../backend/app/automation/schemas.py', import.meta.url), 'utf8');

function keysOf(block) {
  const body = schemas.split(block)[1].split('\n}')[0];
  return [...body.matchAll(/^\s+"([a-z_.A-Z]+)":/gm)].map((m) => m[1]);
}

test('every backend trigger, action, field and wait has English and Bangla copy', () => {
  const lists = {
    trigger: keysOf('TRIGGER_SUBJECTS: dict[str, str] = {'),
    action: keysOf('CONFIGS: dict[str, type[Input]] = {'),
    field: keysOf('FIELDS: dict[str, tuple[str | None, str, frozenset[str]]] = {'),
    event: keysOf('WAIT_EVENTS: dict[str, str] = {'),
  };
  assert.ok(lists.trigger.length >= 18 && lists.action.length >= 11 && lists.field.length >= 20);
  for (const [kind, keys] of Object.entries(lists)) {
    for (const key of keys) {
      const name = `auto.${kind}.${key}`;
      assert.ok(name in automationEn, `missing English: ${name}`);
      assert.ok(automationBn[name], `missing Bangla: ${name}`);
    }
  }
});

test('English and Bangla have the same keys, and Bangla is Bangla', () => {
  assert.deepEqual(Object.keys(automationBn).sort(), Object.keys(automationEn).sort());
  const bangla = Object.values(automationBn).filter((v) => /[ঀ-৿]/.test(v));
  // Brand-only strings such as "Email" may stay English; nearly all must not.
  assert.ok(bangla.length / Object.keys(automationBn).length > 0.97);
});

test('step ids fit the server pattern and are unique', () => {
  const ids = new Set();
  for (let i = 0; i < 500; i += 1) {
    const id = newId('act');
    assert.match(id, /^[a-z0-9_-]{1,32}$/);
    ids.add(id);
  }
  assert.ok(ids.size > 490);
});

test('flatten walks branches depth first; provides follows the trigger subject', () => {
  const steps = [
    { type: 'action', id: 'a', action: 'ADD_TAG', config: {} },
    {
      type: 'branch',
      id: 'b',
      conditions: emptyGroup(),
      then: [{ type: 'delay', id: 'c', mode: 'duration', minutes: 5 }],
      else: [{ type: 'action', id: 'd', action: 'CREATE_FOLLOWUP', config: {} }],
    },
  ];
  assert.deepEqual(flatten(steps).map((s) => s.id), ['a', 'b', 'c', 'd']);
  const catalog = {
    workflow_triggers: [{ key: 'order.created', subject: 'order' }, { key: 'inventory.low', subject: 'product' }],
    subjects: { order: ['customer', 'order'], product: ['product'] },
  };
  assert.deepEqual([...provides(catalog, 'order.created')].sort(), ['customer', 'order']);
  assert.deepEqual([...provides(catalog, 'inventory.low')], ['product']);
});

test('new actions start from safe defaults', () => {
  const catalog = { templates: [{ key: 'order_update', channel: 'EMAIL' }], tags: [], couriers: ['manual', 'pathao'] };
  assert.deepEqual(defaultConfig('SEND_TEMPLATE', catalog, 'bn'), { template_key: 'order_update', locale: 'bn', channel: 'EMAIL' });
  assert.deepEqual(defaultConfig('BOOK_COURIER', catalog, 'en'), { provider: 'pathao' });
  assert.equal(defaultConfig('SELLER_NOTIFICATION', catalog, 'en').audience, 'OPERATIONS');
});
