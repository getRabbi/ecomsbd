/**
 * Autopilot (V4 Phase 4): types, copy and the few choices the pages make.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's type
 * stripping. The server decides everything that matters: turning on needs
 * every checklist line (`409 AUTOPILOT_NOT_READY`), each run re-checks them,
 * and a held chat order stays with the seller with its reason code. This file
 * only labels those codes and picks which buttons to show.
 */

export interface CheckLine {
  key: 'PLAN' | 'CONFIRMATIONS' | 'COURIER' | 'DELIVERY_RULES' | 'VOICE' | 'HISTORY' | 'CONSENT';
  ok: boolean;
  reason: string;
  detail: Record<string, unknown>;
}

export interface AutopilotStatus {
  on: boolean;
  consent_at: string | null;
  paused_until: string | null;
  manual_queue: boolean;
  daily_cap: number | null;
  ready: boolean;
  checklist: CheckLine[];
  /** Why it is not acting right now (a switch or a failing line), or null. */
  reason: string | null;
  workflow: { installed: boolean; enabled: boolean };
  /** The default courier Autopilot uses, and the shop's best three by its own
   * delivered rate (a suggestion only; the default is always used). */
  courier_comparison: CourierComparison;
}

export interface CourierOption {
  courier: string;
  delivered: number;
  parcels: number;
  is_default: boolean;
}

export interface CourierComparison {
  default: string | null;
  days: number;
  options: CourierOption[];
}

/** Whole percent, rounded down so a rate never reads better than it is. */
export function deliveredPercent(option: Pick<CourierOption, 'delivered' | 'parcels'>): number {
  return option.parcels > 0 ? Math.floor((option.delivered * 100) / option.parcels) : 0;
}

