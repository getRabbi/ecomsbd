'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';

import { Chip, Drawer, ErrorState, Row } from '@/components/ui';
import { api } from '@/lib/api';
import {
  courierName,
  type BookableCourier,
  type BookingReport,
  type Quote,
  type Tracking,
} from '@/lib/couriers';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import {
  RETURN_REASONS,
  STATUS_MOVES,
  errorMessage,
  isEditable,
  paisaToTaka,
  takaToPaisa,
} from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import {
  describeFulfillment,
  describeOrderStatus,
  describeProfit,
  describeRisk,
} from '@/lib/states';
import { useApi } from '@/lib/useApi';

interface OrderItem {
  id: string;
  product_id: string | null;
  product_name: string;
  sku: string | null;
  variant_label: string | null;
  quantity: number;
  unit_price_paisa: number;
  discount_paisa: number;
  line_total_paisa: number;
}

interface OrderDetail {
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
  version: number;
  created_at: string;
  fulfillment_state: string;
  risk_state: string;
  profit_state: string;
  courier_provider: string | null;
  tracking_code: string | null;
  items: OrderItem[];
  source_text: string | null;
  estimated_item_cost_paisa: number;
  consignment_id: string | null;
  consignment_status: string | null;
  return_pending_units: number;
  review_hold?: { reason: string; at: string } | null;
}

/** Parcel states in which a return can still be asked of the courier. */
const RETURNABLE = new Set(['BOOKED', 'PICKED_UP', 'IN_TRANSIT', 'OUT_FOR_DELIVERY']);
/** Parcel states a manual (self-delivered) parcel can still be closed from. */
const MANUAL_OPEN = new Set(['BOOKED', 'PICKED_UP', 'IN_TRANSIT', 'OUT_FOR_DELIVERY']);

/**
 * The full order, and everything a seller can do with it from a desk.
 *
 * Every action is a call to the canonical endpoint — status moves through
 * `PATCH /orders/{id}`, booking through the courier booking service, manual
 * dispatch and outcomes through consignments — so the state machine, stock
 * movements, receivables and audit stay the backend's. Buttons are offered
 * from hints; the API re-checks every one and its refusal is shown verbatim.
 */
