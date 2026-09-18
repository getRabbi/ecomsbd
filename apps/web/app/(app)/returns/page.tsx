'use client';

import { useMemo, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState, Row, Tile } from '@/components/ui';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

/** The server's counts. RTO = returned + courier_cancelled; rate is over `completed`. */
interface Counts {
  completed: number;
  delivered: number;
  partial: number;
  rto: number;
  returned: number;
  courier_cancelled: number;
  lost: number;
  rto_rate_basis_points: number | null;
  success_rate_basis_points: number | null;
  sufficient: boolean;
}

interface Summary {
  since: string;
  until: string;
  days: number;
  counts: Counts;
  open_now: number;
  cancelled_before_dispatch: number;
  windows: { days: number; since: string; counts: Counts }[];
  definition: { min_sample: number };
}

interface Trend {
  points: { week_start: string; week_end: string; counts: Counts }[];
}

interface ProductRow {
  product_id: string | null;
  product_name: string;
  counts: Counts;
  rto_value_paisa: number;
}

interface ProductPage {
  items: ProductRow[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

interface CourierRow {
  provider: string;
  counts: Counts;
  recent: Counts;
  previous: Counts;
  trend: 'UP' | 'DOWN' | 'FLAT' | 'INSUFFICIENT_DATA';
  in_transit_now: number;
}

interface Couriers {
  items: CourierRow[];
  excluded_providers: string[];
}

interface Areas {
  status: 'ACTIVE' | 'DATA_NOT_RELIABLE';
  coverage_basis_points: number | null;
  min_coverage_basis_points: number;
  items: { label: string; counts: Counts }[];
  total: number;
  offset: number;
  has_more: boolean;
}

interface Observation {
  code: string;
  parcel_count: number;
  rto_count: number;
  delivered_count: number;
  earlier_parcel_count: number;
  earlier_rto_count: number;
  product_name: string | null;
}

interface Pattern {
  customer_id: string;
  name: string | null;
  phone_masked: string | null;
  counts: Counts;
  observations: Observation[];
}

interface History {
  customer_id: string;
  name: string | null;
  phone_masked: string | null;
  order_count: number;
  counts: Counts;
  in_transit_count: number;
  cancelled_before_dispatch: number;
  last_order_at: string | null;
  recent: { order_number: string; outcome: string; provider: string; at: string | null }[];
  observations: Observation[];
  risk_state: string;
}

type Translate = (key: StringKey, vars?: Record<string, string | number>) => string;

const PAGE = 20;
const COURIER_NAMES: Record<string, string> = {
  steadfast: 'Steadfast',
  pathao: 'Pathao',
  redx: 'RedX',
  manual: 'Manual',
};
const OBSERVATIONS: Record<string, StringKey> = {
  REPEAT_RTO: 'rto.obs.REPEAT_RTO',
  RTO_AFTER_DELIVERIES: 'rto.obs.RTO_AFTER_DELIVERIES',
  WORSENING: 'rto.obs.WORSENING',
  IMPROVING: 'rto.obs.IMPROVING',
  PRODUCT_REPEAT_RTO: 'rto.obs.PRODUCT_REPEAT_RTO',
};
const OUTCOMES: Record<string, StringKey> = {
  DELIVERED: 'rto.outcome.DELIVERED',
  PARTIAL: 'rto.outcome.PARTIAL',
  RTO: 'rto.outcome.RTO',
  LOST: 'rto.outcome.LOST',
  IN_TRANSIT: 'rto.outcome.IN_TRANSIT',
};
const TRENDS: Record<CourierRow['trend'], StringKey> = {
  UP: 'rto.trend.UP',
  DOWN: 'rto.trend.DOWN',
  FLAT: 'rto.trend.FLAT',
  INSUFFICIENT_DATA: 'rto.trend.INSUFFICIENT_DATA',
};

function courier(provider: string): string {
  return COURIER_NAMES[provider] ?? provider;
}

/** Basis points as a percentage, formatted only — never recomputed here. */
function pct(bps: number | null): string {
  if (bps === null) return '—';
  const value = bps / 100;
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

function observation(t: Translate, obs: Observation): string | null {
  const key = OBSERVATIONS[obs.code];
  if (!key) return null;
  return t(key, {
    rto: obs.rto_count,
    parcels: obs.parcel_count,
    delivered: obs.delivered_count,
    earlier: obs.earlier_parcel_count,
    earlierRto: obs.earlier_rto_count,
    earlierDelivered: obs.earlier_parcel_count - obs.earlier_rto_count,
    product: obs.product_name ?? '',
  });
}

/** The rate when the sample supports it, "limited data" otherwise. */
function RateCell({ counts, t }: { counts: Counts; t: Translate }) {
  if (counts.completed === 0) return <>—</>;
  if (!counts.sufficient) {
    return (
      <span title={t('rto.rateOf', { rto: counts.rto, completed: counts.completed })}>
        <Chip label={t('rto.limited')} />
      </span>
    );
  }
  return <strong>{pct(counts.rto_rate_basis_points)}</strong>;
}

/**
 * Returns & RTO.
 *
 * Same endpoints as the phone, and nothing added up here: every rate is the
 * server's basis points, shown next to the counts it came from. RTO is
 * returned plus cancelled at the courier; the rate is over completed parcels,
 * so parcels still moving and orders cancelled before dispatch never move it.
 * Tables are paged on the server — the browser never holds the whole list.
 */
export default function ReturnsPage() {
  const { t, locale } = useSession();
  const [days, setDays] = useState(90);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('rto_rate');
  const [areaOffset, setAreaOffset] = useState(0);
  const [customer, setCustomer] = useState<string | null>(null);
  const debounced = useDebounced(search);

  const summary = useApi<Summary>('/analytics/rto/summary', { days: 30 });
  const trend = useApi<Trend>('/analytics/rto/trend', { weeks: 12 });
  const couriers = useApi<Couriers>('/analytics/rto/couriers', { days });
  const productQuery = useMemo(
    () => ({ days, offset, limit: PAGE, sort, search: debounced.trim() || undefined }),
    [days, offset, sort, debounced],
  );
  const products = useApi<ProductPage>('/analytics/rto/products', productQuery);
  const areas = useApi<Areas>('/analytics/rto/areas', { days, offset: areaOffset, limit: PAGE });
  const patterns = useApi<{ items: Pattern[] }>('/analytics/rto/patterns', { days });

  const counts = summary.data?.counts;

  return (
    <>
      <PageHeader title={t('rto.title')} subtitle={t('rto.subtitle')} />
      <div className="content">
        {summary.error ? (
          <Card>
            <ErrorState error={summary.error} onRetry={summary.reload} />
          </Card>
        ) : null}

        <div className="tiles">
          <Tile
            label={t('rto.rate30')}
            value={summary.loading || !counts ? '…' : pct(counts.rto_rate_basis_points)}
            hint={
              counts
                ? counts.completed === 0
                  ? t('rto.noCompleted')
                  : `${t('rto.rateOf', { rto: counts.rto, completed: counts.completed })}${
                      counts.sufficient ? '' : ` · ${t('rto.limited')}`
                    }`
                : undefined
            }
          />
          <Tile
            label={t('rto.rtoSplit')}
            value={counts ? `${counts.rto}` : '…'}
            hint={
              counts
                ? t('rto.rtoSplitHint', {
                    returned: counts.returned,
                    cancelled: counts.courier_cancelled,
                  })
                : undefined
            }
          />
          <Tile
            label={t('rto.openNow')}
            value={summary.data ? `${summary.data.open_now}` : '…'}
            hint={t('rto.openNowHint')}
          />
          <Tile
            label={t('rto.cancelledBefore')}
            value={summary.data ? `${summary.data.cancelled_before_dispatch}` : '…'}
            hint={t('rto.cancelledBeforeHint')}
          />
        </div>

        <p className="card__hint" style={{ margin: '0 0 18px' }}>
          {t('rto.definition')}
        </p>

        <div className="grid2">
          <Card title={t('rto.trendTitle')} hint={t('rto.trendHint')}>
            {trend.data ? <TrendChart points={trend.data.points} t={t} /> : null}
          </Card>
          <Card title={t('rto.windowsTitle')} padded={false}>
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('rto.window')}</th>
                    <th className="num">{t('rto.completed')}</th>
                    <th className="num">{t('rto.rto')}</th>
                    <th className="num">{t('rto.rate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(summary.data?.windows ?? []).map((w) => (
                    <tr key={w.days}>
                      <td>{t('rto.lastDays', { days: w.days })}</td>
                      <td className="num">{w.counts.completed}</td>
                      <td className="num">{w.counts.rto}</td>
                      <td className="num">
                        <RateCell counts={w.counts} t={t} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="toolbar" style={{ padding: '18px 0 6px' }}>
          <label className="field" style={{ margin: 0 }}>
            <span className="field__label">{t('rto.period')}</span>
            <select
              className="select"
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value));
                setOffset(0);
                setAreaOffset(0);
              }}
            >
              {[30, 90, 180].map((value) => (
                <option key={value} value={value}>
                  {t('rto.lastDays', { days: value })}
                </option>
              ))}
            </select>
          </label>
        </div>

        <Card title={t('rto.couriersTitle')} hint={t('rto.couriersHint')} padded={false}>
          {couriers.error ? <ErrorState error={couriers.error} onRetry={couriers.reload} /> : null}
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('rto.courier')}</th>
                  <th className="num">{t('rto.completed')}</th>
                  <th className="num">{t('rto.delivered')}</th>
                  <th className="num">{t('rto.rto')}</th>
                  <th className="num">{t('rto.rate')}</th>
                  <th className="num">{t('rto.onTheWay')}</th>
                  <th>{t('rto.trend')}</th>
                </tr>
              </thead>
              <tbody>
                {(couriers.data?.items ?? []).map((row) => (
                  <tr key={row.provider}>
                    <td>{courier(row.provider)}</td>
                    <td className="num">{row.counts.completed}</td>
                    <td className="num">{row.counts.delivered + row.counts.partial}</td>
                    <td className="num">{row.counts.rto}</td>
                    <td className="num">
                      <RateCell counts={row.counts} t={t} />
                    </td>
                    <td className="num">{row.in_transit_now}</td>
                    <td>
                      {t(TRENDS[row.trend], {
                        recent: pct(row.recent.rto_rate_basis_points),
                        previous: pct(row.previous.rto_rate_basis_points),
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {couriers.data && couriers.data.items.length === 0 ? (
            <EmptyState title={t('rto.noCompleted')} />
          ) : null}
          {couriers.data && couriers.data.excluded_providers.length > 0 ? (
            <p className="card__hint" style={{ padding: '8px 18px 14px' }}>
              {t('rto.excluded', {
                providers: couriers.data.excluded_providers.map(courier).join(', '),
              })}
            </p>
          ) : null}
        </Card>

        <div style={{ marginTop: 18 }}>
          <Card title={t('rto.productsTitle')} hint={t('rto.productsHint')} padded={false}>
            <div className="toolbar">
              <input
                className="input input--search"
                type="search"
                placeholder={t('rto.searchProduct')}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
                aria-label={t('common.search')}
              />
              <select
                className="select"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value);
                  setOffset(0);
                }}
                aria-label={t('rto.sort')}
              >
                <option value="rto_rate">{t('rto.sortRate')}</option>
                <option value="rto_count">{t('rto.sortCount')}</option>
                <option value="completed">{t('rto.sortCompleted')}</option>
                <option value="rto_value">{t('rto.sortValue')}</option>
              </select>
            </div>
            {products.error ? <ErrorState error={products.error} onRetry={products.reload} /> : null}
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('rto.product')}</th>
                    <th className="num">{t('rto.completed')}</th>
                    <th className="num">{t('rto.delivered')}</th>
                    <th className="num">{t('rto.rto')}</th>
                    <th className="num">{t('rto.rate')}</th>
                    <th className="num">{t('rto.value')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(products.data?.items ?? []).map((row) => (
                    <tr key={`${row.product_id ?? 'free'}:${row.product_name}`}>
                      <td>{row.product_name}</td>
                      <td className="num">{row.counts.completed}</td>
                      <td className="num">{row.counts.delivered + row.counts.partial}</td>
                      <td className="num">{row.counts.rto}</td>
                      <td className="num">
                        <RateCell counts={row.counts} t={t} />
                      </td>
                      <td className="num">{formatPaisa(row.rto_value_paisa, { locale })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {products.data && products.data.items.length === 0 ? (
              <EmptyState title={t('rto.noCompleted')} />
            ) : null}
            <Pager
              t={t}
              offset={offset}
              total={products.data?.total ?? 0}
              hasMore={Boolean(products.data?.has_more)}
              onChange={setOffset}
            />
          </Card>
        </div>

        <div className="grid2" style={{ marginTop: 18 }}>
          <Card title={t('rto.areasTitle')} hint={t('rto.areasHint')} padded={false}>
            {areas.data && areas.data.status !== 'ACTIVE' ? (
              <p className="card__hint" style={{ padding: 18 }}>
                {t('rto.areasNotReliable', {
                  coverage: pct(areas.data.coverage_basis_points ?? 0),
                  needed: pct(areas.data.min_coverage_basis_points),
                })}
              </p>
            ) : (
              <>
                <div className="tablewrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t('rto.district')}</th>
                        <th className="num">{t('rto.completed')}</th>
                        <th className="num">{t('rto.rto')}</th>
                        <th className="num">{t('rto.rate')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(areas.data?.items ?? []).map((row) => (
                        <tr key={row.label}>
                          <td>{row.label}</td>
                          <td className="num">{row.counts.completed}</td>
                          <td className="num">{row.counts.rto}</td>
                          <td className="num">
                            <RateCell counts={row.counts} t={t} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager
                  t={t}
                  offset={areaOffset}
                  total={areas.data?.total ?? 0}
                  hasMore={Boolean(areas.data?.has_more)}
                  onChange={setAreaOffset}
                />
              </>
            )}
          </Card>

          <Card title={t('rto.patternsTitle')} hint={t('rto.patternsHint')} padded={false}>
            {patterns.error ? (
              <ErrorState error={patterns.error} />
            ) : (patterns.data?.items ?? []).length === 0 ? (
              <EmptyState title={t('rto.noPatterns')} />
            ) : (
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('rto.customer')}</th>
                      <th>{t('rto.observed')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(patterns.data?.items ?? []).map((row) => (
                      <tr
                        key={row.customer_id}
                        className="table__row--clickable"
                        onClick={() => setCustomer(row.customer_id)}
                      >
                        <td>
                          <div className="table__primary">{row.name ?? '—'}</div>
                          <div className="table__sub">{row.phone_masked}</div>
                        </td>
                        <td style={{ whiteSpace: 'normal' }}>
                          {row.observations
                            .map((obs) => observation(t, obs))
                            .filter(Boolean)
                            .join(' ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <p className="card__hint" style={{ marginTop: 18 }}>
          {t('rto.ownShopOnly')}
        </p>
      </div>

      {customer ? (
        <CustomerDrawer customerId={customer} onClose={() => setCustomer(null)} />
      ) : null}
    </>
  );
}

function Pager({
  t,
  offset,
  total,
  hasMore,
  onChange,
}: {
  t: Translate;
  offset: number;
  total: number;
  hasMore: boolean;
  onChange: (offset: number) => void;
}) {
  if (total <= PAGE) return null;
  return (
    <div className="toolbar" style={{ justifyContent: 'flex-end' }}>
      <span className="card__hint">
        {t('rto.showing', {
          from: offset + 1,
          to: Math.min(offset + PAGE, total),
          total,
        })}
      </span>
      <button
        type="button"
        className="btn btn--sm"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - PAGE))}
      >
        {t('common.previous')}
      </button>
      <button
        type="button"
        className="btn btn--sm"
        disabled={!hasMore}
        onClick={() => onChange(offset + PAGE)}
      >
        {t('common.next')}
      </button>
    </div>
  );
}

/** Weekly RTO as bars; a week below the sample is drawn faint, an empty week not at all. */
function TrendChart({ points, t }: { points: Trend['points']; t: Translate }) {
  const max = Math.max(1, ...points.map((p) => p.counts.rto_rate_basis_points ?? 0));
  const width = 480;
  const height = 120;
  const slot = width / Math.max(1, points.length);
  return (
    <svg
      viewBox={`0 0 ${width} ${height + 18}`}
      width="100%"
      role="img"
      aria-label={t('rto.trendTitle')}
    >
      {points.map((point, index) => {
        const bps = point.counts.rto_rate_basis_points;
        const barHeight = bps === null ? 0 : Math.max(2, (bps / max) * height);
        return (
          <g key={point.week_start}>
            <title>
              {`${formatDate(point.week_start)}: ${t('rto.rateOf', {
                rto: point.counts.rto,
                completed: point.counts.completed,
              })}`}
            </title>
            {point.counts.completed > 0 ? (
              <rect
                x={index * slot + slot * 0.18}
                y={height - barHeight}
                width={slot * 0.64}
                height={barHeight}
                rx={3}
                fill="var(--accent, #1f3a5f)"
                opacity={point.counts.sufficient ? 1 : 0.35}
              />
            ) : null}
            <text
              x={index * slot + slot / 2}
              y={height + 13}
              textAnchor="middle"
              fontSize="9"
              fill="var(--muted-2, #888)"
            >
              {point.counts.completed}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function CustomerDrawer({ customerId, onClose }: { customerId: string; onClose: () => void }) {
  const { t } = useSession();
  const history = useApi<History>(`/analytics/rto/customers/${customerId}`);
  const data = history.data;
  return (
    <Drawer title={t('rto.customerTitle')} onClose={onClose}>
      {history.error ? <ErrorState error={history.error} onRetry={history.reload} /> : null}
      {data ? (
        <>
          <Row label={t('rto.customer')} value={data.name ?? data.phone_masked ?? '—'} />
          <Row label={t('rto.phone')} value={data.phone_masked ?? '—'} />
          <Row
            label={t('rto.rate')}
            value={
              data.counts.completed === 0
                ? t('rto.noCompleted')
                : t('rto.rateOf', { rto: data.counts.rto, completed: data.counts.completed })
            }
          />
          <Row label={t('rto.orders')} value={data.order_count} />
          <Row
            label={t('rto.delivered')}
            value={data.counts.delivered + data.counts.partial}
          />
          <Row label={t('rto.returned')} value={data.counts.returned} />
          <Row label={t('rto.courierCancelled')} value={data.counts.courier_cancelled} />
          <Row label={t('rto.onTheWay')} value={data.in_transit_count} />
          <Row label={t('rto.cancelledBefore')} value={data.cancelled_before_dispatch} />
          <Row
            label={t('rto.lastOrder')}
            value={data.last_order_at ? formatDate(data.last_order_at) : '—'}
          />
          <Row label={t('rto.band')} value={t(bandKey(data.risk_state))} />

          <h3 className="card__title" style={{ marginTop: 16 }}>
            {t('rto.observed')}
          </h3>
          {data.observations.length === 0 ? (
            <p className="card__hint">{t('rto.noObservations')}</p>
          ) : (
            <ul style={{ paddingLeft: 18, margin: '6px 0' }}>
              {data.observations.map((obs, index) => {
                const text = observation(t, obs);
                return text ? <li key={index}>{text}</li> : null;
              })}
            </ul>
          )}

          <h3 className="card__title" style={{ marginTop: 16 }}>
            {t('rto.recentTitle')}
          </h3>
          {data.recent.map((event) => (
            <Row
              key={`${event.order_number}:${event.at ?? ''}`}
              label={`${event.order_number} · ${courier(event.provider)}`}
              value={
                <>
                  <Chip
                    label={outcomeLabel(t, event.outcome)}
                    tone={
                      event.outcome === 'RTO' || event.outcome === 'LOST'
                        ? 'bad'
                        : event.outcome === 'IN_TRANSIT'
                          ? 'neutral'
                          : 'good'
                    }
                  />{' '}
                  {event.at ? formatDate(event.at) : ''}
                </>
              }
            />
          ))}
          <p className="card__hint" style={{ marginTop: 12 }}>
            {t('rto.observationsNote')}
          </p>
        </>
      ) : null}
    </Drawer>
  );
}

function outcomeLabel(t: Translate, outcome: string): string {
  const key = OUTCOMES[outcome];
  return key ? t(key) : outcome;
}

function bandKey(state: string): StringKey {
  switch (state) {
    case 'LOW':
      return 'rto.band.LOW';
    case 'MEDIUM':
      return 'rto.band.MEDIUM';
    case 'HIGH':
      return 'rto.band.HIGH';
    default:
      return 'rto.band.INSUFFICIENT_DATA';
  }
}