/** A courier key as a name (`steadfast` -> `Steadfast`, `redx` -> `RedX`). */
export function courierName(key: string): string {
  const names: Record<string, string> = { steadfast: 'Steadfast', pathao: 'Pathao', redx: 'RedX' };
  return names[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

export interface AutopilotDecision {
  draft_id: string;
  outcome: 'CONVERTED' | 'HELD';
  reason: string | null;
  order_id: string | null;
  order_number: string | null;
  at: string;
}

export interface AutopilotToday {
  date: string;
  converted: number;
  confirmed: number;
  cancelled: number;
  waiting: number;
  held: number;
  recent: AutopilotDecision[];
}

export interface HomeCardView {
  /** Show the card at all: Autopilot is on, or it decided something. */
  show: boolean;
  paused: boolean;
  canPause: boolean;
  canResume: boolean;
}

/** `can`: the viewer's permission check. */
export function homeCard(
  status: AutopilotStatus,
  today: AutopilotToday | null,
  can: (permission: string) => boolean,
): HomeCardView {
  const paused = status.paused_until !== null;
  return {
    show: status.on || (today?.recent.length ?? 0) > 0,
    paused,
    canPause: status.on && !paused && can('order.write'),
    canResume: paused && can('settings.manage'),
  };
}

/** Whether the seller may press "Turn on": every line but consent passes and
 * consent is ticked now (or was recorded before). */
export function canTurnOn(status: AutopilotStatus, consentTicked: boolean): boolean {
  if (status.on) return false;
  return status.checklist.every(
    (line) => line.ok || (line.key === 'CONSENT' && consentTicked),
  );
}

const REASONS = [
  'AUTOPILOT_DISABLED',
  'SHOP_INACTIVE',
  'AUTOPILOT_OFF',
  'AUTOPILOT_PAUSED',
  'NOT_IN_PLAN',
  'CONFIRMATION_ENGINE_OFF',
  'COURIER_NOT_READY',
  'DELIVERY_RULES_INCOMPLETE',
  'VOICE_UNAVAILABLE',
  'NOT_ENOUGH_CONFIRMED',
  'CONSENT_MISSING',
  'DRAFT_NOT_READY',
  'FIELDS_UNCERTAIN',
  'PHONE_NOT_SINGLE',
  'ITEM_NOT_MATCHED',
  'COD_NOT_CALCULATED',
  'ZONE_UNRESOLVED',
  'LOCATION_UNRESOLVED',
  'COD_MISMATCH',
  'OUT_OF_STOCK',
  'DUPLICATE_RECENT',
  'POLICY_SELLER_REVIEW',
  'DAILY_CAP_REACHED',
  'ORDER_REFUSED',
] as const;

export const AUTOPILOT_REASONS: readonly string[] = REASONS;

const COURIER_PROBLEMS = ['NO_DEFAULT_COURIER', 'PROVIDER_OFF', 'NOT_LIVE_VERIFIED', 'NOT_CONNECTED', 'UNHEALTHY'] as const;

export const AUTOPILOT_COURIER_PROBLEMS: readonly string[] = COURIER_PROBLEMS;

/** The copy key for a reason code; an unknown code reads as "held". */
export function reasonKey(code: string | null): AutopilotKey {
  return (REASONS as readonly string[]).includes(code ?? '') ? (`ap.r.${code}` as AutopilotKey) : 'ap.r.unknown';
}

export function courierProblemKey(code: unknown): AutopilotKey {
  return (COURIER_PROBLEMS as readonly string[]).includes(String(code)) ? (`ap.c.${String(code)}` as AutopilotKey) : 'ap.r.COURIER_NOT_READY';
}

export const autopilotEn = {
  'nav.autopilot': 'Autopilot',
  'ap.subtitle': 'Chat orders that are complete become orders on their own and go to the customer for confirmation.',
  'ap.notEnabled': 'Autopilot is not available for your shop yet.',
  'ap.readOnly': 'Only the shop owner or a manager can change this.',
  'ap.what': 'What Autopilot does',
  'ap.what1': 'A complete chat order (one phone number, every item found and in stock, a known area, the price your delivery charges give) becomes an order.',
  'ap.what2': 'The customer is then asked to confirm, by call or by you. A courier is booked only after the customer confirms, by your "Customer confirmed → book courier" workflow.',
  'ap.what3': 'Anything unclear stays in Chat orders for you, with the reason. Your linked phone may send order messages without you tapping Create.',
  'ap.checklist': 'Before it can run',
  'ap.checklistHint': 'Checked again before every order.',
  'ap.l.PLAN': 'Your plan includes Autopilot',
  'ap.l.CONFIRMATIONS': 'Customer confirmation is on',
  'ap.l.COURIER': 'Your default courier is connected and verified',
  'ap.l.DELIVERY_RULES': 'Delivery charges for every zone and a default courier',
  'ap.l.VOICE': 'Confirmation calls are ready, or you call customers yourself',
  'ap.l.HISTORY': 'At least {needed} orders confirmed ({confirmed} so far)',
  'ap.l.CONSENT': 'You agreed to what Autopilot does',
  'ap.manualQueue': 'I will call customers myself from the call queue while confirmation calls are off',
  'ap.consent': 'I have read what Autopilot does and want it to make orders for my shop',
  'ap.turnOn': 'Turn on Autopilot',
  'ap.turnOff': 'Turn off Autopilot',
  'ap.on': 'Autopilot is on.',
  'ap.off': 'Autopilot is off.',
  'ap.pausedUntil': 'Paused until {time}.',
  'ap.dailyCap': 'At most {cap} orders a day.',
  'ap.workflowOff': 'The Autopilot workflow is not on. Install it in Automation and turn it on.',
  'ap.workflowLink': 'Open Automation',
  'ap.home': 'Autopilot today',
  'ap.made': 'Orders made',
  'ap.confirmed': 'Confirmed',
  'ap.cancelled': 'Cancelled',
  'ap.waiting': 'Waiting',
  'ap.recent': 'Last decisions',
  'ap.none': 'Nothing yet today.',
  'ap.madeOrder': 'Order {number} made',
  'ap.heldFor': 'Left for you: {reason}',
  'ap.pause': 'Pause 1 hour',
  'ap.resume': 'Resume now',
  'ap.pausedNote': 'Paused. No order is made and queued calls wait; nothing is cancelled.',
  'ap.r.AUTOPILOT_DISABLED': 'Autopilot is switched off by ecomsbd',
  'ap.r.SHOP_INACTIVE': 'the shop is not active',
  'ap.r.AUTOPILOT_OFF': 'Autopilot is off',
  'ap.r.AUTOPILOT_PAUSED': 'Autopilot was paused',
  'ap.r.NOT_IN_PLAN': 'your plan does not include Autopilot',
  'ap.r.CONFIRMATION_ENGINE_OFF': 'customer confirmation is off',
  'ap.r.COURIER_NOT_READY': 'the default courier is not ready',
  'ap.r.DELIVERY_RULES_INCOMPLETE': 'delivery charges are not complete',
  'ap.r.VOICE_UNAVAILABLE': 'confirmation calls are off',
  'ap.r.NOT_ENOUGH_CONFIRMED': 'not enough confirmed orders yet',
  'ap.r.CONSENT_MISSING': 'Autopilot was not agreed to',
  'ap.r.DRAFT_NOT_READY': 'the chat order is not complete',
  'ap.r.FIELDS_UNCERTAIN': 'some details are uncertain',
  'ap.r.PHONE_NOT_SINGLE': 'not exactly one phone number',
  'ap.r.ITEM_NOT_MATCHED': 'an item was not found in your products',
  'ap.r.COD_NOT_CALCULATED': 'the price could not be calculated',
  'ap.r.ZONE_UNRESOLVED': 'the delivery area is unknown',
  'ap.r.LOCATION_UNRESOLVED': "the courier's area for this address is not certain",
  'ap.r.COD_MISMATCH': 'the amount does not match your prices',
  'ap.r.OUT_OF_STOCK': 'an item is out of stock',
  'ap.r.DUPLICATE_RECENT': 'this customer ordered in the last 24 hours',
  'ap.r.POLICY_SELLER_REVIEW': 'this customer needs your review',
  'ap.r.DAILY_CAP_REACHED': "today's limit was reached",
  'ap.r.ORDER_REFUSED': 'the order could not be saved',
  'ap.r.unknown': 'held for review',
  'ap.c.NO_DEFAULT_COURIER': 'No default courier is set',
  'ap.c.PROVIDER_OFF': 'This courier is not switched on yet',
  'ap.c.NOT_LIVE_VERIFIED': 'ecomsbd has not verified this courier live yet',
  'ap.c.NOT_CONNECTED': 'Connect your account with this courier',
  'ap.c.UNHEALTHY': 'This courier is failing right now',
  'ap.usesDefault': 'Autopilot uses your default courier: {courier}',
  'ap.compare': 'Compare: your delivered rate, last {days} days',
  'ap.compareRow': '{courier}: {percent}% delivered ({delivered} of {parcels})',
  'ap.compareDefault': 'your default',
  'ap.compareNone': 'Not enough of your own parcels yet to compare couriers.',
} as const;

export type AutopilotKey = keyof typeof autopilotEn;

export const autopilotBn: Record<AutopilotKey, string> = {
  'nav.autopilot': 'অটোপাইলট',
  'ap.subtitle': 'সম্পূর্ণ চ্যাট অর্ডার নিজে থেকেই অর্ডার হবে এবং কনফার্মেশনের জন্য কাস্টমারের কাছে যাবে।',
  'ap.notEnabled': 'আপনার দোকানে অটোপাইলট এখনো চালু হয়নি।',
  'ap.readOnly': 'শুধু দোকানের মালিক বা ম্যানেজার এটি বদলাতে পারেন।',
  'ap.what': 'অটোপাইলট যা করে',
  'ap.what1': 'সম্পূর্ণ চ্যাট অর্ডার (একটি ফোন নম্বর, প্রতিটি পণ্য পাওয়া গেছে ও স্টকে আছে, এলাকা জানা, আপনার ডেলিভারি চার্জ অনুযায়ী দাম) অর্ডার হয়ে যায়।',
  'ap.what2': 'তারপর কাস্টমারকে কল করে বা আপনার মাধ্যমে কনফার্ম করা হয়। কাস্টমার কনফার্ম করলেই শুধু "গ্রাহক নিশ্চিত করলে → কুরিয়ার বুক" ওয়ার্কফ্লো কুরিয়ার বুক করে।',
  'ap.what3': 'অস্পষ্ট কিছু থাকলে কারণসহ চ্যাট অর্ডারে আপনার জন্য থেকে যায়। আপনার লিংক করা ফোন Create না চেপেই অর্ডারের মেসেজ পাঠাতে পারবে।',
  'ap.checklist': 'চালুর আগে যা লাগবে',
  'ap.checklistHint': 'প্রতিটি অর্ডারের আগে আবার দেখা হয়।',
  'ap.l.PLAN': 'আপনার প্ল্যানে অটোপাইলট আছে',
  'ap.l.CONFIRMATIONS': 'কাস্টমার কনফার্মেশন চালু',
  'ap.l.COURIER': 'ডিফল্ট কুরিয়ার কানেক্টেড ও যাচাই করা',
  'ap.l.DELIVERY_RULES': 'সব জোনের ডেলিভারি চার্জ ও ডিফল্ট কুরিয়ার সেট করা',
  'ap.l.VOICE': 'কনফার্মেশন কল প্রস্তুত, অথবা আপনি নিজে কাস্টমারকে কল করবেন',
  'ap.l.HISTORY': 'অন্তত {needed}টি অর্ডার কনফার্ম হয়েছে (এখন পর্যন্ত {confirmed}টি)',
  'ap.l.CONSENT': 'অটোপাইলট যা করে তাতে আপনি রাজি',
  'ap.manualQueue': 'কনফার্মেশন কল বন্ধ থাকলে আমি কল কিউ থেকে নিজেই কাস্টমারকে কল করব',
  'ap.consent': 'অটোপাইলট যা করে তা পড়েছি এবং চাই এটি আমার দোকানের জন্য অর্ডার তৈরি করুক',
  'ap.turnOn': 'অটোপাইলট চালু করুন',
  'ap.turnOff': 'অটোপাইলট বন্ধ করুন',
  'ap.on': 'অটোপাইলট চালু আছে।',
  'ap.off': 'অটোপাইলট বন্ধ আছে।',
  'ap.pausedUntil': '{time} পর্যন্ত বিরতিতে।',
  'ap.dailyCap': 'দিনে সর্বোচ্চ {cap}টি অর্ডার।',
  'ap.workflowOff': 'অটোপাইলট ওয়ার্কফ্লো চালু নেই। অটোমেশনে গিয়ে ইনস্টল করে চালু করুন।',
  'ap.workflowLink': 'অটোমেশন খুলুন',
  'ap.home': 'আজকের অটোপাইলট',
  'ap.made': 'তৈরি অর্ডার',
  'ap.confirmed': 'কনফার্মড',
  'ap.cancelled': 'বাতিল',
  'ap.waiting': 'অপেক্ষায়',
  'ap.recent': 'শেষ সিদ্ধান্তগুলো',
  'ap.none': 'আজ এখনো কিছু হয়নি।',
  'ap.madeOrder': 'অর্ডার {number} তৈরি হয়েছে',
  'ap.heldFor': 'আপনার জন্য রাখা হয়েছে: {reason}',
  'ap.pause': '১ ঘণ্টা বিরতি',
  'ap.resume': 'এখনই আবার চালু',
  'ap.pausedNote': 'বিরতিতে আছে। কোনো অর্ডার তৈরি হবে না, কিউর কলগুলো অপেক্ষা করবে; কিছুই বাতিল হবে না।',
  'ap.r.AUTOPILOT_DISABLED': 'ecomsbd অটোপাইলট বন্ধ রেখেছে',
  'ap.r.SHOP_INACTIVE': 'দোকানটি সক্রিয় নয়',
  'ap.r.AUTOPILOT_OFF': 'অটোপাইলট বন্ধ',
  'ap.r.AUTOPILOT_PAUSED': 'অটোপাইলট বিরতিতে ছিল',
  'ap.r.NOT_IN_PLAN': 'আপনার প্ল্যানে অটোপাইলট নেই',
  'ap.r.CONFIRMATION_ENGINE_OFF': 'কাস্টমার কনফার্মেশন বন্ধ',
  'ap.r.COURIER_NOT_READY': 'ডিফল্ট কুরিয়ার প্রস্তুত নয়',
  'ap.r.DELIVERY_RULES_INCOMPLETE': 'ডেলিভারি চার্জ সম্পূর্ণ নয়',
  'ap.r.VOICE_UNAVAILABLE': 'কনফার্মেশন কল বন্ধ',
  'ap.r.NOT_ENOUGH_CONFIRMED': 'যথেষ্ট কনফার্মড অর্ডার এখনো হয়নি',
  'ap.r.CONSENT_MISSING': 'অটোপাইলটে সম্মতি দেওয়া হয়নি',
  'ap.r.DRAFT_NOT_READY': 'চ্যাট অর্ডারটি সম্পূর্ণ নয়',
  'ap.r.FIELDS_UNCERTAIN': 'কিছু তথ্য নিশ্চিত নয়',
  'ap.r.PHONE_NOT_SINGLE': 'ঠিক একটি ফোন নম্বর নেই',
  'ap.r.ITEM_NOT_MATCHED': 'একটি পণ্য আপনার পণ্যের তালিকায় পাওয়া যায়নি',
  'ap.r.COD_NOT_CALCULATED': 'দাম হিসাব করা যায়নি',
  'ap.r.ZONE_UNRESOLVED': 'ডেলিভারি এলাকা জানা নেই',
  'ap.r.LOCATION_UNRESOLVED': 'এই ঠিকানার কুরিয়ার এলাকা নিশ্চিত নয়',
  'ap.r.COD_MISMATCH': 'টাকার পরিমাণ আপনার দামের সাথে মেলে না',
  'ap.r.OUT_OF_STOCK': 'একটি পণ্য স্টকে নেই',
  'ap.r.DUPLICATE_RECENT': 'এই কাস্টমার গত ২৪ ঘণ্টায় অর্ডার করেছেন',
  'ap.r.POLICY_SELLER_REVIEW': 'এই কাস্টমারকে আপনার দেখা দরকার',
  'ap.r.DAILY_CAP_REACHED': 'আজকের সীমা পূর্ণ হয়েছে',
  'ap.r.ORDER_REFUSED': 'অর্ডারটি সেভ করা যায়নি',
  'ap.r.unknown': 'দেখার জন্য রাখা হয়েছে',
  'ap.c.NO_DEFAULT_COURIER': 'কোনো ডিফল্ট কুরিয়ার সেট নেই',
  'ap.c.PROVIDER_OFF': 'এই কুরিয়ার এখনো চালু হয়নি',
  'ap.c.NOT_LIVE_VERIFIED': 'ecomsbd এখনো এই কুরিয়ার লাইভ যাচাই করেনি',
  'ap.c.NOT_CONNECTED': 'এই কুরিয়ারে আপনার অ্যাকাউন্ট কানেক্ট করুন',
  'ap.c.UNHEALTHY': 'এই কুরিয়ার এখন কাজ করছে না',
  'ap.usesDefault': 'অটোপাইলট আপনার ডিফল্ট কুরিয়ার ব্যবহার করে: {courier}',
  'ap.compare': 'তুলনা: গত {days} দিনে আপনার ডেলিভারির হার',
  'ap.compareRow': '{courier}: {percent}% ডেলিভারি ({parcels}টির মধ্যে {delivered}টি)',
  'ap.compareDefault': 'আপনার ডিফল্ট',
  'ap.compareNone': 'কুরিয়ার তুলনা করার মতো যথেষ্ট পার্সেল এখনো নেই।',
};
