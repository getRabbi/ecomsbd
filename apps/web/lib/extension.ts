/**
 * Browser extension (V4 Phase 5): the web side.
 *
 * Settings → Browser extension shows a 6-digit pairing code (`POST
 * /v1/devices/pair`, the same codes a phone claims) and the linked extensions
 * (`GET /v1/devices/extensions`; FEATURE_DISABLED while `extension_enabled` is
 * off). The extension captures with its ingest key and opens
 * `/quick-order?draft=<id>`: that page reads the draft with the seller's own
 * session and opens the normal order composer, so the key never reads shop
 * data or makes an order.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's type
 * stripping.
 */

export interface PairingCode {
  code: string;
  expires_at: string;
}

export interface LinkedExtension {
  connection_id: string;
  name: string | null;
  state: string;
  app_version: string | null;
  last_heartbeat_at: string | null;
  linked_at: string;
}

export interface DraftItem {
  name?: string;
  quantity?: number;
  unit_price_paisa?: number | null;
  size?: string | null;
  color?: string | null;
}

/** The fields of `GET /v1/chat-orders/{id}` this page uses. */
export interface QuickOrderDraft {
  id: string;
  status: string;
  capture_source: string | null;
  customer_name: string | null;
  phones: string[];
  selected_phone: string | null;
  address: string | null;
  items: DraftItem[];
  cod_amount_paisa: number | null;
  notes: string | null;
  missing_fields: string[];
  confirmed_order_id: string | null;
}

