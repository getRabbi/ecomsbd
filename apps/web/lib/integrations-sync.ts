import type { CustomSetup, SnippetLang } from '@/lib/integrations';

/** Wire shapes of the V3.2 two-way sync endpoints. */
export type ProductAuthority = 'MANUAL' | 'EXTERNAL' | 'ECOMSBD';
export type InventoryAuthority = 'NONE' | 'ECOMSBD' | 'EXTERNAL';

export interface SyncSettings {
  catalog: boolean;
  products: ProductAuthority;
  inventory: InventoryAuthority;
  order_status: 'OFF' | 'TWO_WAY';
  fulfillment: 'OFF' | 'ON';
  location_id: string | null;
}

export interface SyncView {
  settings: SyncSettings;
  capabilities: Record<string, { enabled: boolean; blocker: string | null }>;
  status_map: Record<string, string | null>;
  links: Record<string, number>;
  open_conflicts: number;
  catalog_synced_at: string | null;
  inventory_synced_at: string | null;
  reconnect_scopes: string[];
}

export interface Link {
  id: string;
  external_product_id: string;
  external_variant_id: string | null;
  external_sku: string | null;
  external_title: string;
  external_price_paisa: number | null;
  external_tracked: boolean;
  external_qty: number | null;
  product_id: string | null;
  variant_id: string | null;
  internal_name: string | null;
  state: 'MATCHED' | 'UNMATCHED' | 'CONFLICT' | 'IGNORED' | 'DELETED';
  match_source: 'SKU' | 'MANUAL' | 'CREATED' | null;
  synced_qty: number | null;
  last_synced_at: string | null;
}

export interface LinkPage {
  items: Link[];
  next_offset: number;
  counts: Record<string, number>;
  open_conflicts: number;
}

export interface Conflict {
  id: string;
  connection_id: string;
  provider: string;
  kind: string;
  entity: 'PRODUCT' | 'INVENTORY' | 'ORDER';
  link_id: string | null;
  order_id: string | null;
  detail: {
    ecomsbd?: Record<string, unknown>;
    external?: Record<string, unknown>;
    last_agreed?: number | null;
    title?: string;
  };
  recommended: string;
  options: string[];
  status: string;
  created_at: string;
  updated_at: string;
}

export const SYNC_STATES = ['CONFIRMED', 'CANCELLED', 'FULFILLED', 'DELIVERED', 'RETURNED'] as const;
export const LINK_STATES = ['MATCHED', 'UNMATCHED', 'CONFLICT', 'IGNORED', 'DELETED'] as const;

// ------------------------------------------------------------- quick start ---

export type SyncPart = 'orderStatus' | 'lookup' | 'stock' | 'events' | 'errors';

export const SYNC_PARTS: { part: SyncPart; label: 'int.dev.part.orderStatus' | 'int.dev.part.lookup' | 'int.dev.part.stock' | 'int.dev.part.events' | 'int.dev.part.errors' }[] = [
  { part: 'orderStatus', label: 'int.dev.part.orderStatus' },
  { part: 'lookup', label: 'int.dev.part.lookup' },
  { part: 'stock', label: 'int.dev.part.stock' },
  { part: 'events', label: 'int.dev.part.events' },
  { part: 'errors', label: 'int.dev.part.errors' },
];

/**
 * Two-way sync examples for a Custom Website, from the same Public API and
 * signed webhooks as the rest of the quick start. Secrets stay in the
 * environment; each write carries an Idempotency-Key.
 */
