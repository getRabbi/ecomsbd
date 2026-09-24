# ecomsbd SDKs and examples

These are small single-file helpers for the [Public API](../docs/PUBLIC_API.md). To use one,
copy the file into your project; there is nothing to install. All values here are
placeholders, so use your own key, source ID and signing secret, and keep them on your server.

| Language | File | Test |
| --- | --- | --- |
| JavaScript / TypeScript (Node 18+) | `js/ecomsbd.mjs`, `js/ecomsbd.d.ts` | `node --test sdk/js/ecomsbd.test.mjs` |
| PHP 8 (curl) | `php/Ecomsbd.php` | `php sdk/php/selftest.php` |
| Python 3.10+ | `python/ecomsbd.py` | `backend/tests/test_developer_platform.py` |

Each one has the same calls:

- `me`
- send a Custom Website order (the external order ID is the idempotency identity)
- get and list orders
- confirm or cancel an order
- find products by SKU
- read inventory, and adjust it (needs `inventory:write`)
- verify a webhook, with replay rejection

## Quick start (Custom Website)

1. In ecomsbd, go to **Integrations → Custom Website → Create connection**. Copy the API
   key, which is shown once, and the source ID.
2. Send an order:

   ```js
   import { Client } from './ecomsbd.mjs';
   const ecomsbd = new Client({ apiKey: process.env.ECOMSBD_API_KEY, baseUrl: process.env.ECOMSBD_API_BASE });
   await ecomsbd.sendSourceOrder(process.env.ECOMSBD_SOURCE_ID, 'WEB-1001', {
     phone: '01712345678', customer_name: 'Rahim', address: 'House 1, Road 2', district: 'Dhaka',
     items: [{ name: 'Black Abaya XL', quantity: 1, unit_price_paisa: 125000 }],
     cod_amount_paisa: 125000,
   });
   ```

3. Add a webhook URL on the connection, save its signing secret, and verify each delivery
   with `verifyWebhook(secret, rawBody, signatureHeader)`.
4. Use **Send test order** (nothing is created) and **Send test webhook**, then **Go live**.

## Examples

- `examples/nextjs/app/api/ecomsbd-webhook/route.ts`: a webhook receiver (App Router).
- `examples/nextjs/lib/send-order.ts`: sends a checkout from the server.
- `examples/laravel/EcomsbdWebhookController.php`: a webhook receiver.
- `examples/laravel/SendOrderToEcomsbd.php`: a queued job that sends an order and honours
  `429 Retry-After`.