/** Same shape as `OrderPrefill` in `lib/orders.ts` (kept structural here). */
export interface DraftPrefill {
  chatDraftId: string;
  phone?: string;
  phones?: string[];
  customer_name?: string;
  address?: string;
  items: { name: string; quantity: number; unit_price_paisa: number | null; variant_label: string | null }[];
  cod_amount_paisa: number | null;
  note?: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The `draft` query value, or null when it is not a draft id. */
export function draftIdFrom(value: string | null | undefined): string | null {
  const id = (value ?? '').trim();
  return UUID.test(id) ? id.toLowerCase() : null;
}

/** Drafts a seller can still turn into an order here. */
export function isOpenDraft(draft: Pick<QuickOrderDraft, 'status' | 'confirmed_order_id'>): boolean {
  return draft.confirmed_order_id === null && ['COLLECTING', 'NEEDS_INFO', 'READY_FOR_REVIEW'].includes(draft.status);
}

/** The composer's prefill for a draft: the same mapping the Inbox review uses. */
export function draftPrefill(draft: QuickOrderDraft): DraftPrefill {
  return {
    chatDraftId: draft.id,
    phone: draft.selected_phone ?? undefined,
    phones: draft.phones.length > 1 ? draft.phones : undefined,
    customer_name: draft.customer_name ?? undefined,
    address: draft.address ?? undefined,
    items: draft.items.map((item) => ({
      name: item.name ?? '',
      quantity: item.quantity ?? 1,
      unit_price_paisa: item.unit_price_paisa ?? null,
      variant_label: [item.size, item.color].filter(Boolean).join(' / ') || null,
    })),
    cod_amount_paisa: draft.cod_amount_paisa,
    note: draft.notes ?? undefined,
  };
}

/** Whole minutes left on a pairing code (0 once it has expired). */
export function minutesLeft(expiresAt: string, now: number = Date.now()): number {
  const left = Date.parse(expiresAt) - now;
  return Number.isFinite(left) && left > 0 ? Math.ceil(left / 60_000) : 0;
}

export const extensionEn = {
  'nav.extension': 'Browser extension',
  'ext.subtitle': 'Capture orders from Messenger and WhatsApp Web in Chrome.',
  'ext.notEnabled': 'The browser extension is not available for your shop yet.',
  'ext.readOnly': 'Only the shop owner can pair an extension.',
  'ext.how': 'How it works',
  'ext.how1': 'Install “ecomsbd order capture” in Chrome on this computer.',
  'ext.how2': 'Open the extension’s side panel and type the 6-digit code below.',
  'ext.how3': 'In a chat, tap Capture. The order opens here for you to check and save.',
  'ext.privacy': 'The extension reads only the chat you have open, and sends it only when you tap Capture. It never reads other chats.',
  'ext.pair': 'Get a pairing code',
  'ext.code': 'Pairing code',
  'ext.codeHint': 'Valid for {minutes} min, one use.',
  'ext.codeExpired': 'This code has expired. Get a new one.',
  'ext.linked': 'Linked extensions',
  'ext.none': 'No extension is linked yet.',
  'ext.lastSeen': 'Last seen {when}',
  'ext.neverSeen': 'Not seen yet',
  'ext.manage': 'Unlink or check health in Connections.',
  'qo.title': 'Quick order',
  'qo.subtitle': 'A captured chat, ready for you to check and save.',
  'qo.badLink': 'This link has no draft in it. Capture the chat again.',
  'qo.notFound': 'This draft was not found in this shop.',
  'qo.closed': 'This chat is already an order or was closed.',
  'qo.openOrder': 'Open the order',
  'qo.missing': 'Still missing: {fields}',
  'qo.review': 'Check and create the order',
  'qo.toInbox': 'Back to Inbox',
} as const;

export type ExtensionKey = keyof typeof extensionEn;

export const extensionBn: Record<ExtensionKey, string> = {
  'nav.extension': 'ব্রাউজার এক্সটেনশন',
  'ext.subtitle': 'Chrome-এ Messenger ও WhatsApp Web থেকে অর্ডার নিন।',
  'ext.notEnabled': 'আপনার শপের জন্য ব্রাউজার এক্সটেনশন এখনো চালু হয়নি।',
  'ext.readOnly': 'শুধু শপের মালিক এক্সটেনশন যুক্ত করতে পারেন।',
  'ext.how': 'কীভাবে কাজ করে',
  'ext.how1': 'এই কম্পিউটারের Chrome-এ “ecomsbd order capture” ইনস্টল করুন।',
  'ext.how2': 'এক্সটেনশনের সাইড প্যানেল খুলে নিচের ৬ সংখ্যার কোড লিখুন।',
  'ext.how3': 'চ্যাটে Capture চাপুন। অর্ডারটি এখানে খুলবে, দেখে সেভ করুন।',
  'ext.privacy': 'এক্সটেনশন শুধু আপনার খোলা চ্যাটটি পড়ে, আর শুধু Capture চাপলেই পাঠায়। অন্য কোনো চ্যাট পড়ে না।',
  'ext.pair': 'পেয়ারিং কোড নিন',
  'ext.code': 'পেয়ারিং কোড',
  'ext.codeHint': '{minutes} মিনিট চলবে, একবারই ব্যবহার করা যাবে।',
  'ext.codeExpired': 'কোডের মেয়াদ শেষ। নতুন কোড নিন।',
  'ext.linked': 'যুক্ত এক্সটেনশন',
  'ext.none': 'এখনো কোনো এক্সটেনশন যুক্ত হয়নি।',
  'ext.lastSeen': 'শেষ দেখা {when}',
  'ext.neverSeen': 'এখনো দেখা যায়নি',
  'ext.manage': 'আনলিংক বা অবস্থা দেখতে Connections-এ যান।',
  'qo.title': 'কুইক অর্ডার',
  'qo.subtitle': 'ক্যাপচার করা চ্যাট, দেখে সেভ করার জন্য তৈরি।',
  'qo.badLink': 'এই লিংকে কোনো ড্রাফট নেই। চ্যাটটি আবার ক্যাপচার করুন।',
  'qo.notFound': 'এই শপে ড্রাফটটি পাওয়া যায়নি।',
  'qo.closed': 'এই চ্যাট থেকে অর্ডার হয়ে গেছে বা এটি বন্ধ।',
  'qo.openOrder': 'অর্ডারটি খুলুন',
  'qo.missing': 'এখনো বাকি: {fields}',
  'qo.review': 'দেখে অর্ডার তৈরি করুন',
  'qo.toInbox': 'ইনবক্সে ফিরুন',
};
