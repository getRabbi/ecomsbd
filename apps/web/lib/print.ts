/**
 * Print an invoice or label the API rendered (V4 WP0.5).
 *
 * The HTML goes into a hidden, sandboxed iframe (no scripts) and the browser's
 * print dialog opens on it, so there is no pop-up to block and nothing is left
 * behind: the frame is removed after printing.
 */
import { fetchHtml } from './api';

export type LabelSize = '4x6' | '3x4' | 'a4-8up';
export const LABEL_SIZES: readonly LabelSize[] = ['4x6', '3x4', 'a4-8up'];
/** The API's bulk limit. */
export const MAX_BULK_PRINT = 100;

export function printHtml(html: string): Promise<void> {
  return new Promise((resolve) => {
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-modals allow-same-origin');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    frame.onload = () => {
      const view = frame.contentWindow;
      const cleanup = () => {
        frame.remove();
        resolve();
      };
      if (!view) {
        cleanup();
        return;
      }
      view.addEventListener('afterprint', cleanup, { once: true });
      view.focus();
      view.print();
      // Some browsers never fire afterprint for a frame; do not leak it.
      window.setTimeout(cleanup, 60_000);
    };
    frame.srcdoc = html;
    document.body.appendChild(frame);
  });
}

export async function printInvoice(orderId: string): Promise<void> {
  await printHtml(await fetchHtml(`/orders/${orderId}/documents/invoice`));
}

export async function printLabel(orderId: string, size: LabelSize, maskPhone: boolean): Promise<void> {
  await printHtml(
    await fetchHtml(`/orders/${orderId}/documents/label`, { query: { size, mask_phone: maskPhone } }),
  );
}

export async function printBulk(
  orderIds: string[],
  kind: 'invoice' | 'label',
  size: LabelSize = '4x6',
  maskPhone = false,
): Promise<void> {
  await printHtml(
    await fetchHtml('/orders/documents/bulk', {
      method: 'POST',
      body: { order_ids: orderIds.slice(0, MAX_BULK_PRINT), kind, size, mask_phone: maskPhone },
    }),
  );
}