export function syncSnippet(lang: SnippetLang, part: SyncPart, setup: Pick<CustomSetup, 'api_base_url'>): string {
  const base = setup.api_base_url;
  const php: Record<SyncPart, string> = {
    orderStatus: `<?php
// Confirm or cancel an order your website sent. Same key, same answer.
$ch = curl_init('${base}/orders/' . $ecomsbdOrderId . '/status');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => [
        'Authorization: Bearer ' . getenv('ECOMSBD_API_KEY'),
        'Idempotency-Key: cancel-' . $websiteOrderId,
        'Content-Type: application/json',
    ],
    CURLOPT_POSTFIELDS => json_encode(['status' => 'CANCELLED', 'reason' => 'Customer cancelled']),
]);
$result = json_decode(curl_exec($ch), true);
if (curl_getinfo($ch, CURLINFO_HTTP_CODE) === 409 && ($result['code'] ?? '') === 'CANCELLED_AFTER_BOOKING') {
    // The parcel is already with the courier; the seller decides in ecomsbd.
}`,
    lookup: `<?php
$ch = curl_init('${base}/products?sku=' . rawurlencode($sku));
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . getenv('ECOMSBD_API_KEY')],
]);
$item = json_decode(curl_exec($ch), true)['items'][0] ?? null;
// $item['id'] is the product; a variant SKU is in $item['variants'][n]['id'].`,
    stock: `<?php
// Needs a key with "Let the website change stock". A delta, never a set:
// ecomsbd's stock history records it as a correction.
$ch = curl_init('${base}/inventory/' . $productId . '/adjustments');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => [
        'Authorization: Bearer ' . getenv('ECOMSBD_API_KEY'),
        'Idempotency-Key: stock-' . $countId,
        'Content-Type: application/json',
    ],
    CURLOPT_POSTFIELDS => json_encode([
        'quantity_delta' => -2,
        'variant_id' => $variantId, // null for a product without variants
        'note' => 'Counted on the website',
    ]),
]);
curl_exec($ch);`,
    events: `<?php
// After verifying the signature (see "Verify a webhook"):
switch ($event['type']) {
    case 'order.confirmed':
    case 'order.cancelled':
    case 'order.delivered':
    case 'order.returned':
        markOrder($event['data']['order_id'], $event['type']);
        break;
    case 'tracking.assigned': // courier booked
        saveTracking($event['data']['order_id'], $event['data']['provider'], $event['data']['tracking_code']);
        break;
    case 'inventory.updated':
        setStock($event['data']['sku'], $event['data']['stock_on_hand']);
        break;
}`,
    errors: `<?php
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$body = json_decode($response, true);
if ($status === 409 && $body['code'] === 'IDEMPOTENCY_KEY_CONFLICT') {
    // The same Idempotency-Key was used for a different request: fix the key.
} elseif ($status === 429 || $status >= 500) {
    // Retry later with the SAME Idempotency-Key: it cannot create a second order.
} elseif ($status >= 400) {
    error_log($body['message_en']); // seller-safe text, also in $body['message_bn']
}`,
  };
  const laravel: Record<SyncPart, string> = {
    orderStatus: `$response = Http::withToken(config('services.ecomsbd.key'))
    ->withHeaders(['Idempotency-Key' => 'cancel-' . $order->id])
    ->post('${base}/orders/' . $order->ecomsbd_id . '/status', [
        'status' => 'CANCELLED',
        'reason' => 'Customer cancelled',
    ]);
if ($response->status() === 409 && $response->json('code') === 'CANCELLED_AFTER_BOOKING') {
    // Already with the courier; the seller settles it in ecomsbd.
}`,
    lookup: `$item = Http::withToken(config('services.ecomsbd.key'))
    ->get('${base}/products', ['sku' => $sku])
    ->throw()
    ->json('items.0'); // ['id' => ..., 'variants' => [['id' => ..., 'sku' => ...]]]`,
    stock: `Http::withToken(config('services.ecomsbd.key'))
    ->withHeaders(['Idempotency-Key' => 'stock-' . $count->id])
    ->post('${base}/inventory/' . $productId . '/adjustments', [
        'quantity_delta' => -2,
        'variant_id' => $variantId,
        'note' => 'Counted on the website',
    ])
    ->throw();`,
    events: `match ($event['type']) {
    'order.confirmed', 'order.cancelled', 'order.delivered', 'order.returned'
        => Order::markFromEcomsbd($event['data']['order_id'], $event['type']),
    'tracking.assigned' => Order::saveTracking($event['data']['order_id'], $event['data']['provider'], $event['data']['tracking_code']),
    'inventory.updated' => Stock::set($event['data']['sku'], $event['data']['stock_on_hand']),
    default => null,
};`,
    errors: `try {
    $response->throw();
} catch (\\Illuminate\\Http\\Client\\RequestException $e) {
    $code = $e->response->json('code');
    if ($e->response->status() === 429 || $e->response->serverError()) {
        dispatch(new RetryEcomsbd($payload, $idempotencyKey))->delay(60); // same key
    } elseif ($code === 'IDEMPOTENCY_KEY_CONFLICT') {
        report($e); // one key was reused for a different request
    }
}`,
  };
  const node: Record<SyncPart, string> = {
    orderStatus: `const res = await fetch(\`${base}/orders/\${ecomsbdOrderId}/status\`, {
  method: 'POST',
  headers: {
    Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\`,
    'Idempotency-Key': \`cancel-\${websiteOrderId}\`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ status: 'CANCELLED', reason: 'Customer cancelled' }),
});
const body = await res.json();
if (res.status === 409 && body.code === 'CANCELLED_AFTER_BOOKING') {
  // Already with the courier; the seller settles it in ecomsbd.
}`,
    lookup: `const res = await fetch(\`${base}/products?sku=\${encodeURIComponent(sku)}\`, {
  headers: { Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\` },
});
const [item] = (await res.json()).items; // item.id, item.variants[n].id`,
    stock: `// Needs a key with "Let the website change stock".
await fetch(\`${base}/inventory/\${productId}/adjustments\`, {
  method: 'POST',
  headers: {
    Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\`,
    'Idempotency-Key': \`stock-\${countId}\`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ quantity_delta: -2, variant_id: variantId ?? null, note: 'Counted on the website' }),
});`,
    events: `switch (event.type) {
  case 'order.confirmed':
  case 'order.cancelled':
  case 'order.delivered':
  case 'order.returned':
    await markOrder(event.data.order_id, event.type);
    break;
  case 'tracking.assigned':
    await saveTracking(event.data.order_id, event.data.provider, event.data.tracking_code);
    break;
  case 'inventory.updated':
    await setStock(event.data.sku, event.data.stock_on_hand);
    break;
}`,
    errors: `if (!res.ok) {
  const body = await res.json().catch(() => ({}));
  if (res.status === 429 || res.status >= 500) {
    retryLater(request, idempotencyKey); // the same key cannot duplicate anything
  } else if (body.code === 'IDEMPOTENCY_KEY_CONFLICT') {
    throw new Error('One Idempotency-Key was reused for a different request');
  } else {
    console.warn(body.message_en); // seller-safe; body.message_bn in Bangla
  }
}`,
  };
  const python: Record<SyncPart, string> = {
    orderStatus: `response = requests.post(
    f"${base}/orders/{ecomsbd_order_id}/status",
    headers={
        "Authorization": f"Bearer {os.environ['ECOMSBD_API_KEY']}",
        "Idempotency-Key": f"cancel-{website_order_id}",
    },
    json={"status": "CANCELLED", "reason": "Customer cancelled"},
    timeout=15,
)
if response.status_code == 409 and response.json().get("code") == "CANCELLED_AFTER_BOOKING":
    pass  # already with the courier; the seller settles it in ecomsbd`,
    lookup: `response = requests.get(
    "${base}/products",
    params={"sku": sku},
    headers={"Authorization": f"Bearer {os.environ['ECOMSBD_API_KEY']}"},
    timeout=15,
)
item = response.json()["items"][0]  # item["id"], item["variants"][n]["id"]`,
    stock: `# Needs a key with "Let the website change stock".
requests.post(
    f"${base}/inventory/{product_id}/adjustments",
    headers={
        "Authorization": f"Bearer {os.environ['ECOMSBD_API_KEY']}",
        "Idempotency-Key": f"stock-{count_id}",
    },
    json={"quantity_delta": -2, "variant_id": variant_id, "note": "Counted on the website"},
    timeout=15,
).raise_for_status()`,
    events: `kind, data = event["type"], event["data"]
if kind in {"order.confirmed", "order.cancelled", "order.delivered", "order.returned"}:
    mark_order(data["order_id"], kind)
elif kind == "tracking.assigned":
    save_tracking(data["order_id"], data["provider"], data["tracking_code"])
elif kind == "inventory.updated":
    set_stock(data["sku"], data["stock_on_hand"])`,
    errors: `if response.status_code == 429 or response.status_code >= 500:
    retry_later(payload, idempotency_key)  # same key, so never a second order
elif response.status_code == 409 and response.json().get("code") == "IDEMPOTENCY_KEY_CONFLICT":
    raise RuntimeError("One Idempotency-Key was reused for a different request")
elif not response.ok:
    log.warning(response.json().get("message_en"))  # seller-safe text`,
  };
  const next: Record<SyncPart, string> = {
    orderStatus: `'use server';

export async function cancelOnEcomsbd(ecomsbdOrderId: string, websiteOrderId: string) {
  const res = await fetch(\`${base}/orders/\${ecomsbdOrderId}/status\`, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\`,
      'Idempotency-Key': \`cancel-\${websiteOrderId}\`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ status: 'CANCELLED' }),
  });
  const body = await res.json();
  return res.status === 409 ? { blocked: body.code } : body;
}`,
    lookup: `const res = await fetch(\`${base}/products?sku=\${encodeURIComponent(sku)}\`, {
  cache: 'no-store',
  headers: { Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\` },
});
const [item] = (await res.json()).items;`,
    stock: `'use server';

export async function reportStock(productId: string, variantId: string | null, delta: number, countId: string) {
  await fetch(\`${base}/inventory/\${productId}/adjustments\`, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\`,
      'Idempotency-Key': \`stock-\${countId}\`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ quantity_delta: delta, variant_id: variantId, note: 'Counted on the website' }),
  });
}`,
    events: `// Inside app/api/ecomsbd/webhook/route.ts, after the signature check:
const event = JSON.parse(raw);
if (event.type === 'tracking.assigned') {
  await db.order.update({ where: { ecomsbdId: event.data.order_id }, data: { tracking: event.data.tracking_code } });
} else if (event.type === 'inventory.updated') {
  await db.product.update({ where: { sku: event.data.sku }, data: { stock: event.data.stock_on_hand } });
} else if (event.type.startsWith('order.')) {
  await db.order.update({ where: { ecomsbdId: event.data.order_id }, data: { status: event.type } });
}`,
    errors: `if (!res.ok) {
  const body = await res.json().catch(() => ({}));
  if (res.status === 429 || res.status >= 500) throw new Error('retry'); // retry with the same Idempotency-Key
  if (body.code === 'IDEMPOTENCY_KEY_CONFLICT') throw new Error('Idempotency-Key reused for a different request');
  return { error: body.message_en as string };
}`,
  };
  return { php, laravel, node, python, next }[lang][part];
}
