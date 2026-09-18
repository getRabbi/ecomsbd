/**
 * Displaying money.
 *
 * This module **formats**; it does not compute. Every figure the dashboard
 * shows arrives from the API already decided — outstanding COD, payouts,
 * profit, a receivable's balance — because the ledger is the backend's job and
 * a second implementation in JavaScript would be a second answer. Two answers
 * to "how much am I owed?" is the one outcome a money screen must never have.
 *
 * So there is no `sum()` here, no `netOf()`, no margin calculation. If a screen
 * needs a total that the API does not return, the fix is an endpoint, not a
 * reduce over the page currently in the browser — which would be wrong anyway,
 * since a paginated table only ever holds part of the data.
 *
 * Amounts cross the wire as **integer paisa**, matching the BIGINT paisa the
 * database stores. They are never parsed into a float: 1050.5 taka cannot be
 * represented exactly, and a rounding drift in a COD total is the kind of bug
 * that surfaces months later as an unexplainable reconciliation difference.
 */

const TAKA = '৳'; // ৳

/**
 * Format integer paisa as taka.
 *
 * Integer arithmetic only — the fractional part is taken with `%`, never by
 * dividing into a float.
 */
export function formatPaisa(
  paisa: number | null | undefined,
  options: { locale?: string; showDecimals?: boolean; withSymbol?: boolean } = {},
): string {
  if (paisa === null || paisa === undefined || !Number.isFinite(paisa)) {
    return '—';
  }

  const { locale = 'en', showDecimals, withSymbol = true } = options;
  const negative = paisa < 0;
  const absolute = Math.abs(Math.trunc(paisa));
  const whole = Math.trunc(absolute / 100);
  const fraction = absolute % 100;

  // Decimals are shown when they carry information. A COD of exactly ৳1,050
  // reads better without them, and ৳1,050.50 must never be shown as ৳1,050.
  const withDecimals = showDecimals ?? fraction !== 0;

  const grouped = new Intl.NumberFormat(locale === 'bn' ? 'bn-BD' : 'en-BD').format(whole);
  const fractionText = withDecimals
    ? `.${fraction.toString().padStart(2, '0')}`
    : '';

  const sign = negative ? '-' : '';
  const symbol = withSymbol ? TAKA : '';
  return `${sign}${symbol}${grouped}${fractionText}`;
}

/**
 * A compact form for dense table cells, e.g. `৳1.2L`.
 *
 * Only ever used where the exact figure is one hover or one click away. A
 * number a seller might act on is never abbreviated.
 */
export function formatPaisaCompact(
  paisa: number | null | undefined,
  options: { locale?: string } = {},
): string {
  if (paisa === null || paisa === undefined || !Number.isFinite(paisa)) {
    return '—';
  }
  const taka = Math.trunc(Math.abs(paisa) / 100);
  const sign = paisa < 0 ? '-' : '';

  // Lakh and crore, because that is how a Bangladeshi seller reads a large
  // number — not "120K".
  if (taka >= 10_000_000) {
    return `${sign}${TAKA}${(taka / 10_000_000).toFixed(1)}Cr`;
  }
  if (taka >= 100_000) {
    return `${sign}${TAKA}${(taka / 100_000).toFixed(1)}L`;
  }
  if (taka >= 1_000) {
    return `${sign}${TAKA}${(taka / 1_000).toFixed(1)}K`;
  }
  return formatPaisa(paisa, { ...options, showDecimals: false });
}

/** A date as a seller reads it. Dates arrive as ISO strings from the API. */
export function formatDate(
  value: string | null | undefined,
  options: { locale?: string; withTime?: boolean } = {},
): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return '—';
  }
  return new Intl.DateTimeFormat(options.locale === 'bn' ? 'bn-BD' : 'en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    ...(options.withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
    // Every shop is Asia/Dhaka, and a business date must not shift with the
    // viewer's browser timezone.
    timeZone: 'Asia/Dhaka',
  }).format(parsed);
}
