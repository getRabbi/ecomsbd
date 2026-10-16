/**
 * Shop details as the seller edits them, and what is sent back to the API.
 *
 * The API owns validation (`ShopCreatePayload` / `ShopUpdatePayload`); this
 * file only shapes the request. Two checks run here as well, because the API
 * would otherwise accept the input and quietly do something else with it:
 * a one-letter name is refused with a generic error, and a pickup number the
 * phone normaliser cannot read is stored as *no number* rather than rejected.
 */

export const BUSINESS_CATEGORIES = [
  'CLOTHING',
  'ELECTRONICS',
  'COSMETICS',
  'FOOD',
  'HOME',
  'JEWELLERY',
  'BOOKS',
  'BABY',
  'OTHER',
] as const;

export type BusinessCategory = (typeof BUSINESS_CATEGORIES)[number];

/** `GET /v1/tenant`. */
export interface ShopDetails {
  id: string;
  name: string;
  business_category: string;
  status: string;
  timezone: string;
  currency: string;
  order_number_prefix: string;
  pickup_contact_name: string | null;
  pickup_address_raw: string | null;
  pickup_district: string | null;
  pickup_area: string | null;
  onboarding_step: string;
  onboarding_completed_at: string | null;
}

export interface ShopFormValues {
  name: string;
  business_category: string;
  pickup_contact_name: string;
  /** Write-only: the API never returns the stored number. Blank keeps it. */
  pickup_phone: string;
  pickup_address: string;
  pickup_district: string;
  pickup_area: string;
}

export type ShopFormError = 'name' | 'phone';

export function initialShopValues(shop: ShopDetails | null): ShopFormValues {
  return {
    name: shop?.name ?? '',
    business_category: shop?.business_category ?? 'OTHER',
    pickup_contact_name: shop?.pickup_contact_name ?? '',
    pickup_phone: '',
    pickup_address: shop?.pickup_address_raw ?? '',
    pickup_district: shop?.pickup_district ?? '',
    pickup_area: shop?.pickup_area ?? '',
  };
}

/** A Bangladeshi mobile number in any of the forms sellers type. */
export function looksLikeBdMobile(value: string): boolean {
  const compact = value.replace(/[\s\-().]/g, '');
  return /^(?:\+?88)?01[3-9]\d{8}$/.test(compact);
}

export function validateShop(values: ShopFormValues): ShopFormError[] {
  const errors: ShopFormError[] = [];
  if (values.name.trim().length < 2) {
    errors.push('name');
  }
  if (values.pickup_phone.trim() && !looksLikeBdMobile(values.pickup_phone)) {
    errors.push('phone');
  }
  return errors;
}

type Payload = Record<string, string>;

/** `POST /v1/tenants`: the name, plus whatever else was filled in. */
export function createPayload(values: ShopFormValues): Payload {
  const payload: Payload = {
    name: values.name.trim(),
    business_category: values.business_category,
  };
  const optional: [keyof ShopFormValues, string][] = [
    ['pickup_contact_name', 'pickup_contact_name'],
    ['pickup_phone', 'pickup_phone'],
    ['pickup_address', 'pickup_address'],
    ['pickup_district', 'pickup_district'],
    ['pickup_area', 'pickup_area'],
  ];
  for (const [field, key] of optional) {
    const value = values[field].trim();
    if (value) {
      payload[key] = value;
    }
  }
  return payload;
}

/**
 * `PATCH /v1/tenant`: only what changed.
 *
 * Sending every field would rewrite the audit trail with changes nobody made.
 * An emptied text field is sent as `""`, which the API stores as cleared. The
 * phone is sent only when a new one was typed, since a blank box means "keep
 * the hidden saved number", not "delete it".
 */
export function updatePayload(initial: ShopFormValues, values: ShopFormValues): Payload {
  const payload: Payload = {};
  if (values.name.trim() !== initial.name.trim()) {
    payload.name = values.name.trim();
  }
  if (values.business_category !== initial.business_category) {
    payload.business_category = values.business_category;
  }
  const text: [keyof ShopFormValues, string][] = [
    ['pickup_contact_name', 'pickup_contact_name'],
    ['pickup_address', 'pickup_address'],
    ['pickup_district', 'pickup_district'],
    ['pickup_area', 'pickup_area'],
  ];
  for (const [field, key] of text) {
    if (values[field].trim() !== initial[field].trim()) {
      payload[key] = values[field].trim();
    }
  }
  if (values.pickup_phone.trim()) {
    payload.pickup_phone = values.pickup_phone.trim();
  }
  return payload;
}
