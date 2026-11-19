/**
 * The customer's confirm page (V4 WP2.3): types, copy and formatting.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's
 * type stripping. The page is for the shop's customer, not the seller, so the
 * copy is plain and Bangla comes first.
 */

export type LinkStatus = 'OPEN' | 'ANSWERED' | 'UNAVAILABLE';

export interface LinkItem {
  name: string;
  variant: string | null;
  quantity: number;
}

/** `GET/POST /v1/public/confirmations/{token}`: one shape for every token. */
export interface LinkView {
  status: LinkStatus;
  result: 'CONFIRM' | 'CANCEL' | null;
  shop_name: string | null;
  items: LinkItem[];
  item_count: number;
  cod_amount_paisa: number | null;
  phone_masked: string | null;
  expires_at: string | null;
}

export type ConfirmLocale = 'bn' | 'en';

export const confirmEn = {
  'cl.title': 'Confirm your order',
  'cl.from': 'Order from {shop}',
  'cl.items': 'Items',
  'cl.more': '+{count} more',
  'cl.cod': 'Cash on delivery',
  'cl.phone': 'Phone',
  'cl.question': 'Do you want this order?',
  'cl.confirm': 'Yes, confirm',
  'cl.cancel': 'No, cancel',
  'cl.sending': 'Sending…',
  'cl.confirmed': 'Thank you. Your order is confirmed.',
  'cl.cancelled': 'Your order is cancelled.',
  'cl.unavailable': 'This link is no longer active. Please contact the shop.',
  'cl.loading': 'Loading…',
  'cl.error': 'Something went wrong. Please try again.',
  'cl.busy': 'Too many tries. Please wait a minute and try again.',
  'cl.language': 'বাংলা',
} as const;

export type ConfirmKey = keyof typeof confirmEn;

export const confirmBn: Record<ConfirmKey, string> = {
  'cl.title': 'অর্ডার কনফার্ম করুন',
  'cl.from': '{shop} থেকে অর্ডার',
  'cl.items': 'পণ্য',
  'cl.more': 'আরো {count}টি',
  'cl.cod': 'ক্যাশ অন ডেলিভারি',
  'cl.phone': 'ফোন',
  'cl.question': 'আপনি কি অর্ডারটি নিতে চান?',
  'cl.confirm': 'হ্যাঁ, কনফার্ম',
  'cl.cancel': 'না, বাতিল',
  'cl.sending': 'পাঠানো হচ্ছে…',
  'cl.confirmed': 'ধন্যবাদ। আপনার অর্ডার কনফার্ম হয়েছে।',
  'cl.cancelled': 'আপনার অর্ডার বাতিল হয়েছে।',
  'cl.unavailable': 'এই লিংকটি আর চালু নেই। দোকানের সাথে যোগাযোগ করুন।',
  'cl.loading': 'লোড হচ্ছে…',
  'cl.error': 'কিছু একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।',
  'cl.busy': 'অনেকবার চেষ্টা হয়েছে। এক মিনিট পর আবার চেষ্টা করুন।',
  'cl.language': 'English',
};

export function confirmText(
  locale: ConfirmLocale,
  key: ConfirmKey,
  params: Record<string, string | number> = {},
): string {
  const template = locale === 'bn' ? confirmBn[key] : confirmEn[key];
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

/** `125000` → `৳1,250`; `125050` → `৳1,250.50`. Integer paisa only. */
export function formatCod(paisa: number): string {
  const taka = Math.trunc(paisa / 100);
  const rest = Math.abs(paisa % 100);
  const whole = taka.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return rest === 0 ? `৳${whole}` : `৳${whole}.${rest.toString().padStart(2, '0')}`;
}

export function itemLine(item: LinkItem): string {
  const label = item.variant ? `${item.name} (${item.variant})` : item.name;
  return `${label} × ${item.quantity}`;
}

/** What the page says once the link is not open any more. */
export function closedMessage(view: LinkView): ConfirmKey {
  if (view.status === 'ANSWERED') {
    return view.result === 'CANCEL' ? 'cl.cancelled' : 'cl.confirmed';
  }
  return 'cl.unavailable';
}

/** The token as the SMS carries it: 22 url-safe characters. */
export function isToken(value: string): boolean {
  return /^[A-Za-z0-9_-]{22}$/.test(value);
}
