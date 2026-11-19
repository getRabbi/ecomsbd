/**
 * Pay by bKash / Nagad with a TrxID (V4 WP2.5): types, copy and form rules.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's type
 * stripping. The server decides whether the form is offered at all
 * (`GET /v1/billing/manual-payments/options`) and re-checks everything on
 * submit; nothing here grants anything.
 */

export type PaymentMethod = 'BKASH' | 'NAGAD';
export type SubmissionState = 'SUBMITTED' | 'APPROVED' | 'REJECTED';

export interface ReceivingAccount {
  method: PaymentMethod;
  number: string;
  label: string;
}

export interface PayablePlan {
  code: string;
  name: string;
  monthly_price: { amount_paisa: number; currency: string };
}

export interface ManualPaymentOptions {
  available: boolean;
  unavailable_reason: string | null;
  accounts: ReceivingAccount[];
  plans: PayablePlan[];
  min_months: number;
  max_months: number;
}

export interface ManualPaymentSubmission {
  id: string;
  plan: string;
  months: number;
  amount: { amount_paisa: number; currency: string };
  method: PaymentMethod;
  payer_number_masked: string;
  trx_id: string;
  state: SubmissionState;
  reason: string | null;
  period_end: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface PaymentForm {
  plan: string;
  months: number;
  method: PaymentMethod | '';
  payer: string;
  trx: string;
}

export type PaymentField = 'plan' | 'months' | 'method' | 'payer' | 'trx';

/** The month choices offered; the server accepts 1–12. */
export const MONTH_CHOICES = [1, 3, 6, 12] as const;

export const manualPaymentEn = {
  'mp.title': 'Pay by bKash or Nagad',
  'mp.intro': 'Send the amount below with Send Money, then enter the TrxID from your bKash or Nagad app. We check it and turn your plan on, usually within a day.',
  'mp.plan': 'Plan',
  'mp.months': 'Months',
  'mp.monthsValue': '{count} month(s)',
  'mp.amount': 'Amount to send',
  'mp.sendTo': 'Send to',
  'mp.method': 'Paid with',
  'mp.payer': 'Number you sent from',
  'mp.trx': 'TrxID',
  'mp.trxHint': 'The transaction ID in the bKash / Nagad confirmation message, e.g. 8N7A6D5C4B.',
  'mp.submit': 'Submit payment',
  'mp.sending': 'Submitting…',
  'mp.invalid': 'Check the highlighted fields.',
  'mp.sent': 'Submitted. We will check the TrxID and let you know.',
  'mp.history': 'Your payments',
  'mp.state.SUBMITTED': 'Waiting for check',
  'mp.state.APPROVED': 'Approved',
  'mp.state.REJECTED': 'Not approved',
  'mp.until': 'Plan active until {date}',
  'mp.why': 'Reason: {reason}',
  'mp.errUsed': 'This TrxID has already been submitted.',
  'mp.errTrx': 'A TrxID is 6 to 20 letters and digits.',
  'mp.errUnavailable': 'Paying by bKash or Nagad is not available right now.',
  'mp.ownerOnly': 'Only the shop owner can pay for a plan.',
  'mp.bkash': 'bKash',
  'mp.nagad': 'Nagad',
} as const;

export type ManualPaymentKey = keyof typeof manualPaymentEn;

export const manualPaymentBn: Record<ManualPaymentKey, string> = {
  'mp.title': 'বিকাশ বা নগদে পেমেন্ট',
  'mp.intro': 'নিচের টাকা Send Money করে পাঠান, তারপর বিকাশ বা নগদ অ্যাপে পাওয়া TrxID লিখুন। আমরা যাচাই করে সাধারণত এক দিনের মধ্যে প্ল্যান চালু করে দিই।',
  'mp.plan': 'প্ল্যান',
  'mp.months': 'মাস',
  'mp.monthsValue': '{count} মাস',
  'mp.amount': 'যত টাকা পাঠাবেন',
  'mp.sendTo': 'এই নম্বরে পাঠান',
  'mp.method': 'যেভাবে পাঠিয়েছেন',
  'mp.payer': 'যে নম্বর থেকে পাঠিয়েছেন',
  'mp.trx': 'TrxID',
  'mp.trxHint': 'বিকাশ / নগদের কনফার্মেশন মেসেজে থাকা ট্রানজেকশন আইডি, যেমন 8N7A6D5C4B।',
  'mp.submit': 'পেমেন্ট জমা দিন',
  'mp.sending': 'জমা হচ্ছে…',
  'mp.invalid': 'চিহ্নিত ঘরগুলো ঠিক করুন।',
  'mp.sent': 'জমা হয়েছে। আমরা TrxID যাচাই করে আপনাকে জানাব।',
  'mp.history': 'আপনার পেমেন্ট',
  'mp.state.SUBMITTED': 'যাচাইয়ের অপেক্ষায়',
  'mp.state.APPROVED': 'গ্রহণ করা হয়েছে',
  'mp.state.REJECTED': 'গ্রহণ করা হয়নি',
  'mp.until': 'প্ল্যান চালু থাকবে {date} পর্যন্ত',
  'mp.why': 'কারণ: {reason}',
  'mp.errUsed': 'এই TrxID আগেই জমা দেওয়া হয়েছে।',
  'mp.errTrx': 'TrxID ৬ থেকে ২০টি ইংরেজি অক্ষর ও সংখ্যা হয়।',
  'mp.errUnavailable': 'এখন বিকাশ বা নগদে পেমেন্ট নেওয়া যাচ্ছে না।',
  'mp.ownerOnly': 'শুধু শপের মালিক প্ল্যানের পেমেন্ট করতে পারেন।',
  'mp.bkash': 'বিকাশ',
  'mp.nagad': 'নগদ',
};

/** Integer paisa × months. The server computes the same and is the authority. */
export function amountPaisa(plan: PayablePlan | undefined, months: number): number {
  return plan ? plan.monthly_price.amount_paisa * months : 0;
}

/** `৳597` / `৳1,250.50` from integer paisa. */
export function formatTaka(paisa: number): string {
  const taka = Math.floor(paisa / 100);
  const rest = paisa % 100;
  const whole = taka.toLocaleString('en-US');
  return rest ? `৳${whole}.${String(rest).padStart(2, '0')}` : `৳${whole}`;
}

const BANGLA_DIGITS = '০১২৩৪৫৬৭৮৯';

function asciiDigits(value: string): string {
  return value.replace(/[০-৯]/g, (digit) => String(BANGLA_DIGITS.indexOf(digit)));
}

/** What the server will store: upper-case, Bangla digits folded, no spaces or dashes. */
export function cleanTrx(raw: string): string {
  return asciiDigits(raw).replace(/[\s-]+/g, '').toUpperCase();
}

export function isTrx(raw: string): boolean {
  return /^[A-Z0-9]{6,20}$/.test(cleanTrx(raw));
}

export function isBdMobile(raw: string): boolean {
  const compact = asciiDigits(raw).replace(/[\s-]/g, '');
  return /^(?:\+?88)?01[3-9]\d{8}$/.test(compact);
}

export function invalidFields(form: PaymentForm, options: ManualPaymentOptions): PaymentField[] {
  const invalid: PaymentField[] = [];
  if (!options.plans.some((plan) => plan.code === form.plan)) invalid.push('plan');
  if (!(form.months >= options.min_months && form.months <= options.max_months)) invalid.push('months');
  if (!options.accounts.some((account) => account.method === form.method)) invalid.push('method');
  if (!isBdMobile(form.payer)) invalid.push('payer');
  if (!isTrx(form.trx)) invalid.push('trx');
  return invalid;
}

export function submitBody(form: PaymentForm) {
  return {
    plan_code: form.plan,
    months: form.months,
    method: form.method,
    payer_number: form.payer.trim(),
    trx_id: cleanTrx(form.trx),
  };
}

/** The copy for a refused submit, from the API's `details.code`. */
export function refusalKey(details: Record<string, unknown> | null): ManualPaymentKey | null {
  switch (details?.code) {
    case 'TRX_ID_ALREADY_USED':
      return 'mp.errUsed';
    case 'TRX_ID_INVALID':
      return 'mp.errTrx';
    case 'MANUAL_PAYMENT_UNAVAILABLE':
      return 'mp.errUnavailable';
    default:
      return null;
  }
}

export function stateKey(state: SubmissionState): ManualPaymentKey {
  return `mp.state.${state}`;
}

export function methodKey(method: PaymentMethod): ManualPaymentKey {
  return method === 'NAGAD' ? 'mp.nagad' : 'mp.bkash';
}
