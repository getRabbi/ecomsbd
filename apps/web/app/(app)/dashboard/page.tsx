'use client';

import Link from 'next/link';

import { Icon, type IconName } from '@/components/icons';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, type Tone } from '@/components/ui';
import type { Page } from '@/lib/api';
import type { CourierAccount } from '@/lib/couriers';
import type { StringKey } from '@/lib/i18n';
import type { Overview, Trend } from '@/lib/insights';
import { formatDate, formatPaisa } from '@/lib/money';
import { hrefFor, type AppNotification } from '@/lib/notifications';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { describeFulfillment, describeOrderStatus } from '@/lib/states';
import type { OrderSummary } from '@/lib/types';
import { useApi } from '@/lib/useApi';

interface Home {
  as_of: string;
  orders_today: number;
  delivered_today: number;
  returned_today: number;
  gross_sales_paisa: number;
  realized_revenue_paisa: number;
  contribution_profit_paisa: number;
  cod_outstanding_paisa: number;
  cod_expected_today_paisa: number;
  cod_unforecast_paisa: number;
  cod_overdue_paisa: number;
  mismatch_paisa: number;
  mismatch_count: number;
  return_loss_paisa: number;
  estimated_parcels: number;
  incomplete_parcels: number;
  alerts: { kind: string; severity: string; count: number; amount_paisa: number }[];
}

interface MoneySummary {
  unknown_deduction_paisa: number;
  unexplained_payout_paisa: number;
  open_case_count: number;
}

interface StockSummary {
  low_stock_items: number;
  out_of_stock_items: number;
}

interface Attention {
  key: string;
  href: string;
  label: string;
  tone: Tone;
}

/** Where each backend alert kind is worked on, on the desktop. */
const ALERT_ROUTES: Record<string, string> = {
  DELIVERED_BUT_UNPAID: '/money/receivables',
  PAYOUT_OVERDUE: '/money/receivables',
  UNDERPAID: '/reconciliation',
  RECONCILIATION_DISCREPANCY: '/reconciliation',
  STALE_IN_TRANSIT: '/orders',
  COURIER_STATUS_STUCK: '/orders',
  RETURNED_NOT_RESTOCKED: '/returns',
  RETURN_SPIKE: '/returns',
  HIGH_RTO: '/returns',
  LOW_STOCK: '/products',
  STOCKOUT_PREDICTED: '/forecasting',
  NEGATIVE_MARGIN: '/insights',
  IMPORT_FAILURE: '/imports',
  PURCHASE_ORDER_OVERDUE: '/procurement',
  PARTIAL_RECEIPT_PENDING: '/procurement',
  SUPPLIER_PAYMENT_OVERDUE: '/procurement/payables',
  COURIER_ACCOUNT_PROBLEM: '/couriers',
  FOLLOW_UP_DUE: '/customers?segment=FOLLOW_UP_DUE',
  CHAT_ORDER_READY: '/inbox',
};

const QUICK: { href: string; labelKey: StringKey; icon: IconName; permission: string }[] = [
  { href: '/orders?new=1', labelKey: 'act.newOrder', icon: 'plus', permission: 'order.write' },
  { href: '/orders?paste=1', labelKey: 'act.pasteOrder', icon: 'inbox', permission: 'order.write' },
  { href: '/orders?status=CONFIRMED', labelKey: 'act.bookCourier', icon: 'couriers', permission: 'order.book' },
  { href: '/expenses?new=1', labelKey: 'act.addExpense', icon: 'money', permission: 'money.reconcile' },
];

/**
 * The seller's home on the desktop.
 *
 * Today's figures come from `/analytics/home`, the same endpoint the phone's
 * Home uses, so the two cannot disagree. An estimated or incomplete figure is
 * labelled as such — never shown as exact. A member without the money
 * permission sees order counts and work, not a wall of locked tiles.
 */
