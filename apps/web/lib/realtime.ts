'use client';

/**
 * Hear about changes to the shop as they are committed, from any device.
 *
 * The API's `/v1/realtime` socket sends **invalidations only** — "orders
 * changed", never the order. Nothing here writes a byte of business data: an
 * invalidation makes the screens that show that topic ask FastAPI again,
 * through the same `useApi` path, permissions and tenant scoping as any other
 * read. So a missed message costs a few seconds of freshness and nothing else,
 * and the 30-second refresh in `./revalidate` stays as the backstop it was.
 *
 * One socket per browser, not per tab: the tab holding the Web Lock below owns
 * it and repeats what it hears to the other tabs over a BroadcastChannel. When
 * that tab closes, the lock passes to another and it connects instead.
 */

//: Application close codes from the API (`app/api/v1/realtime.py`).
export const CLOSE_UNAUTHENTICATED = 4401;
export const CLOSE_FORBIDDEN = 4403;
export const CLOSE_REBIND = 4409;
export const CLOSE_UNAVAILABLE = 1013;

//: First retry after a lost connection; doubles to the ceiling, with jitter.
export const RETRY_BASE_MS = 1_000;
export const RETRY_MAX_MS = 30_000;
//: A shop the session cannot read, or realtime switched off: try much later.
export const RETRY_REFUSED_MS = 60_000;
export const RETRY_DISABLED_MS = 300_000;
//: Invalidations arriving this close together cause one refresh, not several.
export const COALESCE_MS = 500;
//: Silence for this many heartbeats means the connection is dead (a laptop
//: lid, a captive portal), even though the browser has not noticed.
export const HEARTBEAT_MISSES = 2.5;

