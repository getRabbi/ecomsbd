import type { Tone } from '@/components/ui';

/** Wire shapes of /v1/integrations. Credentials never appear in any of them. */
export type Provider = 'SHOPIFY' | 'WOOCOMMERCE' | 'CUSTOM_WEBSITE' | 'MESSENGER';

export const PROVIDERS: Provider[] = ['SHOPIFY', 'WOOCOMMERCE', 'CUSTOM_WEBSITE', 'MESSENGER'];

export interface Availability {
  provider: Provider;
  available: boolean;
  blocker: string | null;
  one_click?: boolean;
}

export interface Connection {
  id: string;
  provider: Provider;
  name: string;
  state: string;
  health: string;
  account_name: string | null;
  account_id?: string | null;
  webhook_state: string | null;
  sync_state: string | null;
  last_success_at: string | null;
  last_webhook_at: string | null;
  last_sync_at: string | null;
  last_error_at: string | null;
  last_error_code: string | null;
  created_at: string;
  open_issues: number;
  open_conflicts: number;
  orders_today: number;
  failed_today: number;
  import_from?: string | null;
  pages?: { id: string; name: string }[] | null;
}

export interface Hub {
  providers: Availability[];
  items: Connection[];
  can_manage: boolean;
  can_retry: boolean;
}

