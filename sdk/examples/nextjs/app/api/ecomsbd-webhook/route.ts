// Next.js (App Router) route that receives ecomsbd webhooks.
// Put ECOMSBD_WEBHOOK_SECRET in your server environment. Never expose it to the browser.
import { ReplayGuard, verifyWebhook } from '../../../../../js/ecomsbd.mjs';

const seen = new ReplayGuard(); // Use your database in production.

export async function POST(request: Request): Promise<Response> {
  const raw = await request.text(); // the raw body, before JSON.parse
  const secret = process.env.ECOMSBD_WEBHOOK_SECRET ?? '';
  if (!verifyWebhook(secret, raw, request.headers.get('x-ecomsbd-signature'))) {
    return new Response('invalid signature', { status: 401 });
  }
  const event = JSON.parse(raw) as { id: string; type: string; data: Record<string, unknown> };
  if (!seen.firstTime(event.id)) return new Response('duplicate', { status: 200 });

  switch (event.type) {
    case 'tracking.assigned':
      // save event.data.tracking_code and event.data.provider on your order
      break;
    case 'order.delivered':
    case 'order.returned':
    case 'order.cancelled':
      // update your order's status
      break;
  }
  // Answer 2xx only after the event is stored; ecomsbd retries anything else.
  return new Response('ok', { status: 200 });
}
