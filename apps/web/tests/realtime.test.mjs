import assert from 'node:assert/strict';
import { afterEach, beforeEach, mock, test } from 'node:test';

import {
  CLOSE_FORBIDDEN,
  CLOSE_REBIND,
  COALESCE_MS,
  HEARTBEAT_MISSES,
  RealtimeClient,
  realtimeUrl,
  RETRY_MAX_MS,
  RETRY_REFUSED_MS,
  retryDelay,
  startRealtime,
} from '../lib/realtime.ts';
import {
  queryShowsTopics,
  revalidateEverything,
  revalidateTopics,
  subscribeToRevalidation,
} from '../lib/revalidate.ts';

/**
 * The web half of realtime: a socket that only ever causes refetches, comes
 * back on its own, binds to the session's shop, and is shared by every tab.
 */

const TOKEN = 'header.payload.signature';
let sockets;

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.closedWith = null;
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    sockets.push(this);
  }
  send(data) {
    this.sent.push(JSON.parse(data));
  }
  close(code) {
    this.readyState = 3;
    this.closedWith = code;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  message(body) {
    this.onmessage?.({ data: JSON.stringify(body) });
  }
  serverClose(code) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const last = () => sockets[sockets.length - 1];

function client(overrides = {}) {
  const calls = { invalidate: [], resync: 0, shop: [] };
  const instance = new RealtimeClient({
    url: 'wss://api.example.com/v1/realtime',
    getToken: async () => TOKEN,
    createSocket: (url) => new FakeSocket(url),
    random: () => 0.5,
    onInvalidate: (topics) => calls.invalidate.push([...topics].sort()),
    onResync: () => calls.resync++,
    onShop: (shopId) => calls.shop.push(shopId),
    ...overrides,
  });
  return { instance, calls };
}

async function connected(instance, shopId = 'shop-a') {
  instance.start();
  await settle();
  const socket = last();
  socket.open();
  socket.message({ type: 'ready', shop_id: shopId, heartbeat_seconds: 25 });
  return socket;
}

beforeEach(() => {
  sockets = [];
  mock.timers.enable({ apis: ['setTimeout'] });
});

afterEach(() => {
  mock.timers.reset();
});

test('the socket address comes from the API base, and carries no token', async () => {
  assert.equal(realtimeUrl('https://api.example.com/v1'), 'wss://api.example.com/v1/realtime');
  assert.equal(realtimeUrl('http://localhost:8000/v1/'), 'ws://localhost:8000/v1/realtime');

  const { instance } = client();
  const socket = await connected(instance);
  assert.equal(socket.url.includes(TOKEN), false);
  assert.deepEqual(socket.sent[0], { type: 'auth', access_token: TOKEN });
  instance.stop();
});

test('retries back off, with jitter, up to a ceiling', () => {
  assert.equal(retryDelay(0, () => 0), 500);
  assert.equal(retryDelay(0, () => 1), 1_000);
  assert.equal(retryDelay(3, () => 0.5), 6_000);
  assert.equal(retryDelay(20, () => 1), RETRY_MAX_MS);
  assert.ok(retryDelay(20, () => 0) >= RETRY_MAX_MS / 2);
});

test('a burst of invalidations becomes one refresh', async () => {
  const { instance, calls } = client();
  const socket = await connected(instance);
  socket.message({ type: 'invalidate', topic: 'orders', entity_id: null });
  socket.message({ type: 'invalidate', topic: 'customers', entity_id: null });
  socket.message({ type: 'invalidate', topic: 'orders', entity_id: null });
  assert.deepEqual(calls.invalidate, []);
  mock.timers.tick(COALESCE_MS);
  assert.deepEqual(calls.invalidate, [['customers', 'orders']]);
  instance.stop();
});

test('a lost connection comes back after a backoff and refetches what it missed', async () => {
  const { instance, calls } = client();
  const first = await connected(instance);
  assert.equal(calls.resync, 0, 'the first connection follows a fresh load');
  assert.deepEqual(calls.shop, ['shop-a']);

  first.serverClose(1006);
  await settle();
  assert.equal(sockets.length, 1, 'no reconnect before the backoff');
  mock.timers.tick(retryDelay(0, () => 0.5));
  await settle();
  assert.equal(sockets.length, 2);

  const second = last();
  second.open();
  second.message({ type: 'ready', shop_id: 'shop-a', heartbeat_seconds: 25 });
  assert.equal(calls.resync, 1);

  second.message({ type: 'resync' });
  assert.equal(calls.resync, 2);
  instance.stop();
});

test('a session moved to another shop reconnects at once and rebinds', async () => {
  const { instance, calls } = client();
  const first = await connected(instance, 'shop-a');
  first.serverClose(CLOSE_REBIND);
  await settle();
  assert.equal(sockets.length, 2, 'no backoff for a rebind');
  const second = last();
  second.open();
  second.message({ type: 'ready', shop_id: 'shop-b', heartbeat_seconds: 25 });
  assert.deepEqual(calls.shop, ['shop-a', 'shop-b']);
  instance.stop();
});

test('a shop switch here drops the old feed before anything else arrives', async () => {
  const { instance, calls } = client();
  const old = await connected(instance, 'shop-a');
  instance.rebind();
  assert.notEqual(old.closedWith, null);
  old.message({ type: 'invalidate', topic: 'orders' });
  mock.timers.tick(COALESCE_MS);
  assert.deepEqual(calls.invalidate, [], 'the old socket is no longer heard');
  await settle();
  assert.equal(sockets.length, 2);
  instance.stop();
});

test('refused, the client waits long before asking again', async () => {
  const { instance } = client();
  const socket = await connected(instance);
  socket.serverClose(CLOSE_FORBIDDEN);
  mock.timers.tick(RETRY_REFUSED_MS - 1);
  await settle();
  assert.equal(sockets.length, 1);
  mock.timers.tick(1);
  await settle();
  assert.equal(sockets.length, 2);
  instance.stop();
});

test('signing out closes the socket for good', async () => {
  const { instance } = client();
  const socket = await connected(instance);
  instance.stop();
  assert.equal(socket.closedWith, 1000);
  socket.serverClose(1006);
  mock.timers.tick(RETRY_MAX_MS * 2);
  await settle();
  assert.equal(sockets.length, 1);
});

test('silence longer than the heartbeat is treated as a dead connection', async () => {
  const { instance } = client();
  const socket = await connected(instance);
  mock.timers.tick(25_000 * HEARTBEAT_MISSES - 1);
  assert.equal(socket.closedWith, null);
  mock.timers.tick(1);
  assert.notEqual(socket.closedWith, null);
  mock.timers.tick(RETRY_MAX_MS);
  await settle();
  assert.equal(sockets.length, 2);
  instance.stop();
});

test('back online, the client reconnects without waiting out the backoff', async () => {
  const { instance } = client();
  const socket = await connected(instance);
  socket.serverClose(1006);
  instance.wake();
  await settle();
  assert.equal(sockets.length, 2);
  instance.stop();
});

test('a refreshed token is handed to the open socket', async () => {
  let token = TOKEN;
  const { instance } = client({ getToken: async () => token });
  const socket = await connected(instance);
  token = 'fresh.token.value';
  await instance.reauth();
  assert.deepEqual(socket.sent.at(-1), { type: 'auth', access_token: 'fresh.token.value' });
  instance.stop();
});

test('without a session there is no socket', async () => {
  const { instance } = client({ getToken: async () => null });
  instance.start();
  await settle();
  assert.equal(sockets.length, 0);
  instance.stop();
});

// ------------------------------------------------------------------ tabs --

function bus() {
  const members = [];
  return () => {
    const member = {
      onmessage: null,
      postMessage(message) {
        for (const other of members) {
          if (other !== member) {
            other.onmessage?.({ data: structuredClone(message) });
          }
        }
      },
      close() {},
    };
    members.push(member);
    return member;
  };
}

function locks() {
  let held = false;
  const queue = [];
  const grant = () => {
    if (held || queue.length === 0) {
      return;
    }
    const { callback, resolve } = queue.shift();
    held = true;
    Promise.resolve(callback()).then(() => {
      held = false;
      resolve();
      grant();
    });
  };
  return {
    request(_name, options, callback) {
      return new Promise((resolve, reject) => {
        const entry = { callback, resolve };
        options.signal?.addEventListener('abort', () => {
          const index = queue.indexOf(entry);
          if (index >= 0) {
            queue.splice(index, 1);
            reject(new Error('aborted'));
          }
        });
        queue.push(entry);
        grant();
      });
    },
  };
}

function tab(channel, lockManager) {
  const seen = { invalidate: [], resync: 0, shop: [] };
  const handle = startRealtime({
    url: 'wss://api.example.com/v1/realtime',
    getToken: async () => TOKEN,
    createSocket: (url) => new FakeSocket(url),
    channel,
    locks: lockManager,
    onInvalidate: (topics) => seen.invalidate.push([...topics].sort()),
    onResync: () => seen.resync++,
    onShop: (shopId) => seen.shop.push(shopId),
  });
  return { handle, seen };
}

test('one socket per browser, and every tab hears it', async () => {
  const join = bus();
  const lockManager = locks();
  const first = tab(join(), lockManager);
  const second = tab(join(), lockManager);
  await settle();
  assert.equal(first.handle.leading, true);
  assert.equal(second.handle.leading, false);
  assert.equal(sockets.length, 1);

  const socket = last();
  socket.open();
  socket.message({ type: 'ready', shop_id: 'shop-a', heartbeat_seconds: 25 });
  socket.message({ type: 'invalidate', topic: 'products' });
  mock.timers.tick(COALESCE_MS);
  assert.deepEqual(first.seen.invalidate, [['products']]);
  assert.deepEqual(second.seen.invalidate, [['products']]);
  assert.deepEqual(second.seen.shop, ['shop-a']);

  // A switch made in the second tab moves the leader's socket.
  second.handle.rebind();
  await settle();
  assert.notEqual(socket.closedWith, null);
  assert.equal(sockets.length, 2);

  // The leader's tab closes: the other takes over.
  first.handle.stop();
  await settle();
  await settle();
  assert.equal(second.handle.leading, true);
  assert.equal(sockets.length, 3);
  second.handle.stop();
});

// ------------------------------------------------------------ revalidate --

test('realtime refreshes only what shows the topic, and only in view', () => {
  const win = new EventTarget();
  win.setInterval = () => 0;
  win.clearInterval = () => {};
  const doc = new EventTarget();
  doc.visibilityState = 'visible';
  globalThis.window = win;
  globalThis.document = doc;
  try {
    const requests = [];
    const stop = subscribeToRevalidation((request) => requests.push(request));

    revalidateTopics(new Set(['orders']));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].fresh, true);
    assert.deepEqual([...requests[0].topics], ['orders']);

    revalidateEverything();
    assert.equal(requests[1].topics, null);
    assert.equal(requests[1].fresh, true);

    doc.visibilityState = 'hidden';
    revalidateTopics(new Set(['orders']));
    revalidateEverything();
    assert.equal(requests.length, 2, 'a hidden tab catches up when it is shown');
    stop();
  } finally {
    delete globalThis.window;
    delete globalThis.document;
  }

  assert.equal(queryShowsTopics('/orders', new Set(['customers'])), true);
  assert.equal(queryShowsTopics('/me', new Set(['orders'])), false);
  assert.equal(queryShowsTopics('/me', new Set(['team'])), true);
  assert.equal(queryShowsTopics('/team/invitations', new Set(['shop'])), false);
  assert.equal(queryShowsTopics('/me', null), true);
});