export interface SocketLike {
  readyState: number;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export interface RealtimeHandlers {
  /** These topics changed: refetch what shows them. */
  onInvalidate: (topics: ReadonlySet<string>) => void;
  /** Messages may have been missed: refetch everything on screen. */
  onResync: () => void;
  /** The server bound this browser's socket to this shop. */
  onShop: (shopId: string) => void;
}

export interface RealtimeClientOptions extends RealtimeHandlers {
  url: string;
  getToken: () => Promise<string | null>;
  createSocket?: (url: string) => SocketLike;
  random?: () => number;
}

const OPEN = 1;

/** `https://api.example.com/v1` → `wss://api.example.com/v1/realtime`. */
export function realtimeUrl(apiBaseUrl: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base.replace(/^http/, 'ws')}/realtime`;
}

/** How long to wait before attempt number `attempt` (0-based). */
export function retryDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt);
  // "Equal jitter": never sooner than half the step, so a server restart does
  // not get every browser back in the same second.
  return Math.round(ceiling / 2 + random() * (ceiling / 2));
}

/**
 * One socket's lifecycle: connect, authenticate, listen, and come back.
 *
 * Framework-free so it can be driven with a fake socket and fake timers.
 */
export class RealtimeClient {
  private readonly options: RealtimeClientOptions;
  private socket: SocketLike | null = null;
  private running = false;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 25_000;
  private everReady = false;
  private pending = new Set<string>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: RealtimeClientOptions) {
    this.options = options;
  }

  get connected(): boolean {
    return this.socket !== null && this.socket.readyState === OPEN;
  }

  start(): void {
    if (this.running) {
      return;
    }
    this.running = true;
    void this.connect();
  }

  /** Signed out, or this tab stopped leading: close and stay closed. */
  stop(): void {
    this.running = false;
    this.clearRetry();
    this.clearWatchdog();
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this.pending.clear();
    this.drop(1000, 'stopped');
  }

  /** The shop changed: the old shop's feed ends now, and a new one starts. */
  rebind(): void {
    if (!this.running) {
      return;
    }
    this.drop(1000, 'rebind');
    this.clearRetry();
    this.attempt = 0;
    void this.connect();
  }

  /** The network or the tab is back: don't wait out a long backoff. */
  wake(): void {
    if (!this.running || this.socket !== null) {
      return;
    }
    this.clearRetry();
    this.attempt = 0;
    void this.connect();
  }

  /** The access token was refreshed: hand the open socket the new one. */
  async reauth(): Promise<void> {
    const socket = this.socket;
    if (!socket || socket.readyState !== OPEN) {
      return;
    }
    const token = await this.options.getToken();
    if (token && this.socket === socket && socket.readyState === OPEN) {
      socket.send(JSON.stringify({ type: 'auth', access_token: token }));
    }
  }

  private async connect(): Promise<void> {
    if (!this.running || this.socket !== null) {
      return;
    }
    const token = await this.options.getToken();
    if (!this.running || this.socket !== null) {
      return;
    }
    if (!token) {
      this.scheduleRetry();
      return;
    }
    const create =
      this.options.createSocket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
    let socket: SocketLike;
    try {
      // The token goes in the first message, never the URL: URLs are logged.
      socket = create(this.options.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'auth', access_token: token }));
      this.armWatchdog();
    };
    socket.onmessage = (event) => {
      if (this.socket === socket) {
        this.receive(event.data);
      }
    };
    socket.onerror = () => {
      // Always followed by `close`, which decides what happens next.
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.clearWatchdog();
      this.closed(event.code);
    };
  }

  private receive(raw: unknown): void {
    this.armWatchdog();
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(String(raw)) as Record<string, unknown>;
    } catch {
      return;
    }
    switch (message.type) {
      case 'ready': {
        if (typeof message.heartbeat_seconds === 'number' && message.heartbeat_seconds > 0) {
          this.heartbeatMs = message.heartbeat_seconds * 1000;
          this.armWatchdog();
        }
        this.attempt = 0;
        if (typeof message.shop_id === 'string') {
          this.options.onShop(message.shop_id);
        }
        // Anything committed while this browser was not connected was not
        // announced to it. The first connection follows a fresh load.
        if (this.everReady) {
          this.options.onResync();
        }
        this.everReady = true;
        break;
      }
      case 'invalidate':
        if (typeof message.topic === 'string') {
          this.queue(message.topic);
        }
        break;
      case 'resync':
        this.options.onResync();
        break;
      default:
        // ping, pong, and anything a newer server adds.
        break;
    }
  }

  private queue(topic: string): void {
    this.pending.add(topic);
    if (this.flushTimer === null) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        const topics = this.pending;
        this.pending = new Set();
        if (topics.size > 0) {
          this.options.onInvalidate(topics);
        }
      }, COALESCE_MS);
    }
  }

  private closed(code: number): void {
    if (!this.running) {
      return;
    }
    if (code === CLOSE_REBIND) {
      // The session now points at another shop. Bind to it straight away.
      this.attempt = 0;
      void this.connect();
      return;
    }
    if (code === CLOSE_FORBIDDEN) {
      this.scheduleRetry(RETRY_REFUSED_MS);
      return;
    }
    if (code === CLOSE_UNAVAILABLE) {
      this.scheduleRetry(RETRY_DISABLED_MS);
      return;
    }
    // 4401 included: the next attempt reads the token again, which refreshes
    // an expired one; if the session really ended, `api.ts` signs out.
    this.scheduleRetry();
  }

  private scheduleRetry(delay?: number): void {
    this.clearRetry();
    if (!this.running) {
      return;
    }
    const wait = delay ?? retryDelay(this.attempt, this.options.random);
    this.attempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.connect();
    }, wait);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private armWatchdog(): void {
    this.clearWatchdog();
    this.watchdog = setTimeout(() => {
      this.watchdog = null;
      const socket = this.socket;
      if (socket) {
        // Treat the silence as the close it is.
        this.socket = null;
        try {
          socket.close(4000, 'heartbeat timeout');
        } catch {
          // Already gone.
        }
        this.closed(1006);
      }
    }, this.heartbeatMs * HEARTBEAT_MISSES);
  }

  private clearWatchdog(): void {
    if (this.watchdog !== null) {
      clearTimeout(this.watchdog);
      this.watchdog = null;
    }
  }

  private drop(code: number, reason: string): void {
    const socket = this.socket;
    this.socket = null;
    this.clearWatchdog();
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      try {
        socket.close(code, reason);
      } catch {
        // Already closed.
      }
    }
  }
}

// --------------------------------------------------------------------------- //
// One socket per browser
// --------------------------------------------------------------------------- //

export const LEADER_LOCK = 'ecomsbd-realtime-leader';
export const TAB_CHANNEL = 'ecomsbd-realtime';

type TabMessage =
  | { kind: 'invalidate'; topics: string[] }
  | { kind: 'resync' }
  | { kind: 'shop'; shopId: string }
  | { kind: 'rebind' };

interface LockManagerLike {
  request(
    name: string,
    options: { signal?: AbortSignal },
    callback: () => Promise<void> | void,
  ): Promise<unknown>;
}

export interface CoordinatorOptions extends RealtimeHandlers {
  url: string;
  getToken: () => Promise<string | null>;
  createSocket?: (url: string) => SocketLike;
  /** Defaults to `navigator.locks`; without one, every tab connects itself. */
  locks?: LockManagerLike | null;
  /** Defaults to a `BroadcastChannel`; without one, tabs don't share. */
  channel?: BroadcastChannelLike | null;
}

export interface BroadcastChannelLike {
  postMessage(message: TabMessage): void;
  onmessage: ((event: { data: TabMessage }) => void) | null;
  close(): void;
}

export interface RealtimeHandle {
  stop(): void;
  rebind(): void;
  reauth(): void;
  wake(): void;
  /** Whether this tab holds the socket. */
  readonly leading: boolean;
}

/** Start realtime for this tab: lead it, or follow the tab that does. */
export function startRealtime(options: CoordinatorOptions): RealtimeHandle {
  const channel =
    options.channel !== undefined
      ? options.channel
      : typeof BroadcastChannel !== 'undefined'
        ? (new BroadcastChannel(TAB_CHANNEL) as unknown as BroadcastChannelLike)
        : null;
  const locks =
    options.locks !== undefined
      ? options.locks
      : typeof navigator !== 'undefined' && 'locks' in navigator
        ? (navigator.locks as unknown as LockManagerLike)
        : null;

  let client: RealtimeClient | null = null;
  let stopped = false;
  let release: (() => void) | null = null;
  const abort = typeof AbortController !== 'undefined' ? new AbortController() : null;

  const share = (message: TabMessage) => {
    try {
      channel?.postMessage(message);
    } catch {
      // A closed channel: the other tabs fall back to their own refresh.
    }
  };

  const lead = () => {
    client = new RealtimeClient({
      url: options.url,
      getToken: options.getToken,
      createSocket: options.createSocket,
      onInvalidate: (topics) => {
        options.onInvalidate(topics);
        share({ kind: 'invalidate', topics: [...topics] });
      },
      onResync: () => {
        options.onResync();
        share({ kind: 'resync' });
      },
      onShop: (shopId) => {
        options.onShop(shopId);
        share({ kind: 'shop', shopId });
      },
    });
    client.start();
  };

  if (channel) {
    channel.onmessage = (event) => {
      const message = event.data;
      if (client) {
        if (message.kind === 'rebind') {
          client.rebind();
        }
        return;
      }
      switch (message.kind) {
        case 'invalidate':
          options.onInvalidate(new Set(message.topics));
          break;
        case 'resync':
          options.onResync();
          break;
        case 'shop':
          options.onShop(message.shopId);
          break;
        default:
          break;
      }
    };
  }

  if (locks) {
    locks
      .request(LEADER_LOCK, abort ? { signal: abort.signal } : {}, () => {
        if (stopped) {
          return;
        }
        lead();
        // Held until this tab stops: closing the tab releases it too.
        return new Promise<void>((resolve) => {
          release = resolve;
        });
      })
      .catch(() => {
        // Aborted while waiting in line: this tab stopped before leading.
      });
  } else {
    lead();
  }

  return {
    stop() {
      stopped = true;
      abort?.abort();
      client?.stop();
      client = null;
      release?.();
      release = null;
      if (channel) {
        channel.onmessage = null;
        if (options.channel === undefined) {
          channel.close();
        }
      }
    },
    rebind() {
      if (client) {
        client.rebind();
      } else {
        share({ kind: 'rebind' });
      }
    },
    reauth() {
      void client?.reauth();
    },
    wake() {
      client?.wake();
    },
    get leading() {
      return client !== null;
    },
  };
}

// The handle for this tab, so a shop switch can ask for a rebind without
// threading it through React.
let active: RealtimeHandle | null = null;

export function setActiveRealtime(handle: RealtimeHandle | null): void {
  active = handle;
}

/** After this tab switches shop: drop the old shop's feed now. */
export function rebindRealtime(): void {
  active?.rebind();
}
