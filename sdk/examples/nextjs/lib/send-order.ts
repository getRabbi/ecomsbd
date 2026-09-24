// Server-only helper for a Next.js checkout: send the order to ecomsbd.
// ECOMSBD_API_KEY, ECOMSBD_API_BASE (https://YOUR-API-HOST/public/v1) and
// ECOMSBD_SOURCE_ID come from Integrations → Custom Website. Keep them server-side.
import { Client, EcomsbdError } from '../../../js/ecomsbd.mjs';

const client = new Client({
  apiKey: process.env.ECOMSBD_API_KEY ?? '',
  baseUrl: process.env.ECOMSBD_API_BASE ?? '',
});

export async function sendOrder(checkout: {
  orderNumber: string;
  phone: string;
  name: string;
  address: string;
  district: string;
  lines: { sku: string; title: string; quantity: number; pricePaisa: number }[];
  codPaisa: number;
}) {
  try {
    // The website's own order number is the idempotency identity: a retry never duplicates.
    return await client.sendSourceOrder(process.env.ECOMSBD_SOURCE_ID ?? '', checkout.orderNumber, {
      phone: checkout.phone,
      customer_name: checkout.name,
      address: checkout.address,
      district: checkout.district,
      items: checkout.lines.map((line) => ({ name: `${line.title} (${line.sku})`, quantity: line.quantity, unit_price_paisa: line.pricePaisa })),
      cod_amount_paisa: checkout.codPaisa,
    });
  } catch (error) {
    if (error instanceof EcomsbdError && error.status === 429) {
      // back off for error.retryAfter seconds, then retry with the same order number
    }
    throw error;
  }
}
