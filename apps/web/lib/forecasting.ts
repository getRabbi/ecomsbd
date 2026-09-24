export type Confidence = 'HIGH' | 'MEDIUM' | 'INSUFFICIENT';

export interface Method {
  history_days: number;
  min_in_stock_days: number;
  min_units: number;
  service_z: number;
}

export interface ItemForecast {
  product_id: string;
  variant_id: string | null;
  name: string;
  variant_name: string | null;
  sku: string | null;
  confidence: Confidence;
  in_stock_days: number;
  units_sold: number;
  rate_per_day: number | null;
  recent_rate_per_day: number | null;
  long_rate_per_day: number | null;
  forecast_30_days: number | null;
  on_hand: number;
  incoming: number;
  lead_time_days: number;
  lead_time_source: 'OBSERVED' | 'SUPPLIER' | 'DEFAULT';
  lead_time_samples: number;
  supplier_id: string | null;
  supplier_name: string | null;
  cover_days: number;
  safety_stock: number | null;
  reorder_point: number | null;
  suggested_quantity: number | null;
  days_of_cover: number | null;
  stockout_on: string | null;
  at_risk: boolean;
}

export interface DemandList {
  items: ItemForecast[];
  total: number;
  counts: { all: number; at_risk: number; insufficient: number };
  cover_days: number;
  method: Method;
  can_draft: boolean;
}

export interface ItemDetail extends ItemForecast {
  history: { date: string; units: number; in_stock: boolean }[];
  method: Method;
  can_draft: boolean;
}

export interface Accuracy {
  status: 'NOT_ENOUGH_HISTORY' | 'SCORED' | 'NO_SALES';
  as_of: string | null;
  items: number;
  skipped?: number;
  predicted_units?: number;
  actual_units?: number;
  error_bps?: number | null;
  bias_units?: number;
}

export interface CashOutlook {
  as_of: string;
  inflow: Record<string, number>;
  outflow: { overdue: number; next_7_days: number; days_8_to_14: number; later: number; no_due_date: number };
  committed_on_open_orders_paisa: number;
  net_7_days_paisa: number;
  net_14_days_paisa: number;
  unplaced_inflow_paisa: number;
}

export function itemLabel(item: Pick<ItemForecast, 'name' | 'variant_name'>): string {
  return item.variant_name ? `${item.name} (${item.variant_name})` : item.name;
}

export function itemHref(item: Pick<ItemForecast, 'product_id' | 'variant_id'>): string {
  const query = new URLSearchParams({ product_id: item.product_id });
  if (item.variant_id) query.set('variant_id', item.variant_id);
  return `/forecasting/item?${query.toString()}`;
}

/** Basis points as a percentage with one decimal: 1234 → "12.3%". */
export function percent(bps: number): string {
  return `${(bps / 100).toFixed(1)}%`;
}