export default function DashboardPage() {
  const { t, locale } = useSession();
  const { can, current, permissions } = useShop();
  const money = can('money.view');
  const known = permissions !== null;

  const home = useApi<Home>(known && money ? '/analytics/home' : null);
  const today = useApi<Overview>(known && !money && can('order.view') ? '/analytics/insights/overview' : null, { days: 1 });
  const summary = useApi<MoneySummary>(known && money ? '/money/summary' : null);
  const stock = useApi<StockSummary>(known && can('product.view') ? '/products/stock-summary' : null);
  const accounts = useApi<CourierAccount[]>(known ? '/couriers/accounts' : null);
  const recent = useApi<Page<OrderSummary>>(known && can('order.view') ? '/orders' : null, { limit: 8 });
  const notes = useApi<Page<AppNotification>>(known ? '/notifications' : null, { limit: 6, lang: locale });
  const trend = useApi<Trend>(known && can('order.view') ? '/analytics/insights/trend' : null, { days: 30 });

  const h = home.data;
  const estimated = h ? h.estimated_parcels > 0 || h.incomplete_parcels > 0 : false;

  const attention: Attention[] = [];
  for (const alert of h?.alerts ?? []) {
    if (alert.count <= 0) continue;
    const key = `dash.alert.${alert.kind}` as StringKey;
    const label = t(key) === key ? alert.kind.replaceAll('_', ' ').toLowerCase() : t(key);
    attention.push({
      key: `alert-${alert.kind}`,
      href: ALERT_ROUTES[alert.kind] ?? '/notifications',
      label: `${label} · ${alert.count}${alert.amount_paisa ? ` · ${formatPaisa(alert.amount_paisa, { locale })}` : ''}`,
      tone: alert.severity === 'CRITICAL' ? 'bad' : 'warn',
    });
  }
  if (summary.data && summary.data.open_case_count > 0) {
    attention.push({ key: 'cases', href: '/reconciliation', label: t('dash.openCases', { count: summary.data.open_case_count }), tone: 'warn' });
  }
  if (summary.data && summary.data.unexplained_payout_paisa > 0) {
    attention.push({ key: 'unexplained', href: '/money/payouts', label: t('dash.unexplained', { amount: formatPaisa(summary.data.unexplained_payout_paisa, { locale }) }), tone: 'warn' });
  }
  if (summary.data && summary.data.unknown_deduction_paisa > 0) {
    // Kept separate from the known charge buckets: a deduction nobody can name
    // is exactly what a seller should see.
    attention.push({ key: 'unknown', href: '/money', label: t('dash.unknownDeduction', { amount: formatPaisa(summary.data.unknown_deduction_paisa, { locale }) }), tone: 'warn' });
  }
  if (h && h.cod_overdue_paisa > 0) {
    attention.push({ key: 'overdue', href: '/money/receivables', label: t('dash.overdue', { amount: formatPaisa(h.cod_overdue_paisa, { locale }) }), tone: 'bad' });
  }
  if (stock.data && stock.data.out_of_stock_items > 0) {
    attention.push({ key: 'out', href: '/products', label: t('dash.outOfStock', { count: stock.data.out_of_stock_items }), tone: 'bad' });
  }
  if (stock.data && stock.data.low_stock_items > 0) {
    attention.push({ key: 'low', href: '/products', label: t('dash.lowStock', { count: stock.data.low_stock_items }), tone: 'warn' });
  }
  for (const account of accounts.data ?? []) {
    if (account.needs_reconnect) {
      attention.push({ key: `courier-${account.provider}`, href: '/couriers', label: t('dash.reconnect', { courier: account.provider }), tone: 'warn' });
    }
  }

  const ordersToday = h?.orders_today ?? today.data?.orders.current;
  const deliveredToday = h?.delivered_today ?? today.data?.delivered.current;
  const buckets = trend.data?.buckets ?? [];
  const peak = Math.max(1, ...buckets.map((bucket) => bucket.orders));

  return (
    <>
      <PageHeader title={current?.name ?? t('dash.title')} subtitle={t('dash.subtitle')} />

      <div className="content">
        <section className="hero" aria-label={t('dash.today')}>
          <div className="hero__head">
            <span className="hero__eyebrow">{t('dash.today')}{h ? ` · ${formatDate(h.as_of, { locale })}` : ''}</span>
            {estimated ? <span className="hero__badge">{t('dash.estimate')}</span> : null}
          </div>
          <div className="hero__grid">
            {money ? (
              <div className="hero__metric">
                <span className="hero__label">{t('dash.grossSales')}</span>
                <span className="hero__value">{h ? formatPaisa(h.gross_sales_paisa, { locale }) : '…'}</span>
              </div>
            ) : null}
            <div className="hero__metric">
              <span className="hero__label">{t('dash.ordersToday')}</span>
              <span className="hero__value">{ordersToday ?? '…'}</span>
            </div>
            <div className="hero__metric">
              <span className="hero__label">{t('dash.deliveredToday')}</span>
              <span className="hero__value">{deliveredToday ?? '…'}</span>
            </div>
            {money ? (
              <>
                <div className="hero__metric">
                  <span className="hero__label">{t('dash.codOutstanding')}</span>
                  <span className="hero__value">{h ? formatPaisa(h.cod_outstanding_paisa, { locale }) : '…'}</span>
                </div>
                <div className="hero__metric">
                  <span className="hero__label">{t('dash.contribution')}</span>
                  <span className="hero__value">{h ? formatPaisa(h.contribution_profit_paisa, { locale }) : '…'}</span>
                </div>
              </>
            ) : null}
          </div>
          {estimated && h ? (
            <p className="hero__note">{t('dash.estimateNote', { estimated: h.estimated_parcels, incomplete: h.incomplete_parcels })}</p>
          ) : null}
          {home.error ? <p className="hero__note">{t('common.couldNotLoad')}</p> : null}
        </section>

        <div className="quickrow">
          {QUICK.filter((action) => can(action.permission)).map((action) => (
            <Link key={action.href} href={action.href} className="quick">
              <Icon name={action.icon} size={18} />
              <span>{t(action.labelKey)}</span>
            </Link>
          ))}
        </div>

        <div className="grid2">
          <Card title={t('dash.alerts')}>
            {attention.length === 0 ? (
              <p className="card__hint">{t('dash.allClear')}</p>
            ) : (
              <ul className="linklist">
                {attention.map((item) => (
                  <li key={item.key}>
                    <Link href={item.href} className="linklist__item">
                      <Chip label={item.tone === 'bad' ? '!' : '•'} tone={item.tone} />
                      <span>{item.label}</span>
                      <Icon name="arrowRight" size={14} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {money && h ? (
            <Card title={t('dash.progress')}>
              <div className="minitiles">
                <Mini label={t('dash.delivered')} value={String(h.delivered_today)} />
                <Mini label={t('dash.returned')} value={String(h.returned_today)} />
                <Mini label={t('dash.realized')} value={formatPaisa(h.realized_revenue_paisa, { locale })} />
                <Mini label={t('dash.expectedToday')} value={formatPaisa(h.cod_expected_today_paisa, { locale })} hint={t('cf.estimate')} />
                <Mini label={t('dash.unforecast')} value={formatPaisa(h.cod_unforecast_paisa, { locale })} hint={t('dash.unforecastHint')} />
                <Mini label={t('dash.returnLoss')} value={formatPaisa(h.return_loss_paisa, { locale })} />
              </div>
              {h.mismatch_count > 0 ? (
                <p className="card__hint">
                  <Link href="/reconciliation">{t('dash.mismatch', { count: h.mismatch_count, amount: formatPaisa(h.mismatch_paisa, { locale }) })}</Link>
                </p>
              ) : null}
            </Card>
          ) : (
            <Card title={t('dash.pulse')}>
              <Pulse buckets={buckets} peak={peak} />
            </Card>
          )}
        </div>

        <div className="grid2" style={{ marginTop: 14 }}>
          {can('order.view') ? (
            <Card title={t('dash.recentOrders')} actions={<Link className="btn btn--sm" href="/orders">{t('dash.allOrders')}</Link>} padded={false}>
              {recent.error ? (
                <ErrorState error={recent.error} onRetry={recent.reload} />
              ) : (recent.data?.items ?? []).length === 0 && !recent.loading ? (
                <EmptyState title={t('orders.empty')} />
              ) : (
                <div className="tablewrap">
                  <table className="table">
                    <tbody>
                      {(recent.data?.items ?? []).map((order) => {
                        const status = describeOrderStatus(order.status, locale);
                        const parcel = describeFulfillment(order.fulfillment_state, locale);
                        return (
                          <tr key={order.id}>
                            <td>
                              <Link className="table__primary" href={`/orders?order=${order.id}`}>{order.order_number}</Link>
                              <div className="table__sub">{order.customer_name ?? order.customer_phone_masked ?? ''}</div>
                            </td>
                            <td><Chip label={status.label} tone={status.tone} /></td>
                            <td><Chip label={parcel.label} tone={parcel.tone} /></td>
                            <td className="num">{formatPaisa(order.cod_amount_paisa, { locale })}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          ) : null}

          <Card title={t('dash.recentAlerts')} actions={<Link className="btn btn--sm" href="/notifications">{t('nav.notifications')}</Link>}>
            {(notes.data?.items ?? []).length === 0 ? (
              <p className="card__hint">{notes.loading ? t('common.loading') : t('dash.noNotifications')}</p>
            ) : (
              <ul className="linklist">
                {(notes.data?.items ?? []).map((note) => {
                  const href = hrefFor(note) ?? '/notifications';
                  return (
                    <li key={note.id}>
                      <Link href={href} className="linklist__item">
                        <span style={{ fontWeight: note.read_at ? 400 : 650 }}>{note.title}</span>
                        <span className="table__sub" style={{ marginLeft: 'auto' }}>{formatDate(note.created_at, { locale })}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </div>

        {money && can('order.view') ? (
          <div style={{ marginTop: 14 }}>
            <Card title={t('dash.pulse')} actions={<Link className="btn btn--sm" href="/insights">{t('nav.insights')}</Link>}>
              <Pulse buckets={buckets} peak={peak} />
            </Card>
          </div>
        ) : null}
      </div>
    </>
  );
}

function Mini({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="mini">
      <span className="tile__label">{label}</span>
      <span className="mini__value">{value}</span>
      {hint ? <span className="tile__hint">{hint}</span> : null}
    </div>
  );
}

/** Thirty days of orders as plain SVG bars — no chart dependency (ADR). */
function Pulse({ buckets, peak }: { buckets: Trend['buckets']; peak: number }) {
  const { t, locale } = useSession();
  if (buckets.length === 0) {
    return <p className="card__hint">{t('ins.trendEmpty')}</p>;
  }
  const width = 600;
  const height = 90;
  const bar = width / buckets.length;
  return (
    <>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('dash.pulse')} style={{ width: '100%', height: 90 }}>
        {buckets.map((bucket, index) => {
          const h = Math.max(2, (bucket.orders / peak) * (height - 6));
          return (
            <rect key={bucket.start} x={index * bar + 1} y={height - h} width={Math.max(1, bar - 2)} height={h} rx={2} fill="var(--brand)" opacity={0.85}>
              <title>{`${formatDate(bucket.start, { locale })}: ${bucket.orders}`}</title>
            </rect>
          );
        })}
      </svg>
      <p className="card__hint">
        {t('dash.pulseHint', { orders: buckets.reduce((sum, bucket) => sum + bucket.orders, 0) })}
      </p>
    </>
  );
}
