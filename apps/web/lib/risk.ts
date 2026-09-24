/** Shapes of the V3.7 risk, provider and network endpoints. */
import type { StringKey } from './i18n';

type T = (key: StringKey, vars?: Record<string, string | number>) => string;

export type Freshness = 'FRESH' | 'STALE';

export interface ProviderFact {
  code: string;
  value: number;
  period_days: number | null;
}

export interface LookupResult {
  status: 'FOUND' | 'NOT_FOUND';
  facts: ProviderFact[];
  provider_observed_at: string | null;
  checked_at: string | null;
  expires_at: string | null;
  freshness: Freshness;
  sample_size: number | null;
  confidence: string | null;
}

export interface ProviderSection {
  provider_id: string;
  name: string;
  enabled: boolean;
  health: string;
  cache_ttl_hours: number;
  result: LookupResult | null;
  last_error: { code: string; at: string } | null;
}

export interface ExternalView {
  status: 'GATED' | 'NOT_CONFIGURED' | 'AVAILABLE';
  blocker: string | null;
  providers: ProviderSection[];
  outcomes?: { provider_id: string; outcome: 'FETCHED' | 'CACHED' | 'FAILED' | 'SKIPPED'; error_code: string | null }[];
}

export interface Cell {
  metric: string;
  dimension: string;
  cohort: string;
  status: 'PUBLISHED' | 'DATA_NOT_SUFFICIENT';
  value: number | null;
  precision: number | null;
  unit: 'PERCENT' | 'HOURS' | 'DAYS' | 'PAISA';
  shops_band: string | null;
  sample_band: string | null;
}

export interface CohortDefinition {
  market: string;
  period: string;
  dimension: string;
  minimum_shops: number;
  minimum_sample: number;
  max_share_percent: number;
}

export interface Release {
  period: string | null;
  computed_at: string | null;
  cells: Cell[];
  cohort_definition?: CohortDefinition | null;
}

export interface RiskProfile {
  customer_id: string;
  own_shop: {
    state: string;
    order_count: number;
    delivered_count: number;
    returned_count: number;
    cancelled_count: number;
    terminal_count: number;
    success_rate_basis_points: number | null;
    repeated_rto: boolean;
    in_transit_count: number;
    recent: { order_number: string; outcome: string; status: string; provider: string; at: string | null }[];
  };
  external: ExternalView;
  network: { period: string | null; computed_at: string | null; cells: Cell[] };
  can_lookup: boolean;
}

export interface CourierFacts {
  courier: string;
  live_integration: boolean;
  completed_parcels: number;
  delivered_parcels: number;
  rto_parcels: number;
  delivery_rate_bps: number | null;
  rto_rate_bps: number | null;
  median_delivery_hours: number | null;
  in_transit_parcels: number;
  stuck_parcels: number;
  median_payout_delay_days: number | null;
  outstanding_cod_paisa: number;
  overdue_cod_paisa: number;
  reconciliation_items: number;
  reconciliation_mismatches: number;
  median_delivery_charge_paisa: number | null;
}

export interface ProviderCatalog {
  status: 'AVAILABLE' | 'GATED';
  blocker: string | null;
  providers: {
    provider_id: string;
    name: string;
    official_contract: string;
    capabilities: string[];
    credential_fields: { name: string; label_en: string; label_bn: string; secret: boolean }[];
    cache_ttl_hours: { default: number; min: number; max: number };
    connection: {
      enabled: boolean;
      credentials_set: boolean;
      credential_hint: string | null;
      cache_ttl_hours: number;
      health: string;
      last_tested_at: string | null;
      last_test_result: string | null;
      last_success_at: string | null;
      last_failure_at: string | null;
      last_error_code: string | null;
      rate_limited_until: string | null;
    } | null;
  }[];
}

/** A cell's value in the seller's words. */
export function cellValue(cell: Cell, t: T): string {
  if (cell.status !== 'PUBLISHED' || cell.value === null) return t('net.insufficient');
  switch (cell.unit) {
    case 'PERCENT':
      return `${cell.value}%`;
    case 'HOURS':
      return t('net.unit.hours', { n: cell.value });
    case 'DAYS':
      return t('net.unit.days', { n: cell.value });
    case 'PAISA':
      return t('net.unit.taka', { n: Math.round(cell.value / 100) });
  }
}

export function when(value: string | null, locale: string): string {
  if (!value) return '—';
  return new Date(value).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
