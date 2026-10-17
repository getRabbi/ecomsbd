/**
 * The one-time code Supabase emails to confirm a new account.
 *
 * Sellers are wary of links in email, so sign-up is confirmed by typing the
 * code instead. Supabase alone decides whether a code is right: this module
 * only tidies what was typed — Bangla digits become ASCII, spaces and dashes
 * go — so a correct code pasted as "১২৩ ৪৫৬" is not refused for its shape.
 */

//: Supabase's email OTP length is set per project, between 6 and 10 digits.
export const MIN_CODE_LENGTH = 6;
export const MAX_CODE_LENGTH = 10;

//: Supabase allows one confirmation email per address per minute.
export const RESEND_COOLDOWN_SECONDS = 60;

const BANGLA_DIGITS = '০১২৩৪৫৬৭৮৯';

/** Digits only, Bangla digits as ASCII, at most {@link MAX_CODE_LENGTH}. */
export function normalizeCode(raw: string): string {
  let digits = '';
  for (const character of raw) {
    const bangla = BANGLA_DIGITS.indexOf(character);
    if (bangla >= 0) {
      digits += String(bangla);
    } else if (character >= '0' && character <= '9') {
      digits += character;
    }
  }
  return digits.slice(0, MAX_CODE_LENGTH);
}

/** Whether `code` has a plausible length to send for checking. */
export function isCodeComplete(code: string): boolean {
  const length = normalizeCode(code).length;
  return length >= MIN_CODE_LENGTH && length <= MAX_CODE_LENGTH;
}

export type CodeFailure = 'invalid' | 'rateLimited' | 'other';

/**
 * Which message a failed check earns. Supabase answers a wrong code and an
 * expired one alike, and so does this.
 */
export function codeFailure(error: { code?: string; status?: number } | null): CodeFailure {
  if (!error) {
    return 'other';
  }
  if (error.status === 429 || error.code === 'over_request_rate_limit' || error.code === 'over_email_send_rate_limit') {
    return 'rateLimited';
  }
  if (error.code === 'otp_expired' || error.code === 'otp_disabled' || error.status === 403) {
    return 'invalid';
  }
  return 'other';
}
