'use client';

import { useCallback, useMemo, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState, LoadingRows, Row } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import { formatDate, formatPaisa } from '@/lib/money';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import {
  ORDER_STATUS_VALUES,
  describeFulfillment,
  describeOrderStatus,
  describeProfit,
  describeRisk,
  looksBookable,
} from '@/lib/states';
import type { OrderDetail, OrderSummary } from '@/lib/types';
import { useApi, useDebounced } from '@/lib/useApi';

const PAGE_SIZE = 50;

/**
 * Orders — the desktop table this dashboard exists for.
 *
 * Four things it does that the phone cannot, and one it refuses to do.
 *
 * **It never holds the whole list.** The API pages by cursor, and this screen
 * keeps exactly one page plus the cursors it has walked. A shop with forty
 * thousand orders loads fifty rows, not forty thousand — searching and
 * filtering go to the server, which is the only place that can see them all.
 *
 * **Filtering and search are server-side**, debounced, and a superseded
 * response is discarded rather than allowed to overwrite a newer one.
 *
 * **Bulk selection is per page, and states that plainly.** A checkbox that
 * silently meant "all forty thousand" is how a seller books a year of history
 * by accident.
 *
 * **A bulk action sends only what could plausibly be acted on.** The API still
 * decides and still refuses with reasons; this only avoids sending it a
 * selection it would reject wholesale.
 *
 * What it refuses: computing anything about money. Every figure here is the
 * one the API sent.
 */
export default function OrdersPage() {
  const { t, locale } = useSession();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const debouncedSearch = useDebounced(search);

  // A stack, because the API pages by opaque cursor: there is no page number to
  // jump to, and inventing one would mean guessing an offset the server never
  // promised.
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.length > 0 ? cursors[cursors.length - 1] : undefined;

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openOrderId, setOpenOrderId] = useState<string | null>(null);
  const [booking, setBooking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const query = useMemo(
    () => ({
      limit: PAGE_SIZE,
      cursor,
      status: status || undefined,
      search: debouncedSearch.trim() || undefined,
    }),
    [cursor, status, debouncedSearch],
  );

  const { data, loading, refreshing, error, reload } = useApi<Page<OrderSummary>>(
    '/orders',
    query,
  );

  const rows = data?.items ?? [];

  const resetPaging = useCallback(() => {
    setCursors([]);
    setSelected(new Set());
  }, []);

  const toggle = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const pageIds = rows.map((row) => row.id);
  const allOnPageSelected =
    pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  const bookableSelected = rows.filter(
    (row) => selected.has(row.id) && looksBookable(row),
  );

  async function bookSelected() {
    if (bookableSelected.length === 0) {
      return;
    }
    setBooking(true);
    setNotice(null);
    try {
      const report = await api.post<{ booked: number; ambiguous: number; failed: number }>(
        '/couriers/orders/book-bulk',
        { order_ids: bookableSelected.map((row) => row.id) },
      );
      // The provider's own counts, including the ambiguous ones, which are
      // neither successes nor failures and must not be reported as either.
      setNotice(
        `${report.booked} booked · ${report.ambiguous} unconfirmed · ${report.failed} refused`,
      );
      setSelected(new Set());
      reload();
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBooking(false);
    }
  }

  return (
    <>
      <PageHeader
        title={t('orders.title')}
        subtitle={t('orders.subtitle')}
        actions={
          refreshing ? <span className="card__hint">{t('common.loading')}</span> : null
        }
      />

      <div className="content">
        <Card padded={false}>
          <div className="toolbar">
            <input
              className="input input--search"
              type="search"
              placeholder={t('orders.searchHint')}
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPaging();
              }}
              aria-label={t('common.search')}
            />

            <select
              className="select"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                resetPaging();
              }}
              aria-label={t('common.filter')}
            >
              <option value="">{t('common.all')}</option>
              {ORDER_STATUS_VALUES.map((value) => (
                <option key={value} value={value}>
                  {describeOrderStatus(value, locale).label}
                </option>
              ))}
            </select>

            {notice ? <span className="card__hint">{notice}</span> : null}
          </div>

          {selected.size > 0 ? (
            <div className="bulkbar">
              <span className="bulkbar__count">
                {selected.size} {t('common.selected')}
              </span>
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={bookSelected}
                disabled={booking || bookableSelected.length === 0}
              >
                {t('orders.bulkBook')}
                {bookableSelected.length > 0 ? ` (${bookableSelected.length})` : ''}
              </button>
              <span className="bulkbar__hint">{t('orders.bulkHint')}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setSelected(new Set())}
              >
                {t('common.cancel')}
              </button>
            </div>
          ) : null}

          {error && rows.length === 0 ? (
            <ErrorState error={error} onRetry={reload} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        checked={allOnPageSelected}
                        onChange={(event) =>
                          setSelected(
                            event.target.checked ? new Set(pageIds) : new Set(),
                          )
                        }
                        // Says what it means: this page, not the whole shop.
                        aria-label={`${t('common.selected')} — ${rows.length}`}
                        disabled={rows.length === 0}
                      />
                    </th>
                    <th>{t('orders.order')}</th>
                    <th>{t('orders.customer')}</th>
                    <th className="num">{t('orders.amount')}</th>
                    <th>{t('orders.courier')}</th>
                    <th>{t('orders.delivery')}</th>
                    <th className="num">{t('orders.cod')}</th>
                    <th>{t('orders.risk')}</th>
                    <th>{t('orders.profit')}</th>
                    <th>{t('orders.actions')}</th>
                  </tr>
                </thead>

                {loading ? (
                  <LoadingRows rows={8} columns={10} />
                ) : (
                  <tbody>
                    {rows.map((order) => {
                      const fulfillment = describeFulfillment(
                        order.fulfillment_state,
                        locale,
                      );
                      const risk = describeRisk(order.risk_state, locale);
                      const profit = describeProfit(order.profit_state, locale);
                      const isSelected = selected.has(order.id);

                      return (
                        <tr key={order.id} data-selected={isSelected}>
                          <td>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggle(order.id)}
                              aria-label={order.order_number}
                            />
                          </td>
                          <td>
                            <div className="table__primary">{order.order_number}</div>
                            <div className="table__sub">
                              {formatDate(order.created_at, { locale })}
                            </div>
                          </td>
                          <td>
                            <div>{order.customer_name ?? '—'}</div>
                            {/* Masked, as everywhere else. A table is not a
                                contact list. */}
                            <div className="table__sub">
                              {order.customer_phone_masked ?? ''}
                            </div>
                          </td>
                          <td className="num">
                            {formatPaisa(order.subtotal_paisa, { locale })}
                          </td>
                          <td>
                            {order.courier_provider ? (
                              <>
                                <div>{courierName(order.courier_provider)}</div>
                                {order.tracking_code ? (
                                  <div className="table__sub">{order.tracking_code}</div>
                                ) : null}
                              </>
                            ) : (
                              <span className="table__sub">{t('orders.noCourier')}</span>
                            )}
                          </td>
                          <td>
                            <Chip label={fulfillment.label} tone={fulfillment.tone} />
                          </td>
                          <td className="num">
                            {formatPaisa(order.cod_amount_paisa, { locale })}
                          </td>
                          <td>
                            <Chip label={risk.label} tone={risk.tone} />
                          </td>
                          <td>
                            <Chip label={profit.label} tone={profit.tone} />
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => setOpenOrderId(order.id)}
                            >
                              {t('orders.view')}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                )}
              </table>

              {!loading && rows.length === 0 ? (
                <EmptyState title={t('orders.empty')} hint={t('orders.emptyHint')} />
              ) : null}
            </div>
          )}

          <div className="pagination">
            <button
              type="button"
              className="btn btn--sm"
              disabled={cursors.length === 0 || loading}
              onClick={() => {
                setCursors((current) => current.slice(0, -1));
                setSelected(new Set());
              }}
            >
              {t('common.previous')}
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={!data?.has_more || !data?.next_cursor || loading}
              onClick={() => {
                if (data?.next_cursor) {
                  setCursors((current) => [...current, data.next_cursor as string]);
                  setSelected(new Set());
                }
              }}
            >
              {t('common.next')}
            </button>
          </div>
        </Card>
      </div>

      {openOrderId ? (
        <OrderDrawer orderId={openOrderId} onClose={() => setOpenOrderId(null)} />
      ) : null}
    </>
  );
}

