import { ApiError } from './api';
import type { StringKey } from './i18n';

/**
 * Shapes and rules for the import wizard.
 *
 * Everything that decides an import — parsing, validation, deduplication,
 * the commit itself — happens on the API. This file only knows enough to lay
 * out the mapping form and to phrase what the API reported.
 */

export type ImportTemplate = 'ORDERS' | 'PRODUCTS';

export type ImportStatus =
  | 'UPLOADED'
  | 'MAPPED'
  | 'VALIDATED'
  | 'COMMITTING'
  | 'COMMITTED'
  | 'FAILED'
  | 'CANCELLED';

export type ImportRowStatus =
  | 'PENDING'
  | 'READY'
  | 'WARNING'
  | 'DUPLICATE'
  | 'INVALID'
  | 'CREATED'
  | 'SKIPPED';

export interface ImportBatch {
  id: string;
  template: ImportTemplate | 'PAYOUT_STATEMENT';
  status: ImportStatus;
  original_filename: string;
  detected_headers: string[];
  column_mapping: Record<string, string>;
  row_count: number;
  ready_count: number;
  warning_count: number;
  duplicate_count: number;
  invalid_count: number;
  created_count: number;
  can_commit: boolean;
  dry_run_at: string | null;
  failure_reason: string | null;
  created_at: string;
  committed_at: string | null;
}

export interface ImportCommitResult {
  import_batch: ImportBatch;
  created_count: number;
  skipped_count: number;
  queued: boolean;
}

export interface RowIssue {
  field: string;
  message: string;
}

export interface ImportRow {
  row_number: number;
  status: ImportRowStatus;
  raw: Record<string, unknown>;
  parsed: Record<string, unknown>;
  errors: RowIssue[];
  warnings: RowIssue[];
}

export interface SavedMapping {
  id: string;
  name: string;
  template: string;
  mapping: Record<string, string>;
  source_headers: string[];
  use_count: number;
  last_used_at: string | null;
}

export interface TemplateField {
  key: string;
  label: StringKey;
  required: boolean;
}

/**
 * The fields each template understands, in the order the form shows them.
 *
 * Mirrors `backend/app/imports/templates.py`. The API is still the judge: a
 * field it does not know is ignored and a missing required one is refused, so
 * drift here shows up as a validation message, never as a wrong import.
 */
export const TEMPLATE_FIELDS: Record<ImportTemplate, TemplateField[]> = {
  ORDERS: [
    { key: 'phone', label: 'impf.phone', required: true },
    { key: 'product', label: 'impf.product', required: true },
    { key: 'customer_name', label: 'impf.customer_name', required: false },
    { key: 'address', label: 'impf.address', required: false },
    { key: 'district', label: 'impf.district', required: false },
    { key: 'area', label: 'impf.area', required: false },
    { key: 'quantity', label: 'impf.quantity', required: false },
    { key: 'amount', label: 'impf.amount', required: false },
    { key: 'note', label: 'impf.note', required: false },
  ],
  PRODUCTS: [
    { key: 'name', label: 'impf.name', required: true },
    { key: 'sku', label: 'impf.sku', required: false },
    { key: 'cost', label: 'impf.cost', required: false },
    { key: 'price', label: 'impf.price', required: false },
    { key: 'stock', label: 'impf.stock', required: false },
    { key: 'description', label: 'impf.description', required: false },
  ],
};

/** Matches the API's own limit, so an oversized file is caught before upload. */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

export const ACCEPTED_EXTENSIONS = ['.csv', '.xlsx'];

/** How often a background import is re-read while the seller watches. */
export const POLL_INTERVAL_MS = 2500;

export function fieldLabel(template: string, key: string): StringKey | null {
  if (key === '_row') {
    return 'impf._row';
  }
  const fields = TEMPLATE_FIELDS[template as ImportTemplate] ?? [];
  return fields.find((field) => field.key === key)?.label ?? null;
}

export function hasAcceptedExtension(name: string): boolean {
  const lowered = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => lowered.endsWith(extension));
}

/** Import states a seller can pick back up from history. */
export function isResumable(batch: ImportBatch): boolean {
  return ['UPLOADED', 'MAPPED', 'VALIDATED', 'COMMITTING'].includes(batch.status);
}

type Translate = (key: StringKey, vars?: Record<string, string | number>) => string;

/**
 * What to tell the seller about a failed call.
 *
 * A 4xx carries copy the API wrote for a seller — "these columns still need
 * mapping", "this file has 6,000 rows" — so it is shown. A 5xx or a network
 * failure gets a plain sentence of our own: whatever text came back with it
 * was not written for a seller and is not repeated to one.
 */
export function describeError(caught: unknown, t: Translate): string {
  if (caught instanceof ApiError) {
    if (caught.isPermissionFailure) {
      return t('err.forbidden');
    }
    if (caught.status === 401) {
      return t('auth.needed');
    }
    if (caught.status >= 400 && caught.status < 500) {
      return caught.message;
    }
    return t('impw.serverError');
  }
  return t('err.offline');
}
