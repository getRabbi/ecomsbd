'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useMemo, useState } from 'react';

import { BookingOutcome, OrderPanel } from '@/components/OrderPanel';
import { OrderComposer } from '@/components/OrderComposer';
import { PasteDetect } from '@/components/PasteDetect';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState, LoadingRows } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import { courierName, type BookableCourier, type BookingReport } from '@/lib/couriers';
import { formatDate, formatPaisa } from '@/lib/money';
import { STATUS_MOVES, errorMessage, type OrderPrefill } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import {
  ORDER_STATUS_VALUES,
  describeFulfillment,
  describeOrderStatus,
  describeProfit,
  describeRisk,
  looksBookable,
} from '@/lib/states';
import type { OrderSummary } from '@/lib/types';
import { useSearchParamState } from '@/lib/url-state';
import { useApi, useDebounced } from '@/lib/useApi';

const PAGE_SIZE = 50;

/**
 * Orders — the desktop table this workspace exists for.
 *
 * **It never holds the whole list.** The API pages by cursor, and this screen
 * keeps exactly one page plus the cursors it has walked. Searching and
 * filtering go to the server, which is the only place that can see them all.
 *
 * **Bulk selection is per page, and says so.** A checkbox that silently meant
 * "all forty thousand" is how a seller books a year of history by accident.
 *
 * **Bulk actions use the canonical endpoints.** Booking goes to the courier
 * service's own bulk endpoint. Confirm and pack have no bulk endpoint, so they
 * are sent one order at a time, in sequence, through the same status move the
 * order drawer uses — each one checked by the state machine, every refusal
 * reported, nothing fired in parallel.
 *
 * What it refuses: computing anything about money. Every figure here is the
 * one the API sent.
 */
