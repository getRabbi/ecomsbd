// Types for ecomsbd.mjs.

export declare const ROUTES: ReadonlyArray<[method: 'GET' | 'POST', path: string]>;
export declare const SIGNATURE_HEADER: 'x-ecomsbd-signature';
export declare const EVENT_ID_HEADER: 'x-ecomsbd-event-id';
export declare const TOLERANCE_SECONDS: 300;

export interface OrderItem {
  product_id?: string;
  variant_id?: string;
  name?: string;
  quantity?: number;
  unit_price_paisa?: number;
  discount_paisa?: number;
  variant_label?: string;
  note?: string;
}

export interface OrderPayload {
  phone: string;
  items: OrderItem[];
  customer_name?: string;
  address?: string;
  district?: string;
  area?: string;
  cod_amount_paisa?: number;
  discount_paisa?: number;
  delivery_fee_paisa?: number;
  note?: string;
}

export declare class EcomsbdError extends Error {
  status: number;
  code?: string;
  body: Record<string, unknown>;
  retryAfter?: number;
}

export declare class Client {
  constructor(options: { apiKey: string; baseUrl: string; fetch?: typeof fetch });
  me(): Promise<{ key_id: string; shop_id: string; scopes: string[]; rate_limit_per_minute: number; expires_at: string | null }>;
  sendSourceOrder(sourceId: string, externalOrderId: string, payload: OrderPayload, options?: { idempotencyKey?: string }): Promise<Record<string, unknown>>;
  createOrder(order: OrderPayload, options?: { idempotencyKey?: string }): Promise<Record<string, unknown>>;
  getOrder(orderId: string): Promise<Record<string, unknown>>;
  listOrders(options?: { limit?: number; offset?: number }): Promise<{ items: Record<string, unknown>[]; next_offset: number }>;
  setOrderStatus(orderId: string, status: 'CONFIRMED' | 'CANCELLED', options?: { reason?: string; idempotencyKey?: string }): Promise<Record<string, unknown>>;
  findProductsBySku(sku: string): Promise<Record<string, unknown>[]>;
  getInventory(productId: string): Promise<Record<string, unknown>>;
  adjustInventory(productId: string, quantityDelta: number, options: { reason?: 'MANUAL_ADJUSTMENT' | 'OPENING' | 'DAMAGED_WRITE_OFF'; variantId?: string; note?: string; idempotencyKey: string }): Promise<Record<string, unknown>>;
}

export declare function sign(secret: string, rawBody: string | Uint8Array, timestamp: number): string;
export declare function verifyWebhook(secret: string, rawBody: string | Uint8Array, header: string | null | undefined, options?: { tolerance?: number; now?: number }): boolean;

export declare class ReplayGuard {
  constructor(size?: number);
  firstTime(eventId: string): boolean;
}
