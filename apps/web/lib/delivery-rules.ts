/**
 * Shop delivery rules (V4 WP0.4): the API shape and the form <-> API mapping.
 *
 * Money stays integer paisa (no float); percentages are shown whole and sent
 * as basis points. Self-contained (no value imports) so `npm test` can load it
 * directly; `tests/delivery.test.mjs` checks the money conversion matches
 * `lib/orders.ts`.
 */

function takaToPaisa(value: string): number | null {
  const trimmed = value.trim().replaceAll(',', '');
  if (!trimmed) return null;
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(trimmed);
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'));
}

export function paisaToTaka(paisa: number): string {
  const whole = Math.trunc(paisa / 100);
  const fraction = Math.abs(paisa % 100);
  return fraction ? `${whole}.${String(fraction).padStart(2, '0')}` : String(whole);
}

export { takaToPaisa };

export interface DeliveryRules {
  configured: boolean;
  inside_dhaka_charge_paisa: number;
  dhaka_suburb_charge_paisa: number;
  outside_dhaka_charge_paisa: number;
  default_courier: string | null;
  max_auto_cod_paisa: number;
  risk_low_bps: number;
  trusted_network_bps: number;
  trusted_repeat_min: number;
  quiet_start: string;
  quiet_end: string;
  autopilot_daily_cap: number;
  call_everyone: boolean;
  call_nobody: boolean;
}

export type FieldKind = 'money' | 'count' | 'percent' | 'time';

export const RULE_FIELDS = {
  inside_dhaka_charge_paisa: 'money',
  dhaka_suburb_charge_paisa: 'money',
  outside_dhaka_charge_paisa: 'money',
  max_auto_cod_paisa: 'money',
  autopilot_daily_cap: 'count',
  quiet_start: 'time',
  quiet_end: 'time',
  risk_low_bps: 'percent',
  trusted_network_bps: 'percent',
  trusted_repeat_min: 'count',
} as const satisfies Record<string, FieldKind>;

export type RuleField = keyof typeof RULE_FIELDS;

export interface RulesForm {
  text: Record<RuleField, string>;
  default_courier: string | null;
  call_everyone: boolean;
  call_nobody: boolean;
}

const TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function toForm(rules: DeliveryRules): RulesForm {
  const text = {} as Record<RuleField, string>;
  for (const [key, kind] of Object.entries(RULE_FIELDS) as [RuleField, FieldKind][]) {
    const value = rules[key];
    text[key] =
      kind === 'money'
        ? paisaToTaka(value as number)
        : kind === 'percent'
          ? String(Math.trunc((value as number) / 100))
          : String(value);
  }
  return {
    text,
    default_courier: rules.default_courier,
    call_everyone: rules.call_everyone,
    call_nobody: rules.call_nobody,
  };
}

export function parseField(kind: FieldKind, raw: string): number | string | null {
  const value = raw.trim();
  if (kind === 'money') {
    return takaToPaisa(value);
  }
  if (kind === 'time') {
    return TIME.test(value) ? value : null;
  }
  if (!/^\d{1,5}$/.test(value)) {
    return null;
  }
  const number = Number(value);
  if (kind === 'percent') {
    return number <= 100 ? number * 100 : null;
  }
  return number;
}

/** The PATCH body, or the fields that are not valid. */
export function toPatch(form: RulesForm): { body: Record<string, unknown> } | { invalid: RuleField[] } {
  const body: Record<string, unknown> = {};
  const invalid: RuleField[] = [];
  for (const [key, kind] of Object.entries(RULE_FIELDS) as [RuleField, FieldKind][]) {
    const parsed = parseField(kind, form.text[key]);
    if (parsed === null) {
      invalid.push(key);
    } else {
      body[key] = parsed;
    }
  }
  if (invalid.length) {
    return { invalid };
  }
  body.default_courier = form.default_courier;
  body.call_everyone = form.call_everyone;
  body.call_nobody = form.call_nobody && !form.call_everyone;
  return { body };
}

/** Turning one call switch on turns the other off; both on is never sent. */
export function setCallSwitch(form: RulesForm, which: 'call_everyone' | 'call_nobody', on: boolean): RulesForm {
  const other = which === 'call_everyone' ? 'call_nobody' : 'call_everyone';
  return { ...form, [which]: on, ...(on ? { [other]: false } : {}) };
}