export function OrderPanel({
  orderId,
  onClose,
  onChanged,
}: {
  orderId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const order = useApi<OrderDetail>(`/orders/${orderId}`);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [editing, setEditing] = useState(false);

  const data = order.data;

  async function run(work: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      await work();
      if (done) setNotice(done);
      order.reload();
      onChanged();
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function move(status: string, reason?: string) {
    if (!data) return;
    void run(
      () => api.patch(`/orders/${data.id}`, { status, cancellation_reason: reason || undefined }),
      t('od.moved', { status: describeOrderStatus(status, locale).label }),
    );
  }

  if (order.error && !data) {
    return (
      <Drawer title={t('orders.detail')} onClose={onClose}>
        <ErrorState error={order.error} onRetry={order.reload} />
      </Drawer>
    );
  }

  const fulfillment = data ? describeFulfillment(data.fulfillment_state, locale) : null;
  const unbooked = data ? data.fulfillment_state === 'NOT_BOOKED' || data.fulfillment_state === 'FAILED' : false;
  const bookable = data ? unbooked && (data.status === 'CONFIRMED' || data.status === 'PACKED') : false;
  const parcelState = data?.consignment_status ?? null;
  const moves = data ? (STATUS_MOVES[data.status] ?? []) : [];

  return (
    <Drawer title={data ? `${t('orders.order')} ${data.order_number}` : t('orders.detail')} wide onClose={onClose}>
      {!data ? <p className="card__hint">{t('common.loading')}</p> : null}
      {data ? (
        <>
          {data.review_hold ? (
            <ReviewHold orderId={data.id} reason={data.review_hold.reason} onReleased={() => order.reload()} />
          ) : null}

          <div className="toolbar" style={{ padding: '0 0 10px', flexWrap: 'wrap' }}>
            <Chip {...describeOrderStatus(data.status, locale)} />
            {fulfillment ? <Chip label={fulfillment.label} tone={fulfillment.tone} /> : null}
            <Chip {...describeRisk(data.risk_state, locale)} />
            <span className="table__sub">{formatDate(data.created_at, { locale, withTime: true })} · {data.channel.replaceAll('_', ' ').toLowerCase()}</span>
          </div>

          {notice ? <div className="notice notice--good">{notice}</div> : null}
          {failure ? <p className="formerror" role="alert">{failure}</p> : null}

          {/* ---------------------------------------------------- lifecycle */}
          {can('order.write') && moves.length > 0 ? (
            <div className="toolbar" style={{ padding: '0 0 10px', flexWrap: 'wrap' }}>
              {moves
                .filter((status) => status !== 'CANCELLED')
                .map((status) => (
                  <button key={status} type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => move(status)}>
                    {t(`od.to.${status}` as StringKey)}
                  </button>
                ))}
              {isEditable(data.status) ? (
                <button type="button" className="btn btn--sm" disabled={busy} onClick={() => setEditing((value) => !value)}>
                  {t('od.edit')}
                </button>
              ) : null}
              {moves.includes('CANCELLED') && can('order.cancel') ? (
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setCancelling((value) => !value)}>
                  {t('od.cancel')}
                </button>
              ) : null}
            </div>
          ) : null}

          {cancelling ? (
            <form
              className="card"
              style={{ padding: 10, marginBottom: 12 }}
              onSubmit={(event) => {
                event.preventDefault();
                move('CANCELLED', cancelReason.trim());
                setCancelling(false);
              }}
            >
              <label className="field">
                <span className="field__label">{t('od.cancelReason')}</span>
                <input className="input" maxLength={200} value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} />
              </label>
              <p className="card__hint">{t('od.cancelHint')}</p>
              <button type="submit" className="btn btn--sm" disabled={busy}>
                {t('od.confirmCancel')}
              </button>
            </form>
          ) : null}

          {editing && isEditable(data.status) ? (
            <EditDetails order={data} onSaved={() => { setEditing(false); order.reload(); onChanged(); }} />
          ) : null}

          {/* ---------------------------------------------------- customer */}
          <h3 className="card__title" style={{ marginTop: 6 }}>{t('orders.customer')}</h3>
          <Row
            label={t('oc.name')}
            value={
              data.customer_id ? (
                <Link href={`/customers/${data.customer_id}`}>{data.customer_name ?? data.customer_phone_masked ?? '—'}</Link>
              ) : (
                data.customer_name ?? '—'
              )
            }
          />
          <Row label={t('oc.phone')} value={data.customer_phone_masked ?? '—'} />
          <Row label={t('oc.address')} value={[data.delivery_address_raw, data.delivery_area, data.delivery_district].filter(Boolean).join(', ') || '—'} />

          {/* ---------------------------------------------------- items + totals */}
          <h3 className="card__title" style={{ marginTop: 14 }}>{t('orders.items')}</h3>
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('oc.product')}</th>
                  <th className="num">{t('oc.quantity')}</th>
                  <th className="num">{t('oc.unitPrice')}</th>
                  <th className="num">{t('money.total')}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="table__primary">{item.product_name}</div>
                      <div className="table__sub">{[item.variant_label, item.sku].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td className="num">{item.quantity}</td>
                    <td className="num">{formatPaisa(item.unit_price_paisa, { locale })}</td>
                    <td className="num">{formatPaisa(item.line_total_paisa, { locale })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Row label={t('od.subtotal')} value={formatPaisa(data.subtotal_paisa, { locale })} />
          {data.discount_paisa ? <Row label={t('oc.discount')} value={`-${formatPaisa(data.discount_paisa, { locale })}`} /> : null}
          <Row label={t('oc.deliveryFee')} value={formatPaisa(data.delivery_fee_paisa, { locale })} />
          <Row label={<strong>{t('orders.cod')}</strong>} value={<strong>{formatPaisa(data.cod_amount_paisa, { locale })}</strong>} />
          {can('money.view') ? (
            <Row
              label={t('orders.profit')}
              value={
                <>
                  <Chip {...describeProfit(data.profit_state, locale)} />{' '}
                  <span className="table__sub">{t('od.itemCost', { amount: formatPaisa(data.estimated_item_cost_paisa, { locale }) })}</span>
                </>
              }
            />
          ) : null}
          {data.note ? <Row label={t('oc.note')} value={data.note} /> : null}

          {/* ---------------------------------------------------- courier */}
          <h3 className="card__title" style={{ marginTop: 14 }}>{t('orders.courier')}</h3>
          <Row
            label={t('orders.courier')}
            value={data.courier_provider ? courierName(data.courier_provider, t('rv.manualCourier')) : t('orders.noCourier')}
          />
          {data.tracking_code ? (
            <Row
              label={t('od.tracking')}
              value={
                <>
                  {data.tracking_code}{' '}
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => void navigator.clipboard?.writeText(data.tracking_code ?? '')}>
                    {t('od.copy')}
                  </button>
                </>
              }
            />
          ) : null}

          {bookable && can('order.book') ? (
            <CourierBooking order={data} onBooked={() => { order.reload(); onChanged(); }} />
          ) : null}

          {bookable && can('order.book') ? (
            <ManualDispatch order={data} busy={busy} onDispatch={(tracking) =>
              run(() => api.post(`/consignments/orders/${data.id}/dispatch`, { provider: 'manual', tracking_code: tracking || undefined }), t('od.dispatched'))
            } />
          ) : null}

          {data.consignment_id ? (
            <TrackingPanel consignmentId={data.consignment_id} unknown={data.fulfillment_state === 'BOOKING_UNKNOWN'} onResolved={() => { order.reload(); onChanged(); }} />
          ) : null}

          {data.consignment_id && data.courier_provider === 'manual' && parcelState && MANUAL_OPEN.has(parcelState) && can('order.book') ? (
            <ManualOutcome consignmentId={data.consignment_id} busy={busy} run={run} />
          ) : null}

          {data.consignment_id && data.courier_provider && data.courier_provider !== 'manual' && parcelState && RETURNABLE.has(parcelState) && can('order.cancel') ? (
            <ReturnRequest consignmentId={data.consignment_id} busy={busy} run={run} />
          ) : null}

          {data.consignment_id && data.return_pending_units > 0 && can('inventory.adjust') ? (
            <div className="card" style={{ padding: 10, marginTop: 10 }}>
              <strong>{t('od.returnArrived', { count: data.return_pending_units })}</strong>
              <p className="card__hint">{t('od.returnArrivedHint')}</p>
              <div className="toolbar" style={{ padding: 0 }}>
                <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void run(() => api.post(`/consignments/${data.consignment_id}/return-receipt`, { decision: 'RESTOCK_ALL' }), t('od.restocked'))}>
                  {t('od.restockAll')}
                </button>
                <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(() => api.post(`/consignments/${data.consignment_id}/return-receipt`, { decision: 'RESTOCK_NONE' }), t('od.writtenOff'))}>
                  {t('od.restockNone')}
                </button>
              </div>
            </div>
          ) : null}

          {data.source_text ? (
            <details style={{ marginTop: 14 }}>
              <summary className="card__hint">{t('oc.original')}</summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}>{data.source_text}</pre>
            </details>
          ) : null}
        </>
      ) : null}
    </Drawer>
  );
}

