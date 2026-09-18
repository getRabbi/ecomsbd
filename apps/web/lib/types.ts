/**
 * The API's shapes, as the dashboard reads them.
 *
 * Mirrors `backend/app/api/v1/commerce_schemas.py`. Fields are optional here
 * where the API may not send them, so a response that grows a field does not
 * break the client and one that lacks a field renders a dash rather than
 * `undefined`.
 *
 * Note what is **not** here: no courier API key, no decrypted secret, no
 * ciphertext. The API has no response that carries one, so the client has no
 * type that could hold one.
 */

export interface OrderSummary {
  id: string;
  order_number: string;
  customer_id: string | null;
  customer_name: string | null;
  customer_phone_masked: string | null;
  delivery_address_raw: string | null;
  delivery_district: string | null;
  delivery_area: string | null;
  status: string;
  channel: string;
  business_date: string;
  subtotal_paisa: number;
  discount_paisa: number;
  delivery_fee_paisa: number;
  cod_amount_paisa: number;
  note: string | null;
  created_at: string;

  /** Where the parcel is, in the consignment lifecycle. */
  fulfillment_state: string;
  risk_state: string;
  /**
   * Whether profit for this order has been calculated. A *state*, not an
   * amount: the API does not return a per-order profit figure yet, and a
   * dashboard must not compute one of its own.
   */
  profit_state: string;

  /**
   * The courier this order's live parcel is with, and its tracking code.
   * `null` for an unbooked order — a fact, not a missing value.
   */
  courier_provider: string | null;
  tracking_code: string | null;
}

export interface OrderItem {
  id: string;
  product_id: string | null;
  name: string;
  quantity: number;
  unit_price_paisa: number;
  line_total_paisa?: number;
}

export interface OrderDetail extends OrderSummary {
  items: OrderItem[];
  source_text: string | null;
  estimated_item_cost_paisa: number;
}
