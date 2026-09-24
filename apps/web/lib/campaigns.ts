import type { StringKey } from './i18n';

export type CampaignStatus = 'DRAFT' | 'SCHEDULED' | 'SENDING' | 'PAUSED' | 'COMPLETED' | 'CANCELLED' | 'ACTIVE';
export type Channel = 'EMAIL' | 'WHATSAPP';
export type Flow = 'WIN_BACK' | 'REPEAT_NUDGE' | 'INACTIVE';

export interface Audience {
  segment?: string | null;
  tag_id?: string | null;
  min_orders?: number | null;
  max_orders?: number | null;
  last_order_before_days?: number | null;
  last_order_within_days?: number | null;
}

export interface Campaign {
  id: string;
  name: string;
  kind: 'ONE_OFF' | 'FLOW';
  flow: Flow | null;
  flow_days: number | null;
  channel: Channel;
  template_key: string;
  locale: 'bn' | 'en';
  audience: Audience;
  status: CampaignStatus;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  last_run_at: string | null;
  last_error: string | null;
  rate_per_minute: number;
  frequency_cap_hours: number;
  attribution_days: number;
  total_recipients: number;
  version: number;
  created_at: string;
  messages?: Record<string, number>;
}

export interface Template {
  key: string;
  purpose: 'TRANSACTIONAL' | 'MARKETING';
  channel: Channel;
  subject_en: string;
  subject_bn: string;
  body_en: string;
  body_bn: string;
  provider_name: string | null;
  provider_status: string | null;
  provider_category: string | null;
  builtin: boolean;
}

export interface Catalog {
  segments: string[];
  tags: { id: string; name: string }[];
  templates: Template[];
  flows: Record<Flow, { default_days: number; min_days: number; max_days: number }>;
  limits: { max_recipients: number; flow_daily_limit: number; quiet_hours: [number, number]; flow_hour: number };
  can_manage: boolean;
  can_pause: boolean;
}

export interface Estimate {
  matched: number;
  over_limit: boolean;
  limit: number;
  reachable: number;
  excluded: Record<string, number>;
}

export interface Analytics {
  recipients: { total: number; PENDING: number; QUEUED: number; SKIPPED: number; skipped_by: Record<string, number> };
  messages: Record<string, number>;
  sent: number;
  delivered: number | null;
  read: number | null;
  failed: number;
  reports: string[];
  errors: { code: string; count: number }[];
  opt_outs: number;
  orders_after: { window_days: number; customers: number; orders: number; order_value_paisa: number | null };
}

export interface Detail {
  campaign: Campaign;
  blocker: string | null;
  analytics: Analytics;
}

export interface Recipient {
  id: string;
  customer_id: string;
  customer_name: string | null;
  phone_masked: string;
  status: 'PENDING' | 'QUEUED' | 'SKIPPED';
  skip_reason: string | null;
  message_status: string | null;
}

export const RUNNING: CampaignStatus[] = ['SCHEDULED', 'SENDING', 'ACTIVE'];

export function statusTone(status: CampaignStatus): 'neutral' | 'good' | 'warn' | 'bad' {
  if (status === 'SENDING' || status === 'ACTIVE' || status === 'COMPLETED') return 'good';
  if (status === 'PAUSED' || status === 'SCHEDULED') return 'warn';
  if (status === 'CANCELLED') return 'bad';
  return 'neutral';
}

/** A server code as seller copy, falling back to a generic line rather than the raw code. */
export function codeKey(prefix: string, code: string | null | undefined, known: (key: string) => boolean): StringKey | null {
  if (!code) return null;
  const key = `${prefix}.${code}`;
  return known(key) ? (key as StringKey) : null;
}
