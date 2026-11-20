/**
 * Refer a shop (V4 WP7.4): types, copy and the code rules.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's type
 * stripping. The server decides whether referrals are offered at all
 * (`GET /v1/referrals`), checks every code and grants the credit; nothing here
 * grants anything.
 */

export type ReferralState = 'PENDING' | 'REWARDED' | 'VOID';
export type CreditState = 'PENDING' | 'GRANTED' | 'HELD';

export interface ReferredBy {
  code: string;
  state: ReferralState;
  credit_state: CreditState;
  applied_at: string;
  rewarded_at: string | null;
}

export interface ReferralRow {
  id: string;
  state: ReferralState;
  credit_state: CreditState;
  joined_at: string;
  rewarded_at: string | null;
}

export interface ReferralOverview {
  available: boolean;
  unavailable_reason: string | null;
  credit_days: number;
  code: string | null;
  referred_by: ReferredBy | null;
  referrals: ReferralRow[];
}

export const referralEn = {
  'ref.title': 'Refer a shop',
  'ref.intro': 'Share your code with another shop. When their first bKash or Nagad payment is approved, you both get {days} days of plan free.',
  'ref.yourCode': 'Your code',
  'ref.makeCode': 'Get my code',
  'ref.copy': 'Copy',
  'ref.copied': 'Copied',
  'ref.share': 'Use my code {code} when you pay for ecomsbd — we both get {days} days free.',
  'ref.joined': 'Shops that joined with your code',
  'ref.none': 'No shop has used your code yet.',
  'ref.joinedOn': 'Joined {date}',
  'ref.state.PENDING': 'Waiting for their first payment',
  'ref.state.REWARDED': 'First payment approved',
  'ref.state.VOID': 'Not eligible',
  'ref.credit.PENDING': 'Your {days} days: waiting',
  'ref.credit.GRANTED': 'Your {days} days: added',
  'ref.credit.HELD': 'Your {days} days: our team will add them',
  'ref.haveCode': 'Have a code from another shop?',
  'ref.haveHint': 'Enter it before your first payment.',
  'ref.apply': 'Use code',
  'ref.applying': 'Checking…',
  'ref.usedCode': 'You joined with code {code}.',
  'ref.errInvalid': 'A code is 8 letters and digits.',
  'ref.errUnknown': 'No shop has this code.',
  'ref.errOwn': 'This is your own shop’s code.',
  'ref.errSameOwner': 'Both shops have the same owner.',
  'ref.errCycle': 'That shop joined with your code.',
  'ref.errAlready': 'This shop already used a code.',
  'ref.errPaid': 'A code can only be used before the first payment.',
  'ref.errUnavailable': 'Referrals are not available right now.',
} as const;

export type ReferralKey = keyof typeof referralEn;

export const referralBn: Record<ReferralKey, string> = {
  'ref.title': 'অন্য দোকানকে রেফার করুন',
  'ref.intro': 'আপনার কোড অন্য দোকানকে দিন। তাদের প্রথম বিকাশ বা নগদ পেমেন্ট গ্রহণ হলে আপনারা দুজনেই {days} দিন প্ল্যান ফ্রি পাবেন।',
  'ref.yourCode': 'আপনার কোড',
  'ref.makeCode': 'আমার কোড নিন',
  'ref.copy': 'কপি',
  'ref.copied': 'কপি হয়েছে',
  'ref.share': 'ecomsbd-এর পেমেন্টের সময় আমার কোড {code} ব্যবহার করুন — আমরা দুজনেই {days} দিন ফ্রি পাব।',
  'ref.joined': 'যে দোকানগুলো আপনার কোড দিয়ে যোগ দিয়েছে',
  'ref.none': 'এখনো কোনো দোকান আপনার কোড ব্যবহার করেনি।',
  'ref.joinedOn': '{date} তারিখে যোগ দিয়েছে',
  'ref.state.PENDING': 'তাদের প্রথম পেমেন্টের অপেক্ষায়',
  'ref.state.REWARDED': 'প্রথম পেমেন্ট গ্রহণ হয়েছে',
  'ref.state.VOID': 'প্রযোজ্য নয়',
  'ref.credit.PENDING': 'আপনার {days} দিন: অপেক্ষায়',
  'ref.credit.GRANTED': 'আপনার {days} দিন: যোগ হয়েছে',
  'ref.credit.HELD': 'আপনার {days} দিন: আমাদের টিম যোগ করে দেবে',
  'ref.haveCode': 'অন্য দোকানের কোড আছে?',
  'ref.haveHint': 'প্রথম পেমেন্টের আগে কোডটি লিখুন।',
  'ref.apply': 'কোড ব্যবহার করুন',
  'ref.applying': 'যাচাই হচ্ছে…',
  'ref.usedCode': 'আপনি {code} কোড দিয়ে যোগ দিয়েছেন।',
  'ref.errInvalid': 'কোড ৮টি ইংরেজি অক্ষর ও সংখ্যা হয়।',
  'ref.errUnknown': 'এই কোড কোনো দোকানের নয়।',
  'ref.errOwn': 'এটি আপনার নিজের দোকানের কোড।',
  'ref.errSameOwner': 'দুই দোকানের মালিক একই।',
  'ref.errCycle': 'ওই দোকান আপনার কোড দিয়ে যোগ দিয়েছে।',
  'ref.errAlready': 'এই দোকান আগেই একটি কোড ব্যবহার করেছে।',
  'ref.errPaid': 'প্রথম পেমেন্টের আগেই শুধু কোড ব্যবহার করা যায়।',
  'ref.errUnavailable': 'এখন রেফারেল চালু নেই।',
};

const BANGLA_DIGITS = '০১২৩৪৫৬৭৮৯';

/** What the server will compare: upper-case, Bangla digits folded, no spaces or dashes. */
export function cleanCode(raw: string): string {
  return raw
    .replace(/[০-৯]/g, (digit) => String(BANGLA_DIGITS.indexOf(digit)))
    .replace(/[\s-]+/g, '')
    .toUpperCase();
}

/** Eight characters from the server's alphabet (no 0/O, 1/I/L). */
export function isCode(raw: string): boolean {
  return /^[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{8}$/.test(cleanCode(raw));
}

/** The copy for a refused code, from the API's `details.code`. */
export function refusalKey(details: Record<string, unknown> | null): ReferralKey | null {
  switch (details?.code) {
    case 'REFERRAL_CODE_INVALID':
      return 'ref.errInvalid';
    case 'REFERRAL_CODE_UNKNOWN':
      return 'ref.errUnknown';
    case 'REFERRAL_OWN_SHOP':
      return 'ref.errOwn';
    case 'REFERRAL_SAME_OWNER':
      return 'ref.errSameOwner';
    case 'REFERRAL_CYCLE':
      return 'ref.errCycle';
    case 'REFERRAL_ALREADY_APPLIED':
      return 'ref.errAlready';
    case 'REFERRAL_AFTER_FIRST_PAYMENT':
      return 'ref.errPaid';
    case 'REFERRALS_UNAVAILABLE':
      return 'ref.errUnavailable';
    default:
      return null;
  }
}

export function stateKey(state: ReferralState): ReferralKey {
  return `ref.state.${state}`;
}

export function creditKey(state: CreditState): ReferralKey {
  return `ref.credit.${state}`;
}