export default function OrdersPage() {
  const { t, locale } = useSession();
  const { can } = useShop();
  const router = useRouter();
  const params = useSearchParams();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState(params.get('status') ?? '');
  const customerId = params.get('customer');
  const debouncedSearch = useDebounced(search);

  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.length > 0 ? cursors[cursors.length - 1] : undefined;

  const [selected, setSelected] = useState<Set<string>>(new Set());
  // In the address bar, so search, notifications and quick actions can open things.
  const [openOrderId, setOpenOrderId] = useSearchParamState('order');
  const [composeParam, setComposeParam] = useSearchParamState('new');
  const [pasteParam, setPasteParam] = useSearchParamState('paste');
  const [prefill, setPrefill] = useState<OrderPrefill | null>(null);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [report, setReport] = useState<BookingReport | null>(null);
  const [bulkCourier, setBulkCourier] = useState('');

  const query = useMemo(
    () => ({
      limit: PAGE_SIZE,
      cursor,
      status: status || undefined,
      search: debouncedSearch.trim() || undefined,
      customer_id: customerId || undefined,
    }),
    [cursor, status, debouncedSearch, customerId],
  );

  const { data, loading, refreshing, error, reload } = useApi<Page<OrderSummary>>('/orders', query);
  const couriers = useApi<BookableCourier[]>(selected.size > 0 && can('order.book') ? '/couriers/bookable' : null);
  const bookableCouriers = (couriers.data ?? []).filter((row) => row.bookable);
  const provider = bulkCourier || bookableCouriers[0]?.provider || '';

  const rows = data?.items ?? [];

  const resetPaging = useCallback(() => {
    setCursors([]);
    setSelected(new Set());
  }, []);

  const toggle = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const pageIds = rows.map((row) => row.id);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const chosen = rows.filter((row) => selected.has(row.id));
  const bookableSelected = chosen.filter((row) => looksBookable(row) && row.status !== 'DRAFT');
  const confirmable = chosen.filter((row) => STATUS_MOVES[row.status]?.includes('CONFIRMED') && row.status === 'DRAFT');
  const packable = chosen.filter((row) => STATUS_MOVES[row.status]?.includes('PACKED'));

  async function bookSelected() {
    if (bookableSelected.length === 0 || !provider) return;
    setBusy(true);
    setNotice(null);
    setReport(null);
    try {
      const result = await api.post<BookingReport>(
        '/couriers/orders/book-bulk',
        { order_ids: bookableSelected.map((row) => row.id) },
        { provider },
      );
      // The provider's own counts, including the ambiguous ones, which are
      // neither successes nor failures and must not be reported as either.
      setReport(result);
      setSelected(new Set());
      reload();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function moveSelected(target: 'CONFIRMED' | 'PACKED', orders: OrderSummary[]) {
    setBusy(true);
    setNotice(null);
    setReport(null);
    let moved = 0;
    const refused: string[] = [];
    for (const order of orders) {
      try {
        await api.patch(`/orders/${order.id}`, { status: target });
        moved += 1;
      } catch (caught) {
        refused.push(`${order.order_number}: ${errorMessage(caught)}`);
      }
    }
    setNotice(
      [t('orders.bulkMoved', { count: moved, status: describeOrderStatus(target, locale).label }), ...refused.slice(0, 5)].join(' · '),
    );
    setSelected(new Set());
    setBusy(false);
    reload();
  }

  const composing = composeParam !== null || prefill !== null;

  return (
    <>
      <PageHeader
        title={t('orders.title')}
        subtitle={t('orders.subtitle')}
        actions={
          <>
            {refreshing ? <span className="card__hint">{t('common.loading')}</span> : null}
            {can('order.write') ? (
              <>
                <button type="button" className="btn" onClick={() => setPasteParam('1')}>
                  {t('act.pasteOrder')}
                </button>
                <button type="button" className="btn btn--primary" onClick={() => setComposeParam('1')}>
                  {t('act.newOrder')}
                </button>
              </>
            ) : null}
          </>
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

            {customerId ? (
              <button type="button" className="btn btn--sm" onClick={() => router.replace('/orders')}>
                {t('orders.oneCustomer')} ×
              </button>
            ) : null}

            {notice ? <span className="card__hint">{notice}</span> : null}
          </div>

          {report ? (
            <div style={{ padding: '0 16px' }}>
              <BookingOutcome report={report} />
            </div>
          ) : null}

          {selected.size > 0 ? (
            <div className="bulkbar">
              <span className="bulkbar__count">
                {selected.size} {t('common.selected')}
              </span>
              {can('order.write') && confirmable.length > 0 ? (
                <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void moveSelected('CONFIRMED', confirmable)}>
                  {t('od.to.CONFIRMED')} ({confirmable.length})
                </button>
              ) : null}
              {can('order.write') && packable.length > 0 ? (
                <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void moveSelected('PACKED', packable)}>
                  {t('od.to.PACKED')} ({packable.length})
                </button>
              ) : null}
              {can('order.book') ? (
                <>
                  <select
                    className="select"
                    value={provider}
                    onChange={(event) => setBulkCourier(event.target.value)}
                    aria-label={t('orders.courier')}
                    disabled={bookableCouriers.length === 0}
                  >
                    {bookableCouriers.length === 0 ? <option value="">{t('od.noCouriers')}</option> : null}
                    {bookableCouriers.map((row) => (
                      <option key={row.provider} value={row.provider}>
                        {row.display_name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn btn--primary btn--sm"
                    onClick={() => void bookSelected()}
                    disabled={busy || bookableSelected.length === 0 || !provider}
                  >
                    {t('orders.bulkBook')}
                    {bookableSelected.length > 0 ? ` (${bookableSelected.length})` : ''}
                  </button>
                </>
              ) : null}
              <span className="bulkbar__hint">{t('orders.bulkHint')}</span>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelected(new Set())}>
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
                        onChange={(event) => setSelected(event.target.checked ? new Set(pageIds) : new Set())}
                        // Says what it means: this page, not the whole shop.
                        aria-label={`${t('common.selected')} — ${rows.length}`}
                        disabled={rows.length === 0}
                      />
                    </th>
                    <th>{t('orders.order')}</th>
                    <th>{t('orders.customer')}</th>
                    <th>{t('orders.status')}</th>
                    <th className="num">{t('orders.amount')}</th>
                    <th>{t('orders.courier')}</th>
                    <th>{t('orders.delivery')}</th>
                    <th className="num">{t('orders.cod')}</th>
                    <th>{t('orders.risk')}</th>
                    <th>{t('orders.profit')}</th>
                  </tr>
                </thead>

                {loading ? (
                  <LoadingRows rows={8} columns={10} />
                ) : (
                  <tbody>
                    {rows.map((order) => {
                      const fulfillment = describeFulfillment(order.fulfillment_state, locale);
                      const risk = describeRisk(order.risk_state, locale);
                      const profit = describeProfit(order.profit_state, locale);
                      const orderStatus = describeOrderStatus(order.status, locale);
                      const isSelected = selected.has(order.id);

                      return (
                        <tr
                          key={order.id}
                          data-selected={isSelected}
                          className="table__row--clickable"
                          onClick={() => setOpenOrderId(order.id)}
                        >
                          <td onClick={(event) => event.stopPropagation()}>
                            <input type="checkbox" checked={isSelected} onChange={() => toggle(order.id)} aria-label={order.order_number} />
                          </td>
                          <td>
                            <div className="table__primary">{order.order_number}</div>
                            <div className="table__sub">{formatDate(order.created_at, { locale })}</div>
                          </td>
                          <td>
                            <div>{order.customer_name ?? '—'}</div>
                            {/* Masked, as everywhere else. A table is not a contact list. */}
                            <div className="table__sub">{order.customer_phone_masked ?? ''}</div>
                          </td>
                          <td>
                            <Chip label={orderStatus.label} tone={orderStatus.tone} />
                          </td>
                          <td className="num">{formatPaisa(order.subtotal_paisa, { locale })}</td>
                          <td>
                            {order.courier_provider ? (
                              <>
                                <div>{courierName(order.courier_provider, t('rv.manualCourier'))}</div>
                                {order.tracking_code ? <div className="table__sub">{order.tracking_code}</div> : null}
                              </>
                            ) : (
                              <span className="table__sub">{t('orders.noCourier')}</span>
                            )}
                          </td>
                          <td>
                            <Chip label={fulfillment.label} tone={fulfillment.tone} />
                          </td>
                          <td className="num">{formatPaisa(order.cod_amount_paisa, { locale })}</td>
                          <td>
                            <Chip label={risk.label} tone={risk.tone} />
                          </td>
                          <td>
                            <Chip label={profit.label} tone={profit.tone} />
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
        <OrderPanel orderId={openOrderId} onClose={() => setOpenOrderId(null)} onChanged={reload} />
      ) : null}

      {pasteParam !== null && prefill === null ? (
        <Drawer title={t('act.pasteOrder')} wide onClose={() => setPasteParam(null)}>
          <PasteDetect
            onReview={(next) => {
              setPasteParam(null);
              setPrefill(next);
            }}
          />
        </Drawer>
      ) : null}

      {composing ? (
        <OrderComposer
          prefill={prefill ?? undefined}
          onClose={() => {
            setPrefill(null);
            setComposeParam(null);
          }}
          onCreated={(id) => {
            setPrefill(null);
            setComposeParam(null);
            reload();
            setOpenOrderId(id);
          }}
        />
      ) : null}
    </>
  );
}
