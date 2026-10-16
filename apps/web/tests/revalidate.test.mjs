import assert from 'node:assert/strict';
import { afterEach, beforeEach, mock, test } from 'node:test';

import {
  keepIfUnchanged,
  REVALIDATE_EVERY_MS,
  subscribeToRevalidation,
} from '../lib/revalidate.ts';

/**
 * A change made on the phone reaches an open web screen without a reload: on
 * the tab's return to view, and on a timer while it stays in view — never while
 * it is hidden, and never by replacing an identical answer.
 */

let doc;

beforeEach(() => {
  mock.timers.enable({ apis: ['setInterval'] });
  const win = new EventTarget();
  win.setInterval = setInterval;
  win.clearInterval = clearInterval;
  doc = new EventTarget();
  doc.visibilityState = 'visible';
  globalThis.window = win;
  globalThis.document = doc;
});

afterEach(() => {
  mock.timers.reset();
  delete globalThis.window;
  delete globalThis.document;
});

test('a tab coming back into view refreshes what is on screen', () => {
  let calls = 0;
  const stop = subscribeToRevalidation(() => calls++);

  window.dispatchEvent(new Event('focus'));
  doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(calls, 2);

  stop();
});

test('an open tab refreshes on a timer, and a hidden one does not', () => {
  let calls = 0;
  const stop = subscribeToRevalidation(() => calls++);

  mock.timers.tick(REVALIDATE_EVERY_MS);
  assert.equal(calls, 1);

  doc.visibilityState = 'hidden';
  mock.timers.tick(REVALIDATE_EVERY_MS * 3);
  doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(calls, 1);

  stop();
});

test('one timer serves every panel, and it stops with the last one', () => {
  const seen = [];
  const stopA = subscribeToRevalidation(() => seen.push('a'));
  const stopB = subscribeToRevalidation(() => seen.push('b'));

  mock.timers.tick(REVALIDATE_EVERY_MS);
  assert.deepEqual(seen, ['a', 'b']);

  stopA();
  mock.timers.tick(REVALIDATE_EVERY_MS);
  assert.deepEqual(seen, ['a', 'b', 'b']);

  stopB();
  mock.timers.tick(REVALIDATE_EVERY_MS);
  window.dispatchEvent(new Event('focus'));
  assert.deepEqual(seen, ['a', 'b', 'b']);
});

test('an identical answer keeps the object on screen; a changed one replaces it', () => {
  const shown = { items: [{ id: 'o1', status: 'CONFIRMED' }], has_more: false };

  assert.equal(keepIfUnchanged(shown, structuredClone(shown)), shown);

  const changed = { items: [{ id: 'o1', status: 'SHIPPED' }], has_more: false };
  assert.equal(keepIfUnchanged(shown, changed), changed);
  assert.equal(keepIfUnchanged(null, changed), changed);
});
