'use client';

import type { Tone } from '@/components/ui';
import type { Locale } from './i18n';

/**
 * Turning the API's state strings into something a seller reads.
 *
 * A seller should never have to read `OUT_FOR_DELIVERY` or
 * `PENDING_CALCULATION`, and the tone carries the same meaning the mobile app
 * gives it — an ambiguous booking is amber, not red, because nothing has gone
 * wrong yet and nothing should be retried.
 *
 * An unrecognised value falls through to itself rather than to "Unknown": a
 * state this build has not met is something support needs to see verbatim.
 */

interface StateCopy {
  en: string;
  bn: string;
  tone: Tone;
}

const FULFILLMENT: Record<string, StateCopy> = {
  NOT_BOOKED: { en: 'Not booked', bn: 'বুক হয়নি', tone: 'neutral' },
  BOOKING: { en: 'Booking…', bn: 'বুক হচ্ছে…', tone: 'warn' },
  // Amber, never red: the parcel may well exist, and the seller must not be
  // nudged into booking it again.
  BOOKING_UNKNOWN: { en: 'Unconfirmed', bn: 'নিশ্চিত নয়', tone: 'warn' },
  BOOKED: { en: 'With courier', bn: 'কুরিয়ারের কাছে', tone: 'neutral' },
  PICKED_UP: { en: 'Picked up', bn: 'তোলা হয়েছে', tone: 'neutral' },
  IN_TRANSIT: { en: 'On its way', bn: 'পথে আছে', tone: 'neutral' },
  OUT_FOR_DELIVERY: { en: 'Out for delivery', bn: 'ডেলিভারিতে গেছে', tone: 'neutral' },
  DELIVERED: { en: 'Delivered', bn: 'ডেলিভারি হয়েছে', tone: 'good' },
  PARTIAL_DELIVERED: { en: 'Partly delivered', bn: 'আংশিক ডেলিভারি', tone: 'warn' },
  RETURN_REQUESTED: { en: 'Return asked', bn: 'ফেরত চাওয়া হয়েছে', tone: 'warn' },
  RETURNING: { en: 'Coming back', bn: 'ফেরত আসছে', tone: 'warn' },
  RETURNED: { en: 'Returned', bn: 'ফেরত এসেছে', tone: 'bad' },
  CANCELLED: { en: 'Cancelled', bn: 'বাতিল', tone: 'neutral' },
  LOST: { en: 'Lost', bn: 'হারিয়ে গেছে', tone: 'bad' },
  DAMAGED: { en: 'Damaged', bn: 'ক্ষতিগ্রস্ত', tone: 'bad' },
  FAILED: { en: 'Failed', bn: 'ব্যর্থ', tone: 'bad' },
};

const RISK: Record<string, StateCopy> = {
  NOT_CHECKED: { en: 'Not checked', bn: 'দেখা হয়নি', tone: 'neutral' },
  LOW: { en: 'Low', bn: 'কম', tone: 'good' },
  MEDIUM: { en: 'Medium', bn: 'মাঝারি', tone: 'warn' },
  HIGH: { en: 'High', bn: 'বেশি', tone: 'bad' },
  // Thin history is reported as thin, never as low risk.
  UNKNOWN: { en: 'Too little history', bn: 'যথেষ্ট তথ্য নেই', tone: 'neutral' },
};

const PROFIT: Record<string, StateCopy> = {
  PENDING: { en: 'Pending', bn: 'অপেক্ষায়', tone: 'neutral' },
  PENDING_CALCULATION: { en: 'Pending', bn: 'অপেক্ষায়', tone: 'neutral' },
  ESTIMATED: { en: 'Estimated', bn: 'আনুমানিক', tone: 'warn' },
  FINAL: { en: 'Final', bn: 'চূড়ান্ত', tone: 'good' },
};

const ORDER_STATUS: Record<string, StateCopy> = {
  DRAFT: { en: 'Draft', bn: 'খসড়া', tone: 'neutral' },
  CONFIRMED: { en: 'Confirmed', bn: 'নিশ্চিত', tone: 'neutral' },
  PACKED: { en: 'Packed', bn: 'প্যাক হয়েছে', tone: 'neutral' },
  DISPATCHED: { en: 'Dispatched', bn: 'পাঠানো হয়েছে', tone: 'neutral' },
  COMPLETED: { en: 'Completed', bn: 'সম্পন্ন', tone: 'good' },
  CANCELLED: { en: 'Cancelled', bn: 'বাতিল', tone: 'neutral' },
};

function lookup(
  table: Record<string, StateCopy>,
  value: string | null | undefined,
  locale: Locale,
): { label: string; tone: Tone } {
  if (!value) {
    return { label: '—', tone: 'neutral' };
  }
  const found = table[value];
  if (!found) {
    // Shown verbatim. A state this build has not met is evidence, not noise.
    return { label: value.replaceAll('_', ' ').toLowerCase(), tone: 'neutral' };
  }
  return { label: locale === 'bn' ? found.bn : found.en, tone: found.tone };
}

export const describeFulfillment = (value: string | null | undefined, locale: Locale) =>
  lookup(FULFILLMENT, value, locale);

export const describeRisk = (value: string | null | undefined, locale: Locale) =>
  lookup(RISK, value, locale);

export const describeProfit = (value: string | null | undefined, locale: Locale) =>
  lookup(PROFIT, value, locale);

export const describeOrderStatus = (value: string | null | undefined, locale: Locale) =>
  lookup(ORDER_STATUS, value, locale);

/** The order statuses offered as a filter, in lifecycle order. */
export const ORDER_STATUS_VALUES = [
  'DRAFT',
  'CONFIRMED',
  'PACKED',
  'DISPATCHED',
  'COMPLETED',
  'CANCELLED',
] as const;

/**
 * Whether an order can be sent to a courier.
 *
 * A hint for the bulk action, not a decision: the API decides, and refuses with
 * a reason. Filtering here only avoids sending it a selection it will reject
 * wholesale.
 */
export function looksBookable(order: { status: string; fulfillment_state: string }): boolean {
  if (order.status === 'CANCELLED' || order.status === 'COMPLETED') {
    return false;
  }
  return order.fulfillment_state === 'NOT_BOOKED' || order.fulfillment_state === 'FAILED';
}