export interface IntegrationEvent {
  id: string;
  connection_id: string;
  provider: Provider;
  kind: string;
  operation: string | null;
  topic: string;
  external_ref: string | null;
  status: string;
  code: string | null;
  attempts: number;
  order_id: string | null;
  retryable: boolean;
  action: 'RETRY' | 'RECONNECT' | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface SyncRun {
  id: string;
  kind: 'INITIAL' | 'INCREMENTAL' | 'CATALOG';
  status: string;
  since: string;
  until: string;
  pages: number;
  imported: number;
  duplicates: number;
  skipped: number;
  failed: number;
  total: number | null;
  last_error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface CustomSetup {
  api_base_url: string;
  orders_endpoint: string;
  source_id: string;
  scopes: string[];
  topics: string[];
  key_active: boolean;
  key_created_at: string | null;
  last_api_call_at: string | null;
  last_order_at: string | null;
  last_order_id: string | null;
  webhook_url: string | null;
  webhook_enabled: boolean;
  webhook_topics: string[];
  deliveries: { failed_24h: number; last_status: string | null; last_at: string | null };
}

export interface Detail {
  connection: Connection;
  runs: SyncRun[];
  events: IntegrationEvent[];
  availability: Availability;
  can_manage: boolean;
  can_retry: boolean;
  custom?: CustomSetup;
}

export interface Recipe {
  key: string;
  name: { en: string; bn: string };
  trigger: string;
  action: string;
  installed: boolean;
  available: boolean;
  blocker: string | null;
}

export interface TestResult {
  ok: boolean;
  checks: { key: string; ok: boolean; code?: string }[];
  health: string;
}

/** Shopify serves 60 days without a protected scope; WooCommerce is capped by us. */
export const MAX_DAYS: Partial<Record<Provider, number>> = { SHOPIFY: 60, WOOCOMMERCE: 365 };

export function healthTone(health: string): Tone {
  if (health === 'CONNECTED') return 'good';
  if (['DEGRADED', 'SETUP_INCOMPLETE', 'DISABLED', 'OFFICIAL_SETUP_REQUIRED'].includes(health)) {
    return 'warn';
  }
  if (health === 'NOT_CONNECTED') return 'neutral';
  return 'bad';
}

export function statusTone(status: string): Tone {
  if (status === 'PROCESSED' || status === 'COMPLETED' || status === 'RESOLVED') return 'good';
  if (status === 'FAILED') return 'bad';
  if (status === 'QUEUED' || status === 'RUNNING') return 'warn';
  return 'neutral';
}

// ------------------------------------------------------------ quick start ---

export type SnippetLang = 'php' | 'laravel' | 'node' | 'python' | 'next';
export type SnippetPart = 'create' | 'verify' | 'status';

export const SNIPPET_LANGS: { id: SnippetLang; label: string }[] = [
  { id: 'php', label: 'PHP' },
  { id: 'laravel', label: 'Laravel' },
  { id: 'node', label: 'Node.js / TypeScript' },
  { id: 'python', label: 'Python' },
  { id: 'next', label: 'Next.js' },
];

/**
 * Ready-to-paste examples built from this connection's identifiers.
 *
 * The API key and webhook secret are never inlined: each example reads them
 * from the environment, because a snippet gets pasted into chats and repos.
 */
export function snippet(lang: SnippetLang, part: SnippetPart, setup: Pick<CustomSetup, 'api_base_url' | 'orders_endpoint'>): string {
  const base = setup.api_base_url;
  const orders = setup.orders_endpoint;
  const body = {
    php: `[
    'external_order_id' => (string) $websiteOrderId,
    'payload' => [
        'phone' => '01712345678',
        'customer_name' => 'Customer name',
        'address' => 'House, road, area',
        'district' => 'Dhaka',
        'items' => [['name' => 'T-shirt', 'quantity' => 1, 'unit_price_paisa' => 50000]],
        'delivery_fee_paisa' => 6000,
    ],
]`,
    js: `{
    external_order_id: String(order.id),
    payload: {
      phone: order.phone,
      customer_name: order.name,
      address: order.address,
      district: order.district,
      items: order.items.map((i) => ({ name: i.name, quantity: i.quantity, unit_price_paisa: i.pricePaisa })),
      delivery_fee_paisa: order.deliveryFeePaisa,
    },
  }`,
  };
  const table: Record<SnippetLang, Record<SnippetPart, string>> = {
    php: {
      create: `<?php
// Server side only. The key never goes to the browser.
$apiKey = getenv('ECOMSBD_API_KEY');
$ch = curl_init('${orders}');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => [
        'Authorization: Bearer ' . $apiKey,
        // One key per website order: a retry returns the same ecomsbd order.
        'Idempotency-Key: order-' . $websiteOrderId,
        'Content-Type: application/json',
    ],
    CURLOPT_POSTFIELDS => json_encode(${body.php}),
]);
$result = json_decode(curl_exec($ch), true); // ['order_id' => ..., 'replayed' => false]`,
      verify: `<?php
$raw = file_get_contents('php://input');
parse_str(str_replace(',', '&', $_SERVER['HTTP_X_ECOMSBD_SIGNATURE'] ?? ''), $sig);
$expected = hash_hmac('sha256', ($sig['t'] ?? '') . '.' . $raw, getenv('ECOMSBD_WEBHOOK_SECRET'));
if (!isset($sig['t'], $sig['v1']) || abs(time() - (int) $sig['t']) > 300
    || !hash_equals($expected, $sig['v1'])) {
    http_response_code(401);
    exit;
}
$eventId = $_SERVER['HTTP_X_ECOMSBD_EVENT_ID']; // store it; skip events you have seen
$event = json_decode($raw, true);                // ['type' => 'order.booked', 'data' => [...]]
http_response_code(200);`,
      status: `<?php
$ch = curl_init('${base}/orders/' . $ecomsbdOrderId);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . getenv('ECOMSBD_API_KEY')],
]);
$status = json_decode(curl_exec($ch), true)['status']; // DRAFT, CONFIRMED, ...`,
    },
    laravel: {
      create: `use Illuminate\\Support\\Facades\\Http;

// config/services.php: 'ecomsbd' => ['key' => env('ECOMSBD_API_KEY'), 'webhook_secret' => env('ECOMSBD_WEBHOOK_SECRET')]
$result = Http::withToken(config('services.ecomsbd.key'))
    ->withHeaders(['Idempotency-Key' => 'order-' . $websiteOrderId])
    ->post('${orders}', ${body.php})
    ->throw()
    ->json(); // ['order_id' => ..., 'replayed' => false]`,
      verify: `// routes/api.php
use Illuminate\\Http\\Request;

Route::post('/ecomsbd/webhook', function (Request $request) {
    parse_str(str_replace(',', '&', $request->header('X-Ecomsbd-Signature', '')), $sig);
    $expected = hash_hmac('sha256', ($sig['t'] ?? '') . '.' . $request->getContent(),
        config('services.ecomsbd.webhook_secret'));
    abort_unless(isset($sig['t'], $sig['v1']) && abs(time() - (int) $sig['t']) <= 300
        && hash_equals($expected, $sig['v1']), 401);
    // Skip X-Ecomsbd-Event-Id values you have already handled.
    return response()->noContent();
});`,
      status: `$status = Http::withToken(config('services.ecomsbd.key'))
    ->get('${base}/orders/' . $ecomsbdOrderId)
    ->throw()
    ->json('status');`,
    },
    node: {
      create: `const API_KEY = process.env.ECOMSBD_API_KEY!; // server only

export async function sendOrder(order: WebsiteOrder) {
  const res = await fetch('${orders}', {
    method: 'POST',
    headers: {
      Authorization: \`Bearer \${API_KEY}\`,
      'Idempotency-Key': \`order-\${order.id}\`, // a retry returns the same ecomsbd order
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(${body.js}),
  });
  if (!res.ok) throw new Error(\`ecomsbd \${res.status}\`);
  return (await res.json()) as { order_id: string; replayed: boolean };
}`,
      verify: `import { createHmac, timingSafeEqual } from 'node:crypto';

/** rawBody is the exact request body string, before JSON.parse. */
export function verifyEcomsbd(rawBody: string, header: string, secret = process.env.ECOMSBD_WEBHOOK_SECRET!) {
  const parts = Object.fromEntries(header.split(',').map((p) => p.split('=') as [string, string]));
  const t = Number(parts.t);
  if (!t || !parts.v1 || Math.abs(Date.now() / 1000 - t) > 300) return false;
  const expected = createHmac('sha256', secret).update(\`\${t}.\${rawBody}\`).digest('hex');
  return expected.length === parts.v1.length && timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}
// Then skip any X-Ecomsbd-Event-Id you have already handled.`,
      status: `export async function ecomsbdStatus(orderId: string) {
  const res = await fetch(\`${base}/orders/\${orderId}\`, {
    headers: { Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\` },
  });
  return ((await res.json()) as { status: string }).status;
}`,
    },
    python: {
      create: `import os
import requests

API_KEY = os.environ["ECOMSBD_API_KEY"]  # server only

def send_order(website_order_id: str, payload: dict) -> dict:
    response = requests.post(
        "${orders}",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            # A retry with the same key returns the same ecomsbd order.
            "Idempotency-Key": f"order-{website_order_id}",
        },
        json={"external_order_id": website_order_id, "payload": payload},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()  # {"order_id": ..., "replayed": False}`,
      verify: `import hashlib
import hmac
import os
import time

def verify_ecomsbd(raw_body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or abs(time.time() - int(t)) > 300:
        return False
    secret = os.environ["ECOMSBD_WEBHOOK_SECRET"].encode()
    expected = hmac.new(secret, t.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
# Then skip any X-Ecomsbd-Event-Id you have already handled.`,
      status: `def order_status(order_id: str) -> str:
    response = requests.get(
        f"${base}/orders/{order_id}",
        headers={"Authorization": f"Bearer {os.environ['ECOMSBD_API_KEY']}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["status"]`,
    },
    next: {
      create: `// app/checkout/actions.ts: a Server Action, so the key stays on the server
'use server';

export async function sendToEcomsbd(order: WebsiteOrder) {
  const res = await fetch('${orders}', {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\`,
      'Idempotency-Key': \`order-\${order.id}\`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(${body.js}),
  });
  if (!res.ok) throw new Error(\`ecomsbd \${res.status}\`);
  return res.json();
}`,
      verify: `// app/api/ecomsbd/webhook/route.ts
import { createHmac, timingSafeEqual } from 'node:crypto';

export async function POST(request: Request) {
  const raw = await request.text(); // verify the raw text, not re-serialized JSON
  const header = request.headers.get('x-ecomsbd-signature') ?? '';
  const parts = Object.fromEntries(header.split(',').map((p) => p.split('=') as [string, string]));
  const t = Number(parts.t);
  const expected = createHmac('sha256', process.env.ECOMSBD_WEBHOOK_SECRET!).update(\`\${t}.\${raw}\`).digest('hex');
  const ok = !!t && Math.abs(Date.now() / 1000 - t) <= 300 && parts.v1?.length === expected.length
    && timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
  if (!ok) return new Response('invalid signature', { status: 401 });
  // Skip request.headers.get('x-ecomsbd-event-id') values you have already handled.
  return new Response(null, { status: 204 });
}`,
      status: `const res = await fetch(\`${base}/orders/\${orderId}\`, {
  cache: 'no-store',
  headers: { Authorization: \`Bearer \${process.env.ECOMSBD_API_KEY}\` },
});
const { status } = (await res.json()) as { status: string };`,
    },
  };
  return table[lang][part];
}
