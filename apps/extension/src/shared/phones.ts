/**
 * Phone numbers in chat text, by the server's rules only (rule L5).
 *
 * A port of the Android `IntakeClassifier.findPhones` / `IntakeText.normalize`
 * (apps/mobile_flutter/android/.../intake/IntakeClassifier.kt), so a number
 * means the same thing on the phone and in the browser:
 *
 * * text is normalised: NFC, lower-case, every run of white space (Java `\s`
 *   plus no-break, figure and narrow no-break spaces) as one space, trimmed;
 * * a phone span is any match of a rules pattern; Bangla digits become ASCII,
 *   everything else but digits is dropped, and the national pattern must match
 *   either the digits or the digits without the country prefix.
 *
 * Nothing here is a number format of this build: the patterns come from
 * `GET /v1/intake/rules`.
 */

export interface PhoneRules {
  patterns: string[];
  national_pattern: string;
  country_prefix: string;
  required?: boolean;
}

export interface CompiledPhoneRules {
  patterns: RegExp[];
  national: RegExp;
  countryPrefix: string;
}

export interface PhoneSpan {
  /** Offsets in the text as given (not normalised). */
  start: number;
  end: number;
  /** `01XXXXXXXXX`. */
  national: string;
}

const BANGLA_ZERO = 0x09e6;
const MASK = String.fromCharCode(0x2022).repeat(3);
const SPACES = new RegExp(`[ \\t\\n\\v\\f\\r${String.fromCharCode(0xa0, 0x2007, 0x202f)}]+`, 'g');

export function compilePhoneRules(rules: PhoneRules): CompiledPhoneRules {
  return {
    patterns: rules.patterns.map((pattern) => new RegExp(pattern, 'g')),
    // Kotlin's `matches` is a whole-string match.
    national: new RegExp(`^(?:${rules.national_pattern})$`),
    countryPrefix: rules.country_prefix,
  };
}

/** NFC, lower-case, one space for every run of white space, trimmed. */
export function normalize(text: string): string {
  return text.normalize('NFC').toLowerCase().replace(SPACES, ' ').trim();
}

/** The digits of a matched span as a national number, or null. */
export function toNational(span: string, rules: CompiledPhoneRules): string | null {
  let digits = '';
  for (const char of span) {
    const code = char.charCodeAt(0);
    if (code >= 0x30 && code <= 0x39) digits += char;
    else if (code >= BANGLA_ZERO && code <= BANGLA_ZERO + 9) digits += String(code - BANGLA_ZERO);
  }
  if (rules.national.test(digits)) return digits;
  if (digits.startsWith(rules.countryPrefix)) {
    const rest = digits.slice(rules.countryPrefix.length);
    if (rules.national.test(rest)) return rest;
  }
  return null;
}

/** National numbers in order of appearance, distinct (as the phone reads them). */
export function findPhones(text: string, rules: CompiledPhoneRules): string[] {
  const normalized = normalize(text);
  const found = new Map<number, string>();
  for (const pattern of rules.patterns) {
    for (const match of normalized.matchAll(pattern)) {
      const national = toNational(match[0], rules);
      if (national === null || [...found.values()].includes(national)) continue;
      found.set(match.index ?? 0, national);
    }
  }
  return [...new Set([...found.entries()].sort((a, b) => a[0] - b[0]).map(([, value]) => value))];
}

/** Where numbers sit in a piece of page text, for highlighting; no overlaps. */
export function phoneSpans(text: string, rules: CompiledPhoneRules): PhoneSpan[] {
  const spans: PhoneSpan[] = [];
  for (const pattern of rules.patterns) {
    for (const match of text.matchAll(pattern)) {
      const national = toNational(match[0], rules);
      const start = match.index ?? 0;
      if (national !== null) spans.push({ start, end: start + match[0].length, national });
    }
  }
  spans.sort((a, b) => a.start - b.start || b.end - a.end);
  const kept: PhoneSpan[] = [];
  for (const span of spans) {
    const last = kept[kept.length - 1];
    if (last === undefined || span.start >= last.end) kept.push(span);
  }
  return kept;
}

/** `01700000010` → `01700•••10`: enough to recognise, not to dial. */
export function maskPhone(national: string): string {
  return national.length < 8 ? MASK : national.slice(0, 5) + MASK + national.slice(-2);
}
