/**
 * Courier names as a seller knows them.
 *
 * Names stay English in both languages, as they do on mobile. One this build
 * has not met falls through to its own identifier rather than being hidden: an
 * unknown courier on a parcel is something support needs to see.
 */
export const LIVE_COURIERS = ['steadfast', 'pathao', 'redx'] as const;

export function courierName(provider: string | null | undefined, manualLabel = 'Manual'): string {
  switch (provider) {
    case 'steadfast':
      return 'Steadfast';
    case 'pathao':
      return 'Pathao';
    case 'redx':
      return 'RedX';
    case 'manual':
      return manualLabel;
    case null:
    case undefined:
    case '':
      return '—';
    default:
      return provider;
  }
}

/** A field on a provider's connect form, exactly as `GET /couriers/providers` declares it. */
export interface ConnectField {
  name: string;
  label_en: string;
  label_bn: string;
  secret: boolean;
  required: boolean;
  help_en: string | null;
  help_bn: string | null;
  input_type: string;
}

export interface ConnectForm {
  provider: string;
  display_name: string;
  fields: ConnectField[];
  supports_sandbox: boolean;
  requires_store: boolean;
  supports_store: boolean;
  requires_delivery_area: boolean;
  uses_webhook: boolean;
  webhook_secret_generated: boolean;
  supports_delivery_type: boolean;
  webhook_help_en: string | null;
  webhook_help_bn: string | null;
}

export interface ProviderInfo {
  provider: string;
  display_name: string;
  capabilities: Record<string, string>;
  enabled: boolean;
  fully_unverified: boolean;
  manual_fallback: string | null;
  verified_at: string | null;
  unknowns?: Record<string, string>;
  connect_form: ConnectForm | null;
}

export interface CourierAccount {
  id: string;
  provider: string;
  label: string | null;
  status: string;
  connected: boolean;
  needs_reconnect: boolean;
  /** `****abcd`. The only credential-derived value any client ever receives. */
  masked_identifier: string | null;
  last_verified_at: string | null;
  last_validation_result: string | null;
  last_validation_message: string | null;
  capabilities: Record<string, string>;
  reported_balance_paisa: number | null;
  reported_balance_at: string | null;
  config: Record<string, unknown>;
  webhook_configured: boolean;
}

export interface BookableCourier {
  provider: string;
  display_name: string;
  bookable: boolean;
  reason: string | null;
  requires_store: boolean;
  store_name: string | null;
  supports_delivery_type: boolean;
  requires_delivery_area: boolean;
}

export interface Quote {
  provider: string;
  available: boolean;
  delivery_fee_paisa: number | null;
  cod_fee_paisa: number | null;
  reason: string | null;
}

export interface BookingReport {
  provider: string;
  batch_id: string | null;
  booked: number;
  ambiguous: number;
  failed: number;
  items: {
    order_id: string;
    consignment_id: string | null;
    merchant_reference: string;
    outcome: string;
    tracking_code: string | null;
    error_code: string | null;
    message: string | null;
  }[];
}

export interface Tracking {
  attempts: {
    id: string;
    attempt_number: number;
    state: string;
    merchant_reference: string;
    provider_consignment_id: string | null;
    tracking_code: string | null;
    error_code: string | null;
    recovery_attempts: number;
    recovery_note: string | null;
    started_at: string;
    completed_at: string | null;
  }[];
  events: {
    kind: string;
    source: string;
    raw_status: string | null;
    normalized_status: string | null;
    status_undocumented: boolean;
    observed_at: string;
    last_seen_at: string;
    observation_count: number;
  }[];
}

/**
 * The capabilities a courier has actually been verified to support.
 *
 * Only `"true"` counts. `"unknown"` means the provider's documentation is
 * silent, which is not the same as supported.
 */
export function verifiedCapabilities(capabilities: Record<string, string>): string[] {
  return Object.entries(capabilities)
    .filter(([, value]) => value === 'true')
    .map(([key]) => key);
}