function OrderDrawer({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const { t, locale } = useSession();
  const { data, loading, error, reload } = useApi<OrderDetail>(`/orders/${orderId}`);

  return (
    <Drawer title={t('orders.detail')} onClose={onClose}>
      {loading ? <p className="card__hint">{t('common.loading')}</p> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}
      {data ? (
        <>
          {data.review_hold ? (
            <ReviewHold orderId={data.id} reason={data.review_hold.reason} onReleased={reload} />
          ) : null}
          <Row label={t('orders.order')} value={data.order_number} />
          <Row
            label={t('orders.customer')}
            value={data.customer_name ?? data.customer_phone_masked ?? '—'}
          />
          <Row
            label={t('orders.courier')}
            value={
              data.courier_provider
                ? `${courierName(data.courier_provider)}${
                    data.tracking_code ? ` · ${data.tracking_code}` : ''
                  }`
                : t('orders.noCourier')
            }
          />
          <Row
            label={t('orders.delivery')}
            value={
              <Chip {...toChip(describeFulfillment(data.fulfillment_state, locale))} />
            }
          />
          <Row
            label={t('orders.risk')}
            value={<Chip {...toChip(describeRisk(data.risk_state, locale))} />}
          />
          <Row
            label={t('orders.amount')}
            value={formatPaisa(data.subtotal_paisa, { locale })}
          />
          <Row
            label={t('orders.cod')}
            value={formatPaisa(data.cod_amount_paisa, { locale })}
          />

          <h3 className="card__title" style={{ marginTop: 18 }}>
            {t('orders.items')}
          </h3>
          {data.items.map((item) => (
            <Row
              key={item.id}
              label={`${item.name} × ${item.quantity}`}
              value={formatPaisa(item.unit_price_paisa * item.quantity, { locale })}
            />
          ))}

          {data.delivery_address_raw ? (
            <p className="card__hint" style={{ marginTop: 14 }}>
              {data.delivery_address_raw}
            </p>
          ) : null}
        </>
      ) : null}
    </Drawer>
  );
}

/**
 * The courier's name as a seller knows it.
 *
 * Courier names stay English in both languages, as they do on mobile. One this
 * build has not met falls through to its own identifier rather than being
 * hidden: an unknown courier on a parcel is something support needs to see.
 */
function courierName(provider: string): string {
  switch (provider) {
    case 'steadfast':
      return 'Steadfast';
    case 'pathao':
      return 'Pathao';
    case 'redx':
      return 'RedX';
    case 'manual':
      return 'Manual';
    default:
      return provider;
  }
}

function toChip(state: { label: string; tone: 'neutral' | 'good' | 'warn' | 'bad' }) {
  return { label: state.label, tone: state.tone };
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
    } catch (e) {
      setError(e);
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
      <button className="btn" disabled={busy} onClick={() => void release()}>
        {t('orders.reviewRelease')}
      </button>
    </div>
  );
}
