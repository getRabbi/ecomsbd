import type { Page } from './api';
import type { StringKey } from './i18n';

export const segments = ['NEW', 'REPEAT', 'HIGH_VALUE', 'INACTIVE', 'SUCCESSFUL_REPEAT', 'REPEATED_RTO', 'FOLLOW_UP_DUE', 'ACTIVE_ORDERS'] as const;
export const crmKey = (code: string) => `crm.${code}` as StringKey;
export interface Tag { id: string; name: string }
export interface Value {
  total_order_value_paisa: number;
  delivered_revenue_paisa: number | null;
  measured_profit_paisa: number | null;
  average_delivered_order_paisa: number | null;
  measured_orders: number;
  measured_parcels: number;
  completed_parcels: number;
}
export interface Customer {
  id: string; name: string | null; phone_masked: string;
  order_count: number; delivered_count: number; returned_count: number; cancelled_count: number;
  success_rate_basis_points: number | null; first_order_at: string | null; last_order_at: string | null;
  active_orders: number; tags: Tag[]; segments: string[]; next_follow_up_at: string | null;
  value: Value | null; money_locked: string | null; can_write: boolean;
  definitions: Record<string, number | null>;
  addresses: { id: string; raw_address: string }[]; notes: string | null;
  risk: { state: string; reasons: string[] } | null;
}
export interface CustomerPage extends Page<Customer> {
  definitions: Record<string, number | null>; can_write: boolean; money_locked: string | null;
}
export interface Activity { id: string; created_at: string; kind: string; text: string | null; actor_name: string | null; actor_id: string | null; order_id: string | null }
export interface FollowUp { id: string; text: string; due_at: string; state: string; completed_at: string | null; author_name: string | null; assignee_name: string | null; created_by: string; assignee_id: string | null }
