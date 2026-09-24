import { ApiError } from './api';
import { strings, type StringKey } from './i18n';

export type PoStatus = 'DRAFT' | 'ORDERED' | 'PARTIALLY_RECEIVED' | 'RECEIVED' | 'CANCELLED';

export interface Payable {
  status: 'NOTHING_DUE' | 'UNPAID' | 'PARTIALLY_PAID' | 'PAID';
  owed_paisa: number;
  paid_paisa: number;
  balance_paisa: number;
  advance_paisa: number;
  due_at: string | null;
  overdue: boolean;
}

export interface Supplier {
  id: string;
  name: string;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  notes: string | null;
  payment_terms_days: number | null;
  lead_time_days?: number | null;
  is_active: boolean;
  open_orders?: number;
  balance_paisa?: number | null;
}

export interface PurchaseOrder {
  id: string;
  number: string;
  supplier_id: string;
  supplier_name: string | null;
  warehouse_id: string | null;
  status: PoStatus;
  expected_at: string | null;
  reference: string | null;
  notes: string | null;
  total_paisa: number;
  received_value_paisa: number;
  source: string;
  version: number;
  created_at: string;
  payable: Payable | null;
}

export interface PoLine {
  id: string;
  product_id: string;
  variant_id: string | null;
  description: string;
  quantity_ordered: number;
  quantity_received: number;
  quantity_rejected: number;
  unit_cost_paisa: number;
  update_cost: boolean;
  remaining: number;
}

export interface PoDetail {
  purchase_order: PurchaseOrder;
  lines: PoLine[];
  receipts: {
    id: string;
    received_at: string;
    supplier_reference: string | null;
    accepted_units: number;
    rejected_units: number;
    value_paisa: number;
    over_receipt_reason: string | null;
  }[];
  payments: { id: string; amount_paisa: number; paid_at: string; method: string; reference: string | null }[];
  can_manage: boolean;
  can_receive: boolean;
  can_pay: boolean;
}

export interface Warehouse {
  id: string | null;
  name: string;
  code: string;
  is_default: boolean;
  is_active: boolean;
}

export interface StockRow {
  product_id: string;
  variant_id: string | null;
  name: string;
  variant_name: string | null;
  sku: string | null;
  on_hand: number;
  low: boolean;
  low_stock_threshold: number | null;
  incoming: number;
  locations: { warehouse_id: string | null; name: string; quantity: number; is_default: boolean }[];
  damaged_30d: number;
  rejected_on_receipt_30d: number;
  cost_paisa: number;
}

export const STATUSES: PoStatus[] = ['DRAFT', 'ORDERED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED'];
export const REJECT_REASONS = ['DAMAGED', 'WRONG_ITEM', 'EXPIRED', 'OTHER'] as const;
export const METHODS = ['CASH', 'BKASH', 'NAGAD', 'ROCKET', 'BANK', 'CHEQUE', 'OTHER'] as const;

export function statusTone(status: PoStatus): 'neutral' | 'good' | 'warn' | 'bad' {
  if (status === 'RECEIVED') return 'good';
  if (status === 'ORDERED' || status === 'PARTIALLY_RECEIVED') return 'warn';
  if (status === 'CANCELLED') return 'bad';
  return 'neutral';
}

/** Taka typed by a person, as integer paisa. */
export function toPaisa(value: string): number {
  const [whole, fraction = ''] = value.trim().split('.');
  return Number(whole || 0) * 100 + Number((fraction + '00').slice(0, 2));
}

export function fromPaisa(paisa: number): string {
  return paisa % 100 === 0 ? String(paisa / 100) : (paisa / 100).toFixed(2);
}

/** The server's stable code as seller copy, when there is copy for it. */
export function problem(error: unknown, t: (key: StringKey) => string): string | null {
  if (!(error instanceof ApiError)) return null;
  const code = error.details?.code;
  if (typeof code === 'string' && `pr.err.${code}` in strings.en) return t(`pr.err.${code}` as StringKey);
  return error.message;
}

export function newKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}
