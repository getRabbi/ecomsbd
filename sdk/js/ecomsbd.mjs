// ecomsbd Public API: a thin JavaScript client and webhook verifier.
// Works in Node 18+ (global fetch) and any runtime with fetch + node:crypto.
// Copy this file into your project; there are no dependencies.

import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto';

/** Every call this client makes, as [method, path template]. */
export const ROUTES = [
  ['GET', '/me'],
  ['GET', '/orders'],
  ['GET', '/orders/{order_id}'],
  ['POST', '/orders'],
  ['POST', '/orders/{order_id}/status'],
  ['POST', '/sources/{source_id}/orders'],
  ['GET', '/customers'],
  ['POST', '/customers'],
  ['GET', '/products'],
  ['GET', '/inventory/{product_id}'],
  ['POST', '/inventory/{product_id}/adjustments'],
];

export const SIGNATURE_HEADER = 'x-ecomsbd-signature';
export const EVENT_ID_HEADER = 'x-ecomsbd-event-id';
export const TOLERANCE_SECONDS = 300;

export class EcomsbdError extends Error {
  constructor(status, body, retryAfter) {
    super(`${status} ${body?.code ?? 'ERROR'}: ${body?.message_en ?? ''}`);
    this.status = status;
    this.code = body?.code;
    this.body = body;
    this.retryAfter = retryAfter;
  }
}

export class Client {
  /** @param {{ apiKey: string, baseUrl: string, fetch?: typeof fetch }} options */
  constructor({ apiKey, baseUrl, fetch: fetchImpl }) {
    if (!apiKey?.startsWith('ec_live_')) throw new Error('Use an ecomsbd API key (ec_live_...)');
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.fetch = fetchImpl ?? globalThis.fetch;
  }

  async request(method, path, { body, query, idempotencyKey } = {}) {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    const headers = { Authorization: `Bearer ${this.apiKey}`, Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    // Reuse the same key when retrying the same request; never for a new one.
    if (method === 'POST') headers['Idempotency-Key'] = idempotencyKey ?? randomUUID();
    const response = await this.fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (response.status === 409 && data.result === 'CONFLICT') return data; // cancel after booking
    if (!response.ok) {
      const retry = response.headers.get('retry-after');
      throw new EcomsbdError(response.status, data, retry ? Number(retry) : undefined);
    }
    return data;
  }

  /** Which key this is, its shop and scopes. A safe connection test. */
  me() { return this.request('GET', '/me'); }

  /** Custom Website: send an order. The same externalOrderId is never duplicated. */
  sendSourceOrder(sourceId, externalOrderId, payload, { idempotencyKey } = {}) {
    return this.request('POST', `/sources/${sourceId}/orders`, {
      body: { external_order_id: externalOrderId, payload },
      idempotencyKey: idempotencyKey ?? `order-${externalOrderId}`,
    });
  }

  createOrder(order, { idempotencyKey } = {}) { return this.request('POST', '/orders', { body: order, idempotencyKey }); }
  getOrder(orderId) { return this.request('GET', `/orders/${orderId}`); }
  listOrders({ limit = 30, offset = 0 } = {}) { return this.request('GET', '/orders', { query: { limit, offset } }); }

  /** CONFIRMED or CANCELLED. A booked order answers result: 'CONFLICT' (HTTP 409). */
  setOrderStatus(orderId, status, { reason, idempotencyKey } = {}) {
    return this.request('POST', `/orders/${orderId}/status`, {
      body: { status, reason: reason ?? null },
      idempotencyKey: idempotencyKey ?? `status-${orderId}-${status}`,
    });
  }

  async findProductsBySku(sku) { return (await this.request('GET', '/products', { query: { sku } })).items; }
  getInventory(productId) { return this.request('GET', `/inventory/${productId}`); }

  /** Needs the inventory:write scope. idempotencyKey is required. */
  adjustInventory(productId, quantityDelta, { reason = 'MANUAL_ADJUSTMENT', variantId, note, idempotencyKey }) {
    if (!idempotencyKey) throw new Error('adjustInventory needs an idempotencyKey');
    const body = { quantity_delta: quantityDelta, reason };
    if (variantId) body.variant_id = variantId;
    if (note) body.note = note;
    return this.request('POST', `/inventory/${productId}/adjustments`, { body, idempotencyKey });
  }
}

export function sign(secret, rawBody, timestamp) {
  const digest = createHmac('sha256', secret).update(`${timestamp}.`).update(rawBody).digest('hex');
  return `t=${timestamp},v1=${digest}`;
}

/**
 * True only for an untampered body signed within the tolerance.
 * Pass the raw body (string or Buffer) exactly as received, before JSON parsing.
 */
export function verifyWebhook(secret, rawBody, header, { tolerance = TOLERANCE_SECONDS, now } = {}) {
  if (typeof header !== 'string') return false;
  const parts = Object.fromEntries(header.split(',').map((p) => p.split('=', 2)));
  const stamp = Number(parts.t);
  if (!Number.isInteger(stamp) || !parts.v1) return false;
  const current = now ?? Math.floor(Date.now() / 1000);
  if (Math.abs(current - stamp) > tolerance) return false;
  const expected = Buffer.from(sign(secret, rawBody, stamp).split('v1=')[1]);
  const given = Buffer.from(parts.v1);
  return expected.length === given.length && timingSafeEqual(expected, given);
}

/** Remembers recent event IDs so a redelivered event is processed once. Use your DB in production. */
export class ReplayGuard {
  constructor(size = 10000) { this.seen = new Set(); this.size = size; }
  firstTime(eventId) {
    if (this.seen.has(eventId)) return false;
    this.seen.add(eventId);
    if (this.seen.size > this.size) this.seen.delete(this.seen.values().next().value);
    return true;
  }
}
