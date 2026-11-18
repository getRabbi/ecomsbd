/**
 * Per-courier history from an external provider (V4 WP0.6), as one chip:
 * "Steadfast 15/17 · Pathao 8/9". Under the 3-parcel floor the server marks it
 * INSUFFICIENT_DATA and the chip says "too little data" instead of a ratio.
 * Self-contained so `npm test` can load it directly.
 */

export interface CourierCount {
  courier: string;
  delivered: number;
  cancelled: number;
  total: number;
}

export interface CourierHistory {
  provider_counts: CourierCount[];
  aggregate: { delivered: number; cancelled: number; total: number };
  as_of: string | null;
  state: 'OK' | 'INSUFFICIENT_DATA';
  success_bps: number | null;
}

const NAMES: Record<string, string> = {
  steadfast: 'Steadfast',
  pathao: 'Pathao',
  redx: 'RedX',
  paperfly: 'Paperfly',
  ecourier: 'eCourier',
  carrybee: 'CarryBee',
  sundarban: 'Sundarban',
};

export function courierLabel(key: string): string {
  return NAMES[key] ?? key.replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/** The chip text, or `komData` (already translated) under the floor. */
export function courierHistoryChip(history: CourierHistory | null | undefined, komData: string, allLabel: string): string | null {
  if (!history) return null;
  if (history.state === 'INSUFFICIENT_DATA') return komData;
  const rows = history.provider_counts.filter((row) => row.total > 0);
  if (rows.length === 0) {
    return `${allLabel} ${history.aggregate.delivered}/${history.aggregate.total}`;
  }
  return rows.map((row) => `${courierLabel(row.courier)} ${row.delivered}/${row.total}`).join(' · ');
}

/** Newest courier history across a customer's provider sections, if any. */
export function latestCourierHistory(
  sections: { result: { courier_history?: CourierHistory | null; checked_at: string | null } | null }[],
): CourierHistory | null {
  let best: { history: CourierHistory; at: string } | null = null;
  for (const section of sections) {
    const history = section.result?.courier_history;
    const at = section.result?.checked_at ?? '';
    if (history && (!best || at > best.at)) best = { history, at };
  }
  return best?.history ?? null;
}
