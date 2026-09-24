import type { Tone } from '@/components/ui';

export type RunStatus = 'QUEUED' | 'RUNNING' | 'WAITING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'SKIPPED';

export type Condition = { field: string; op: string; value?: string | number | string[] | null };
export type ConditionSet = { match: 'all' | 'any'; conditions: Condition[] };
export type ConditionGroup = ConditionSet & { groups?: ConditionSet[] };

export type ActionStep = { type: 'action'; id: string; action: string; config: Record<string, unknown> };
export type DelayStep = {
  type: 'delay';
  id: string;
  mode: 'duration' | 'until_time' | 'followup_due';
  minutes?: number;
  time?: string;
  day_offset?: 0 | 1;
  step?: string;
};
export type WaitStep = {
  type: 'wait_event';
  id: string;
  event: string;
  timeout_minutes: number;
  on_timeout: 'continue' | 'stop';
};
export type BranchStep = { type: 'branch'; id: string; conditions: ConditionGroup; then: Step[]; else: Step[] };
export type Step = ActionStep | DelayStep | WaitStep | BranchStep;

export interface Definition {
  trigger: string;
  conditions: ConditionGroup;
  steps: Step[];
}

export interface Workflow {
  id: string;
  name: string;
  trigger: string;
  enabled: boolean;
  published_version: number | null;
  recipe_key: string | null;
  draft: Definition | null;
  has_unpublished_changes: boolean;
  runs_7d?: Partial<Record<RunStatus, number>>;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDetail extends Workflow {
  published: Definition | null;
  versions: { number: number; published_at: string; published_by: string }[];
}

export interface Run {
  id: string;
  rule_id: string;
  workflow_name: string | null;
  version: number;
  trigger: string | null;
  order_id: string | null;
  customer_id: string | null;
  status: RunStatus;
  current_step: string | null;
  attempts: number;
  retryable: boolean | null;
  last_error: string | null;
  depth: number;
  source: string;
  next_attempt_at: string | null;
  waiting_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  created_at: string;
}

export interface RunDetail extends Run {
  steps: {
    step_id: string;
    kind: string;
    action: string | null;
    event: string | null;
    status: string;
    outcome: string | null;
    started_at: string;
    finished_at: string | null;
  }[];
  history: { id: string; step_id: string | null; status: string; error: string | null; created_at: string }[];
}

export interface FieldSpec {
  key: string;
  needs: string | null;
  kind: 'enum' | 'int' | 'text' | 'bool' | 'tag';
  ops: string[];
  values: string[];
}

export interface Catalog {
  workflow_triggers: { key: string; subject: string }[];
  workflow_actions: { key: string; needs: string | null }[];
  fields: FieldSpec[];
  subjects: Record<string, string[]>;
  wait_events: { key: string; subject: string }[];
  tags: { id: string; name: string }[];
  templates: { key: string; channel: string }[];
  couriers: string[];
  can_manage: boolean;
  can_operate: boolean;
}

export interface Metrics {
  days: number;
  executions: number;
  succeeded: number;
  failed: number;
  waiting: number;
  average_duration_seconds: number | null;
  top_failures: { reason: string | null; count: number }[];
}

export interface Recipe {
  key: string;
  name: { en: string; bn: string };
  trigger: string;
  needs: string[];
  installed: boolean;
}

export function statusTone(status: string): Tone {
  if (status === 'SUCCEEDED') return 'good';
  if (status === 'FAILED') return 'bad';
  if (status === 'WAITING' || status === 'QUEUED' || status === 'RUNNING') return 'warn';
  return 'neutral';
}

/** A short, stable step id: the idempotency key of the step's side effect. */
export function newId(prefix: string): string {
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${random}`.slice(0, 32);
}

export function emptyGroup(): ConditionGroup {
  return { match: 'all', conditions: [], groups: [] };
}

export function defaultConfig(action: string, catalog: Catalog | null, locale: 'en' | 'bn'): Record<string, unknown> {
  switch (action) {
    case 'SEND_TEMPLATE':
      return { template_key: catalog?.templates.find((t) => t.channel === 'EMAIL')?.key ?? 'order_update', locale, channel: 'EMAIL' };
    case 'CREATE_FOLLOWUP':
      return { text: '', due_hours: 24 };
    case 'ADD_TAG':
    case 'REMOVE_TAG':
      return { tag_id: catalog?.tags[0]?.id ?? '' };
    case 'SELLER_NOTIFICATION':
      return { title_en: '', title_bn: '', text_en: '', text_bn: '', audience: 'OPERATIONS' };
    case 'CREATE_TASK':
      return { text_en: '', text_bn: '', due_hours: 24 };
    case 'BOOK_COURIER':
      return { provider: catalog?.couriers.find((c) => c !== 'manual') ?? 'steadfast' };
    case 'SET_ORDER_LABEL':
    case 'TRIGGER_WEBHOOK':
      return { label: '' };
    case 'CREATE_TEAM_TASK':
      return { text_en: '', text_bn: '', due_hours: 24 };
    case 'CREATE_DRAFT_PO':
      return { quantity: 10 };
    default:
      return {};
  }
}

/** Every step in the tree, depth first. */
export function flatten(steps: Step[]): Step[] {
  return steps.flatMap((step) => (step.type === 'branch' ? [step, ...flatten(step.then), ...flatten(step.else)] : [step]));
}

/** Keys a subject kind can offer: an order also brings its customer. */
export function provides(catalog: Catalog | null, trigger: string): Set<string> {
  const subject = catalog?.workflow_triggers.find((t) => t.key === trigger)?.subject ?? 'shop';
  return new Set(catalog?.subjects[subject] ?? []);
}
