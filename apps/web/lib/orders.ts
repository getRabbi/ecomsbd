/**
 * Order shapes the composer, paste detector and order drawer share.
 *
 * Mirrors `backend/app/api/v1/commerce_schemas.py`. Nothing here decides
 * anything: the parser, duplicate detection, the state machine and every
 * total belong to the API.
 */

export interface ParsedItem {
  name: string;
  quantity: number;
  size: string | null;
  color: string | null;
  unit_price_paisa: number | null;
}

export interface ParseResult {
  customer_name: string | null;
  phones: string[];
  selected_phone: string | null;
  address: string | null;
  items: ParsedItem[];
  cod_amount_paisa: number | null;
  notes: string | null;
  confidence: Record<string, number>;
  parser: string;
  warnings: string[];
  needs_phone_selection: boolean;
  is_low_confidence: boolean;
  source_text: string;
}

export interface DuplicateCandidate {
  order_id: string;
  order_number: string;
  status: string;
  cod_amount_paisa: number;
  hours_ago: number;
  reasons: string[];
  matching_item_names: string[];
  is_strong: boolean;
}

export interface DuplicateCheck {
  possible_duplicate: boolean;
  message: string;
  window_hours: number;
  candidates: DuplicateCandidate[];
}

export interface RiskCheck {
  found: boolean;
  customer_id: string | null;
  name: string | null;
  phone_masked: string | null;
  state: string;
  order_count: number;
  delivered_count: number;
  returned_count: number;
  cancelled_count: number;
  terminal_count: number;
  success_rate_basis_points: number | null;
  first_order_at: string | null;
  last_order_at: string | null;
  reasons: string[];
  checks_remaining: number | null;
  in_transit_count: number;
}

/** Everything the composer can start from: blank, a paste, or a chat draft. */
export interface OrderPrefill {
  phone?: string;
  phones?: string[];
  customer_name?: string;
  address?: string;
  items?: { name: string; quantity: number; unit_price_paisa: number | null; variant_label?: string | null }[];
  cod_amount_paisa?: number | null;
  note?: string;
  source_text?: string;
  channel?: 'MANUAL' | 'PASTE_PARSE';
  /** Confirm through the chat-order endpoint, so the draft is linked to the order. */
  chatDraftId?: string;
}

/**
 * Which status moves to offer as buttons.
 *
 * A hint mirroring the backend's `ORDER_TRANSITIONS`, used only to choose
 * buttons; the API re-checks every move and refuses an illegal one with a
 * reason. Fulfilment (`FULFILLMENT_STARTED`) and completion are reached
 * through booking/dispatch and the parcel outcome, never a bare status edit,
 * so they are not offered here.
 */
export const STATUS_MOVES: Record<string, string[]> = {
  DRAFT: ['CONFIRMED', 'CANCELLED'],
  CONFIRMED: ['PACKED', 'CANCELLED'],
  PACKED: ['CONFIRMED', 'CANCELLED'],
};

/** Whether the order's items and delivery details may still be edited. */
export function isEditable(status: string): boolean {
  return status === 'DRAFT' || status === 'CONFIRMED' || status === 'PACKED';
}

export const RETURN_REASONS = [
  'CUSTOMER_UNREACHABLE',
  'CUSTOMER_REFUSED',
  'WRONG_PRODUCT',
  'WRONG_SIZE',
  'DAMAGED',
  'DELAYED_DELIVERY',
  'CHANGED_MIND',
  'SUSPECTED_INVALID_ORDER',
  'COURIER_ISSUE',
  'MERCHANT_ISSUE',
  'OTHER',
] as const;

/** Taka typed by a seller → integer paisa, without a float in between. */
export function takaToPaisa(value: string): number | null {
  const trimmed = value.trim().replaceAll(',', '');
  if (!trimmed) return null;
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(trimmed);
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'));
}

/** Integer paisa → the plain taka text an input shows. */
export function paisaToTaka(paisa: number | null | undefined): string {
  if (paisa === null || paisa === undefined) return '';
  const whole = Math.trunc(paisa / 100);
  const fraction = Math.abs(paisa % 100);
  return fraction ? `${whole}.${String(fraction).padStart(2, '0')}` : String(whole);
}

/** A client request id (a UUID, as the API expects), so a double-submit creates one thing. */
export function newClientId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  const hex = Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20)}`;
}

/** The API's message for a failed action, or the error's own text. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
