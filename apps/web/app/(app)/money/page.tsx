'use client';

import { useMemo, useState } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Tile } from '@/components/ui';
import type { Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface MoneySummary {
  outstanding_paisa: number;
  settled_paisa: number;
  unpaid_parcel_count: number;
  courier_charge_paisa: number;
  cod_fee_paisa: number;
  return_charge_paisa: number;
  unknown_deduction_paisa: number;
  write_off_paisa: number;
  unexplained_payout_paisa: number;
  open_case_count: number;
}

interface AgingBand {
  label: string;
  min_days: number;
  max_days: number | null;
  parcel_count: number;
  outstanding_paisa: number;
}

interface PayoutDelay {
  provider: string;
  samples: number;
  average_days: number;
  median_days: number;
  reliable: boolean;
}

interface CourierBalance {
  provider: string;
  outstanding_paisa: number;
  parcel_count: number;
  delivered_unpaid_count: number;
  delivered_unpaid_paisa: number;
  overdue_count: number;
  overdue_paisa: number;
  oldest_age_days: number | null;
  in_transit_count: number;
  in_transit_paisa: number;
  last_payment_on: string | null;
  payout_delay: PayoutDelay | null;
}

interface Cashflow {
  since: string;
  until: string;
  received_paisa: number;
  receivable_paisa: number;
  overdue_paisa: number;
  overdue_after_days: number;
  in_transit_paisa: number;
  in_transit_count: number;
  forecast: {
    quality: 'ESTIMATE';
    windows: { key: string; parcel_count: number; amount_paisa: number }[];
  };
  overall_delay: PayoutDelay | null;
}

interface Receivable {
  id: string;
  order_id: string;
  order_number: string | null;
  provider: string;
  status: string;
  collectible_paisa: number;
  settled_paisa: number;
  deduction_paisa: number;
  outstanding_paisa: number;
  age_days: number | null;
}

interface Payout {
  id: string;
  provider: string;
  provider_reference: string | null;
  status: string;
  total_paisa: number;
  applied_paisa: number;
  unexplained_paisa: number;
  paid_on: string | null;
  received_at: string;
}

/** A receivables filter, set by clicking a courier or an aging band. */
interface ReceivableFilter {
  provider: string;
  band: string;
  unpaidOnly: boolean;
}

const COURIERS = ['steadfast', 'pathao', 'redx', 'manual'] as const;

type Translate = (key: StringKey, vars?: Record<string, string | number>) => string;

/**
 * Money.
 *
 * Every figure on this screen is one the API computed from the ledger or the
 * receivables. Nothing here adds anything up — not the outstanding total, not
 * the aging bands, not a courier's balance. Two implementations of a ledger is
 * two answers to "how much am I owed?", and the seller has no way to know
 * which one is wrong.
 *
 * Facts and the one estimate are kept apart and labelled: received, with
 * couriers, overdue and on the road are facts; "when it may arrive" is the
 * backend's estimate, which only spreads money already owed across time.
 *
 * The tiles are not derived from the receivables table below them: the table
 * holds one page, and a total of one page is not a total. Clicking a courier
 * or an aging band filters that table on the server.
 */
export default function MoneyPage() {
  const { t, locale } = useSession();
  const [tab, setTab] = useState<'receivables' | 'payouts'>('receivables');
  const [filter, setFilter] = useState<ReceivableFilter>({
    provider: '',
    band: '',
    unpaidOnly: false,
  });

  const summary = useApi<MoneySummary>('/money/summary');
  const aging = useApi<AgingBand[]>('/money/aging');
  const cashflow = useApi<Cashflow>('/money/cashflow');
  const couriers = useApi<CourierBalance[]>('/money/couriers');

  function focus(next: Partial<ReceivableFilter>) {
    setFilter((current) => ({ ...current, ...next }));
    setTab('receivables');
  }

  return (
    <>
      <PageHeader title={t('money.title')} subtitle={t('money.subtitle')} />

      <div className="content">
        <div className="tiles">
          <Tile
            label={t('money.outstanding')}
            value={
              summary.loading ? '…' : formatPaisa(summary.data?.outstanding_paisa, { locale })
            }
          />
          <Tile
            label={t('money.settled')}
            value={summary.loading ? '…' : formatPaisa(summary.data?.settled_paisa, { locale })}
          />
          <Tile
            label={t('money.unpaidParcels')}
            value={summary.loading ? '…' : `${summary.data?.unpaid_parcel_count ?? 0}`}
          />
          <Tile
            label={t('money.deductions')}
            value={
              summary.loading
                ? '…'
                : // The API's own buckets, shown as the sum it already
                  // reported per bucket — not a total this screen invented.
                  formatPaisa(
                    (summary.data?.courier_charge_paisa ?? 0) +
                      (summary.data?.cod_fee_paisa ?? 0) +
                      (summary.data?.return_charge_paisa ?? 0),
                    { locale },
                  )
            }
            hint={
              summary.data && summary.data.unknown_deduction_paisa > 0
                ? t('money.unexplainedHint', {
                    amount: formatPaisa(summary.data.unknown_deduction_paisa, { locale }),
                  })
                : undefined
            }
          />
        </div>

        {cashflow.data ? <CashflowSection flow={cashflow.data} /> : null}

        {couriers.data && couriers.data.length > 0 ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('cf.couriers')} hint={t('cf.couriersHint')} padded={false}>
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('cf.courier')}</th>
                      <th className="num">{t('cf.outstanding')}</th>
                      <th className="num">{t('cf.deliveredUnpaid')}</th>
                      <th className="num">{t('cf.overdue')}</th>
                      <th className="num">{t('cf.oldest')}</th>
                      <th className="num">{t('cf.inTransit')}</th>
                      <th>{t('cf.lastPaid')}</th>
                      <th>{t('cf.delay')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {couriers.data.map((row) => (
                      <tr
                        key={row.provider}
                        className="table__row--clickable"
                        onClick={() => focus({ provider: row.provider })}
                      >
                        <td className="table__primary">{courierName(row.provider, t)}</td>
                        <td className="num">{formatPaisa(row.outstanding_paisa, { locale })}</td>
                        <td className="num">{row.delivered_unpaid_count}</td>
                        <td className={`num${row.overdue_paisa > 0 ? ' text--bad' : ''}`}>
                          {formatPaisa(row.overdue_paisa, { locale })}
                        </td>
                        <td className="num">
                          {row.oldest_age_days === null
                            ? '—'
                            : t('cf.days', { days: row.oldest_age_days })}
                        </td>
                        <td className="num">{formatPaisa(row.in_transit_paisa, { locale })}</td>
                        <td>
                          {row.last_payment_on
                            ? formatDate(row.last_payment_on, { locale })
                            : t('cf.never')}
                        </td>
                        <td>
                          {row.payout_delay && row.payout_delay.reliable
                            ? t('cf.delayValue', {
                                days: row.payout_delay.median_days,
                                count: row.payout_delay.samples,
                              })
                            : t('cf.notEnough')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        ) : null}

        {aging.data && aging.data.length > 0 ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('money.aging')} hint={t('money.agingHint')} padded={false}>
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('money.age')}</th>
                      <th className="num">{t('money.parcel')}</th>
                      <th className="num">{t('money.balance')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {aging.data.map((band) => (
                      <tr
                        key={band.label}
                        className="table__row--clickable"
                        onClick={() => focus({ band: bandKey(band) })}
                      >
                        <td>{bandLabel(band, t)}</td>
                        <td className="num">{band.parcel_count}</td>
                        <td className="num">{formatPaisa(band.outstanding_paisa, { locale })}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        ) : null}

        <div style={{ marginTop: 18 }}>
          <Card padded={false}>
            <div className="toolbar">
              <button
                type="button"
                className={`btn btn--sm${tab === 'receivables' ? ' btn--primary' : ''}`}
                onClick={() => setTab('receivables')}
              >
                {t('money.receivables')}
              </button>
              <button
                type="button"
                className={`btn btn--sm${tab === 'payouts' ? ' btn--primary' : ''}`}
                onClick={() => setTab('payouts')}
              >
                {t('money.payouts')}
              </button>
              <span className="bulkbar__hint">{t('money.fromBackend')}</span>
            </div>

            {tab === 'receivables' ? (
              <ReceivablesTable
                filter={filter}
                onFilter={setFilter}
                bands={aging.data ?? []}
              />
            ) : (
              <PayoutsTable />
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function CashflowSection({ flow }: { flow: Cashflow }) {
  const { t, locale } = useSession();
  const windows = flow.forecast.windows.filter((window) => window.amount_paisa > 0);

  return (
    <div style={{ marginTop: 18 }}>
      <Card title={t('cf.title')} actions={<Chip label={t('cf.fact')} tone="good" />}>
        <div className="tiles">
          <Tile label={t('cf.received')} value={formatPaisa(flow.received_paisa, { locale })} />
          <Tile
            label={t('cf.receivable')}
            value={formatPaisa(flow.receivable_paisa, { locale })}
            hint={t('cf.receivableHint')}
          />
          <Tile
            label={t('cf.overdue')}
            value={formatPaisa(flow.overdue_paisa, { locale })}
            hint={t('cf.overdueHint', { days: flow.overdue_after_days })}
          />
          <Tile
            label={t('cf.inTransit')}
            value={formatPaisa(flow.in_transit_paisa, { locale })}
            hint={t('cf.inTransitHint', { count: flow.in_transit_count })}
          />
        </div>
      </Card>

      <div style={{ marginTop: 18 }}>
        <Card
          title={t('cf.forecast')}
          hint={t('cf.forecastNote')}
          actions={<Chip label={t('cf.estimate')} tone="warn" />}
          padded={false}
        >
          {windows.length > 0 ? (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('cf.window')}</th>
                    <th className="num">{t('cf.parcels')}</th>
                    <th className="num">{t('cf.amount')}</th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map((window) => (
                    <tr key={window.key}>
                      <td>{t(`cf.win.${window.key}` as StringKey)}</td>
                      <td className="num">{window.parcel_count}</td>
                      <td className="num">{formatPaisa(window.amount_paisa, { locale })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="card__hint" style={{ padding: '10px 16px' }}>
            {flow.overall_delay
              ? t('cf.usualDelay', {
                  days: flow.overall_delay.median_days,
                  count: flow.overall_delay.samples,
                })
              : t('cf.noHistory')}
          </p>
        </Card>
      </div>
    </div>
  );
}

function ReceivablesTable({
  filter,
  onFilter,
  bands,
}: {
  filter: ReceivableFilter;
  onFilter: (filter: ReceivableFilter) => void;
  bands: AgingBand[];
}) {
  const { t, locale } = useSession();
  const paging = useCursorStack();
  const band = bands.find((candidate) => bandKey(candidate) === filter.band);

  const query = useMemo(
    () => ({
      limit: 50,
      cursor: paging.cursor,
      provider: filter.provider || undefined,
      min_age_days: band ? band.min_days : undefined,
      max_age_days: band && band.max_days !== null ? band.max_days : undefined,
      // "Delivered, unpaid" is the same status the summary counts.
      status: filter.unpaidOnly ? 'ELIGIBLE' : undefined,
      open_only: filter.provider || band ? true : undefined,
    }),
    [paging.cursor, filter.provider, filter.unpaidOnly, band],
  );
  const { data, loading, error, reload } = useApi<Page<Receivable>>('/money/receivables', query);

  function change(next: Partial<ReceivableFilter>) {
    onFilter({ ...filter, ...next });
    paging.reset();
  }

  const columns: Column<Receivable>[] = [
    {
      key: 'parcel',
      header: t('money.parcel'),
      render: (row) => (
        <>
          <div className="table__primary">{row.order_number ?? row.order_id.slice(0, 8)}</div>
          <div className="table__sub">{courierName(row.provider, t)}</div>
        </>
      ),
    },
    { key: 'status', header: t('recon.state'), render: (row) => <Chip label={row.status} /> },
    {
      key: 'collectible',
      header: t('money.collectible'),
      numeric: true,
      render: (row) => formatPaisa(row.collectible_paisa, { locale }),
    },
    {
      key: 'settled',
      header: t('money.received'),
      numeric: true,
      render: (row) => formatPaisa(row.settled_paisa, { locale }),
    },
    {
      key: 'outstanding',
      header: t('money.balance'),
      numeric: true,
      // The API's own balance. Not collectible minus settled: deductions,
      // adjustments and write-offs all move it, and re-deriving it here would
      // disagree with the ledger for exactly the parcels that matter.
      render: (row) => formatPaisa(row.outstanding_paisa, { locale }),
    },
    {
      key: 'age',
      header: t('money.age'),
      numeric: true,
      render: (row) => (row.age_days === null ? '—' : t('cf.days', { days: row.age_days })),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={data?.items ?? []}
      loading={loading}
      error={error}
      onRetry={reload}
      emptyTitle={t('money.emptyReceivables')}
      toolbar={
        <>
          <select
            className="select"
            value={filter.provider}
            onChange={(event) => change({ provider: event.target.value })}
            aria-label={t('cf.courier')}
          >
            <option value="">{t('rv.anyCourier')}</option>
            {COURIERS.map((value) => (
              <option key={value} value={value}>
                {courierName(value, t)}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={filter.band}
            onChange={(event) => change({ band: event.target.value })}
            aria-label={t('money.age')}
          >
            <option value="">{t('money.anyAge')}</option>
            {bands.map((value) => (
              <option key={value.label} value={bandKey(value)}>
                {bandLabel(value, t)}
              </option>
            ))}
          </select>
          <label className="card__hint" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={filter.unpaidOnly}
              onChange={(event) => change({ unpaidOnly: event.target.checked })}
            />
            {t('money.unpaidOnly')}
          </label>
        </>
      }
      pagination={{
        canGoBack: paging.canGoBack,
        canGoForward: Boolean(data?.has_more && data?.next_cursor),
        onBack: paging.back,
        onForward: () => paging.forward(data?.next_cursor ?? null),
      }}
    />
  );
}

function bandKey(band: AgingBand): string {
  return `${band.min_days}-${band.max_days ?? ''}`;
}

/** The band in the reader's language, from its bounds rather than the API's English label. */
function bandLabel(band: AgingBand, t: Translate): string {
  return band.max_days === null
    ? t('money.bandOver', { days: band.min_days - 1 })
    : t('money.bandRange', { min: band.min_days, max: band.max_days });
}

function courierName(provider: string, t: Translate): string {
  switch (provider) {
    case 'steadfast':
      return 'Steadfast';
    case 'pathao':
      return 'Pathao';
    case 'redx':
      return 'RedX';
    case 'manual':
      return t('rv.manualCourier');
    default:
      return provider;
  }
}


function PayoutsTable() {
  const { t, locale } = useSession();
  const paging = useCursorStack();
  const query = useMemo(() => ({ limit: 50, cursor: paging.cursor }), [paging.cursor]);
  const { data, loading, error, reload } = useApi<Page<Payout>>('/payouts', query);

  const columns: Column<Payout>[] = [
    {
      key: 'provider',
      header: t('cour.provider'),
      render: (row) => (
        <>
          <div className="table__primary">{row.provider}</div>
          {row.provider_reference ? (
            <div className="table__sub">{row.provider_reference}</div>
          ) : null}
        </>
      ),
    },
    {
      key: 'paid_on',
      header: t('money.paidOn'),
      render: (row) => formatDate(row.paid_on ?? row.received_at, { locale }),
    },
    {
      key: 'total',
      header: t('money.total'),
      numeric: true,
      render: (row) => formatPaisa(row.total_paisa, { locale }),
    },
    {
      key: 'applied',
      header: t('money.applied'),
      numeric: true,
      render: (row) => formatPaisa(row.applied_paisa, { locale }),
    },
    {
      key: 'unexplained',
      header: t('money.unexplained'),
      numeric: true,
      render: (row) =>
        row.unexplained_paisa > 0 ? (
          // Money that arrived but could not be tied to a parcel. Kept visible
          // rather than folded into the total, because it is the figure a
          // seller has to act on.
          <Chip label={formatPaisa(row.unexplained_paisa, { locale })} tone="warn" />
        ) : (
          formatPaisa(0, { locale })
        ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={data?.items ?? []}
      loading={loading}
      error={error}
      onRetry={reload}
      emptyTitle={t('money.emptyPayouts')}
      pagination={{
        canGoBack: paging.canGoBack,
        canGoForward: Boolean(data?.has_more && data?.next_cursor),
        onBack: paging.back,
        onForward: () => paging.forward(data?.next_cursor ?? null),
      }}
    />
  );
}
