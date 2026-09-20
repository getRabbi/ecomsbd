/** Read-only wire shapes; all business figures are computed by the API. */
export interface Comparison { current: number; previous: number; change_basis_points: number | null }
export interface Explanation { code: string; params: Record<string, string | number | null> }
export interface Counts { completed: number; delivered: number; partial: number; rto: number; sufficient: boolean; rto_rate_basis_points: number | null; success_rate_basis_points: number | null }
export interface Overview {
  window: { since: string; until: string; days: number };
  previous: { since: string; until: string };
  orders: Comparison; delivered: Comparison; rto: Counts;
  money_locked: string | null;
  money: null | { revenue: Comparison; profit: Comparison; received: Comparison;
    profit_quality: Record<string, number>; receivable_paisa: number; overdue_paisa: number;
    discrepancy_count: number; discrepancy_paisa: number };
  stock: null | { low_stock_items: number; out_of_stock_items: number; slow_moving_items: number; slow_moving_days: number };
  explanations: Explanation[];
}
export interface Trend { buckets: { start: string; end: string; orders: number; parcels: number; revenue_paisa: number | null; profit_paisa: number | null }[]; money_locked: string | null }
export interface Product { product_id: string | null; name: string; sku: string | null; parcels: number; units_delivered: number; revenue_paisa: number | null; profit_paisa: number | null; margin_basis_points: number | null; profit_quality: string | null; rto: Counts; stock_on_hand: number | null; stock_status: string; slow_moving: boolean; units_booked: number }
export interface ProductPage { items: Product[]; counts: Record<string, number>; has_more: boolean; money_locked: string | null; slow_moving_days: number }
export interface Couriers { items: { provider: string; counts: Counts; in_transit_now: number; stuck_now: number; outstanding_paisa: number | null; overdue_paisa: number | null; payout_delay: { median_days: number; reliable: boolean } | null; discrepancy_count: number | null; discrepancy_paisa: number | null }[]; money_locked: string | null; excluded_providers: string[] }
export interface Cash { received: Comparison; receivable_paisa: number; overdue_paisa: number; in_transit_paisa: number; aging: { min_days: number; max_days: number | null; count: number; amount_paisa: number }[]; explanations: Explanation[] }
export interface Reconciliation { matched: number; discrepancy_count: number; difference_paisa: number; missing_cod_count: number; missing_cod_paisa: number; charge_mismatch_count: number; charge_mismatch_paisa: number; unmatched_count: number; unmatched_paisa: number; open_case_count: number; open_case_paisa: number; explanations: Explanation[] }
export interface Customers { active: Comparison; new: Comparison; returning: number; orders_with_customer: number; repeat_orders: number; repeat_order_rate_basis_points: number | null; sufficient: boolean; repeat_customers: number; multi_delivery_customers: number; repeat_rto_customers: number; explanations: Explanation[] }
export interface Inventory { total_units: number; low_stock_items: number; out_of_stock_items: number; slow_moving_days: number; counts: Record<string, number>; has_more: boolean; items: { product_id: string; variant_id: string | null; name: string; variant_name: string | null; sku: string | null; stock_on_hand: number; status: string; units_booked: number; slow_moving: boolean }[]; explanations: Explanation[] }