/** Delivery details, items' money fields and the note — while the order is still editable. */
function EditDetails({ order, onSaved }: { order: OrderDetail; onSaved: () => void }) {
  const { t } = useSession();
  const [address, setAddress] = useState(order.delivery_address_raw ?? '');
  const [district, setDistrict] = useState(order.delivery_district ?? '');
  const [area, setArea] = useState(order.delivery_area ?? '');
  const [cod, setCod] = useState(paisaToTaka(order.cod_amount_paisa));
  const [fee, setFee] = useState(paisaToTaka(order.delivery_fee_paisa));
  const [discount, setDiscount] = useState(paisaToTaka(order.discount_paisa));
  const [note, setNote] = useState(order.note ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    const codPaisa = takaToPaisa(cod);
    const feePaisa = takaToPaisa(fee || '0');
    const discountPaisa = takaToPaisa(discount || '0');
    if (codPaisa === null || feePaisa === null || discountPaisa === null) {
      setError(t('oc.badAmount'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/orders/${order.id}`, {
        address,
        district,
        area,
        note,
        cod_amount_paisa: codPaisa,
        delivery_fee_paisa: feePaisa,
        discount_paisa: discountPaisa,
        expected_version: order.version,
      });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" style={{ padding: 10, marginBottom: 12 }} onSubmit={save}>
      <div className="formgrid">
        <label className="field formgrid__full">
          <span className="field__label">{t('oc.address')}</span>
          <textarea className="textarea" rows={2} maxLength={1000} value={address} onChange={(event) => setAddress(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.district')}</span>
          <input className="input" maxLength={80} value={district} onChange={(event) => setDistrict(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.area')}</span>
          <input className="input" maxLength={120} value={area} onChange={(event) => setArea(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('orders.cod')}</span>
          <input className="input" inputMode="decimal" value={cod} onChange={(event) => setCod(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.deliveryFee')}</span>
          <input className="input" inputMode="decimal" value={fee} onChange={(event) => setFee(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.discount')}</span>
          <input className="input" inputMode="decimal" value={discount} onChange={(event) => setDiscount(event.target.value)} />
        </label>
        <label className="field formgrid__full">
          <span className="field__label">{t('oc.note')}</span>
          <textarea className="textarea" rows={2} maxLength={4000} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
      </div>
      {error ? <p className="formerror">{error}</p> : null}
      <p className="card__hint">{t('od.editHint')}</p>
      <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
        {busy ? t('set.saving') : t('set.save')}
      </button>
    </form>
  );
}

/**
 * Courier choice, side by side: which couriers can take this order, what each
 * actually quotes (only where the provider's quote endpoint is verified), and
 * a booking. Nothing is estimated here; an unavailable quote says so.
 */
function CourierBooking({ order, onBooked }: { order: OrderDetail; onBooked: () => void }) {
  const { t, locale } = useSession();
  const couriers = useApi<BookableCourier[]>('/couriers/bookable');
  const [quotes, setQuotes] = useState<Record<string, Quote | string>>({});
  const [chosen, setChosen] = useState('');
  const [deliveryType, setDeliveryType] = useState('');
  const [areaId, setAreaId] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<BookingReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = couriers.data?.find((row) => row.provider === chosen) ?? null;
  const areas = useApi<{ id: string; name: string; post_code: string | null }[]>(
    selected?.requires_delivery_area ? `/couriers/accounts/${selected.provider}/areas` : null,
    { district_name: order.delivery_district || undefined },
  );

  async function quote(provider: string) {
    try {
      const result = await api.get<Quote>(`/couriers/orders/${order.id}/quote`, {
        provider,
        delivery_area_id: provider === chosen && areaId ? Number(areaId) : undefined,
      });
      setQuotes((current) => ({ ...current, [provider]: result }));
    } catch (caught) {
      setQuotes((current) => ({ ...current, [provider]: errorMessage(caught) }));
    }
  }

  async function book(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const area = areas.data?.find((row) => row.id === areaId);
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<BookingReport>(
        `/couriers/orders/${order.id}/book`,
        {
          note: note.trim() || undefined,
          delivery_type: deliveryType === '' ? undefined : Number(deliveryType),
          delivery_area_id: area ? Number(area.id) : undefined,
          delivery_area_name: area ? area.name : undefined,
        },
        { provider: selected.provider },
      );
      setReport(result);
      onBooked();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (couriers.error) return <ErrorState error={couriers.error} onRetry={couriers.reload} />;
  const rows = couriers.data ?? [];

  return (
    <div className="card" style={{ padding: 10, marginTop: 10 }}>
      <strong>{t('od.bookTitle')}</strong>
      {rows.length === 0 && !couriers.loading ? (
        <p className="card__hint">
          {t('od.noCouriers')} <Link href="/couriers">{t('nav.couriers')}</Link>
        </p>
      ) : null}
      {rows.length > 0 ? (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('cour.provider')}</th>
                <th>{t('cour.state')}</th>
                <th className="num">{t('od.deliveryCharge')}</th>
                <th className="num">{t('od.codCharge')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const result = quotes[row.provider];
                return (
                  <tr key={row.provider} data-selected={chosen === row.provider}>
                    <td>
                      <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <input type="radio" name="courier" disabled={!row.bookable} checked={chosen === row.provider} onChange={() => setChosen(row.provider)} />
                        {row.display_name}
                      </label>
                      {row.store_name ? <div className="table__sub">{t('cour.store')}: {row.store_name}</div> : null}
                    </td>
                    <td>{row.bookable ? <Chip label={t('od.canBook')} tone="good" /> : <span className="table__sub">{row.reason ?? t('cour.notConnected')}</span>}</td>
                    <td className="num">{typeof result === 'object' && result.available ? formatPaisa(result.delivery_fee_paisa, { locale }) : '—'}</td>
                    <td className="num">{typeof result === 'object' && result.available ? formatPaisa(result.cod_fee_paisa, { locale }) : '—'}</td>
                    <td>
                      {row.bookable ? (
                        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void quote(row.provider)}>
                          {t('od.quote')}
                        </button>
                      ) : null}
                      {typeof result === 'string' ? <div className="table__sub">{result}</div> : null}
                      {typeof result === 'object' && !result.available ? <div className="table__sub">{result.reason ?? t('od.noQuote')}</div> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="card__hint">{t('od.quoteNote')}</p>

      {selected ? (
        <form onSubmit={book}>
          <div className="formgrid">
            {selected.supports_delivery_type ? (
              <label className="field">
                <span className="field__label">{t('od.deliveryType')}</span>
                <select className="select" value={deliveryType} onChange={(event) => setDeliveryType(event.target.value)}>
                  <option value="">{t('od.default')}</option>
                  <option value="0">{t('od.home')}</option>
                  <option value="1">{t('od.point')}</option>
                </select>
              </label>
            ) : null}
            {selected.requires_delivery_area ? (
              <label className="field">
                <span className="field__label">{t('od.deliveryArea')} *</span>
                <select className="select" required value={areaId} onChange={(event) => setAreaId(event.target.value)}>
                  <option value="">{areas.loading ? t('common.loading') : t('od.chooseArea')}</option>
                  {(areas.data ?? []).map((area) => (
                    <option key={area.id} value={area.id}>
                      {area.name}
                      {area.post_code ? ` (${area.post_code})` : ''}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="field formgrid__full">
              <span className="field__label">{t('od.courierNote')}</span>
              <input className="input" maxLength={400} value={note} onChange={(event) => setNote(event.target.value)} />
            </label>
          </div>
          {error ? <p className="formerror">{error}</p> : null}
          <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
            {busy ? t('od.booking') : t('od.bookWith', { courier: selected.display_name })}
          </button>
        </form>
      ) : null}

      {report ? <BookingOutcome report={report} /> : null}
    </div>
  );
}

/** A booking report in the provider's own counts — an ambiguous booking is neither success nor failure. */
export function BookingOutcome({ report }: { report: BookingReport }) {
  const { t } = useSession();
  return (
    <div className={`notice ${report.failed ? 'notice--warn' : 'notice--good'}`} style={{ display: 'block', marginTop: 10 }}>
      <strong>{t('od.bookReport', { booked: report.booked, ambiguous: report.ambiguous, failed: report.failed })}</strong>
      {report.ambiguous ? <p className="card__hint">{t('od.ambiguousHint')}</p> : null}
      {report.items
        .filter((item) => item.outcome !== 'BOOKED')
        .slice(0, 20)
        .map((item) => (
          <p key={item.order_id} className="card__hint" style={{ margin: '2px 0' }}>
            {item.merchant_reference}: {item.message ?? item.error_code ?? item.outcome}
          </p>
        ))}
    </div>
  );
}

function ManualDispatch({ order, busy, onDispatch }: { order: OrderDetail; busy: boolean; onDispatch: (tracking: string) => Promise<void> | void }) {
  const { t } = useSession();
  const [open, setOpen] = useState(false);
  const [tracking, setTracking] = useState('');
  void order;
  return (
    <div style={{ marginTop: 8 }}>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen((value) => !value)}>
        {t('od.manualDispatch')}
      </button>
      {open ? (
        <form
          className="card"
          style={{ padding: 10, marginTop: 6 }}
          onSubmit={(event) => {
            event.preventDefault();
            void onDispatch(tracking.trim());
            setOpen(false);
          }}
        >
          <p className="card__hint">{t('od.manualDispatchHint')}</p>
          <label className="field">
            <span className="field__label">{t('od.tracking')}</span>
            <input className="input" maxLength={120} value={tracking} onChange={(event) => setTracking(event.target.value)} />
          </label>
          <button type="submit" className="btn btn--sm" disabled={busy}>
            {t('od.markDispatched')}
          </button>
        </form>
      ) : null}
    </div>
  );
}

/** Close a self-delivered parcel: delivered, or returned with an optional reason. */
function ManualOutcome({ consignmentId, busy, run }: { consignmentId: string; busy: boolean; run: (work: () => Promise<unknown>, done?: string) => Promise<void> }) {
  const { t } = useSession();
  const [reason, setReason] = useState('');
  return (
    <div className="card" style={{ padding: 10, marginTop: 10 }}>
      <strong>{t('od.manualOutcome')}</strong>
      <div className="toolbar" style={{ padding: '6px 0 0', flexWrap: 'wrap' }}>
        <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void run(() => api.post(`/consignments/${consignmentId}/outcome`, { status: 'DELIVERED' }), t('od.markedDelivered'))}>
          {t('od.markDelivered')}
        </button>
        <select className="select" value={reason} onChange={(event) => setReason(event.target.value)} aria-label={t('od.returnReason')}>
          <option value="">{t('od.reasonUnknown')}</option>
          {RETURN_REASONS.map((value) => (
            <option key={value} value={value}>
              {t(`od.reason.${value}` as StringKey)}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(() => api.post(`/consignments/${consignmentId}/outcome`, { status: 'RETURNED', return_reason: reason || undefined }), t('od.markedReturned'))}>
          {t('od.markReturned')}
        </button>
      </div>
    </div>
  );
}

function ReturnRequest({ consignmentId, busy, run }: { consignmentId: string; busy: boolean; run: (work: () => Promise<unknown>, done?: string) => Promise<void> }) {
  const { t } = useSession();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  return (
    <div style={{ marginTop: 8 }}>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen((value) => !value)}>
        {t('od.requestReturn')}
      </button>
      {open ? (
        <form
          className="card"
          style={{ padding: 10, marginTop: 6 }}
          onSubmit={(event) => {
            event.preventDefault();
            void run(() => api.post(`/couriers/consignments/${consignmentId}/return`, { reason: reason.trim() || undefined }), t('od.returnRequested'));
            setOpen(false);
          }}
        >
          <p className="card__hint">{t('od.requestReturnHint')}</p>
          <label className="field">
            <span className="field__label">{t('od.returnReason')}</span>
            <input className="input" maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <button type="submit" className="btn btn--sm" disabled={busy}>
            {t('od.sendReturn')}
          </button>
        </form>
      ) : null}
    </div>
  );
}

/**
 * The parcel's history from the courier, and — for a booking the provider
 * never confirmed — the one place a person records what really happened.
 */
function TrackingPanel({ consignmentId, unknown, onResolved }: { consignmentId: string; unknown: boolean; onResolved: () => void }) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const [open, setOpen] = useState(unknown);
  const tracking = useApi<Tracking>(open ? `/couriers/consignments/${consignmentId}/tracking` : null);
  const [exists, setExists] = useState<'yes' | 'no' | ''>('');
  const [reason, setReason] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pending = tracking.data?.attempts.find((attempt) => attempt.state === 'UNKNOWN' || attempt.state === 'BOOKING_UNKNOWN' || attempt.state === 'AMBIGUOUS');

  async function resolve(event: FormEvent) {
    event.preventDefault();
    if (!pending || !exists) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/couriers/booking-attempts/${pending.id}/resolve`, {
        parcel_exists: exists === 'yes',
        reason: reason.trim(),
        tracking_code: exists === 'yes' && code.trim() ? code.trim() : undefined,
      });
      tracking.reload();
      onResolved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 10 }}>
      {unknown ? (
        <div className="notice notice--warn" style={{ display: 'block' }}>
          <strong>{t('od.unknownTitle')}</strong>
          <p className="card__hint" style={{ margin: '4px 0 0' }}>{t('od.unknownHint')}</p>
        </div>
      ) : null}
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen((value) => !value)}>
        {open ? t('od.hideTracking') : t('od.showTracking')}
      </button>
      {open && tracking.error ? <ErrorState error={tracking.error} onRetry={tracking.reload} /> : null}
      {open && tracking.data ? (
        <>
          {tracking.data.events.length === 0 ? <p className="card__hint">{t('od.noEvents')}</p> : null}
          {tracking.data.events.map((event) => {
            const state = describeFulfillment(event.normalized_status, locale);
            return (
              <Row
                key={`${event.observed_at}-${event.raw_status}`}
                label={formatDate(event.observed_at, { locale, withTime: true })}
                value={
                  <>
                    {event.normalized_status ? <Chip label={state.label} tone={state.tone} /> : null} <span className="table__sub">{event.raw_status ?? event.kind}</span>
                  </>
                }
              />
            );
          })}
          {tracking.data.attempts.map((attempt) => (
            <p key={attempt.id} className="card__hint" style={{ margin: '4px 0' }}>
              {t('od.attempt', { n: attempt.attempt_number })}: {attempt.state}
              {attempt.tracking_code ? ` · ${attempt.tracking_code}` : ''}
              {attempt.error_code ? ` · ${attempt.error_code}` : ''}
              {attempt.recovery_note ? ` · ${attempt.recovery_note}` : ''}
            </p>
          ))}
          {pending && can('courier.credential_manage') ? (
            <form className="card" style={{ padding: 10, marginTop: 8 }} onSubmit={resolve}>
              <strong>{t('od.resolveTitle')}</strong>
              <p className="card__hint">{t('od.resolveHint')}</p>
              <label className="field">
                <span className="field__label">{t('od.parcelExists')}</span>
                <select className="select" required value={exists} onChange={(event) => setExists(event.target.value as 'yes' | 'no' | '')}>
                  <option value="">—</option>
                  <option value="yes">{t('od.existsYes')}</option>
                  <option value="no">{t('od.existsNo')}</option>
                </select>
              </label>
              {exists === 'yes' ? (
                <label className="field">
                  <span className="field__label">{t('od.tracking')}</span>
                  <input className="input" maxLength={120} value={code} onChange={(event) => setCode(event.target.value)} />
                </label>
              ) : null}
              <label className="field">
                <span className="field__label">{t('od.resolveReason')} *</span>
                <input className="input" required minLength={3} maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
              {error ? <p className="formerror">{error}</p> : null}
              <button type="submit" className="btn btn--sm" disabled={busy}>
                {t('od.resolve')}
              </button>
            </form>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** A workflow paused this order for a person to look at (V3.7). */
function ReviewHold({ orderId, reason, onReleased }: { orderId: string; reason: string; onReleased: () => void }) {
  const { t } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function release() {
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/orders/${orderId}/review-hold`);
      onReleased();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card" style={{ padding: 12, marginBottom: 12 }}>
      <p>
        <Chip label={t('orders.reviewHold')} tone="warn" /> {t(`auto.cfg.reviewReason.${reason}` as StringKey)}
      </p>
      <p className="card__hint">{t('orders.reviewHoldHint')}</p>
      {error ? <ErrorState error={error} /> : null}
      <button type="button" className="btn" disabled={busy} onClick={() => void release()}>
        {t('orders.reviewRelease')}
      </button>
    </div>
  );
}
