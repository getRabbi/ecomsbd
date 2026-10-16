'use client';

import { useState } from 'react';

import Link from 'next/link';

import {
  PayoutsTable,
  ReceivablesTable,
  bandKey,
  bandLabel,
  type AgingBand,
  type ReceivableFilter,
} from '@/components/MoneyLedger';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Tile } from '@/components/ui';
import { courierName as baseCourierName } from '@/lib/couriers';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

function courierName(provider: string, t: Translate): string {
  return baseCourierName(provider, t('rv.manualCourier'));
}

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

        {summary.data && (summary.data.open_case_count > 0 || summary.data.unexplained_payout_paisa > 0) ? (
          <div className="notice notice--warn">
            <span>
              <strong>{t('ml.discrepancies')}</strong>{' '}
              {t('ml.discrepancySummary', {
                cases: summary.data.open_case_count,
                amount: formatPaisa(summary.data.unexplained_payout_paisa, { locale }),
              })}
            </span>
            <Link className="btn btn--sm" href="/reconciliation">
              {t('act.reconcile')}
            </Link>
          </div>
        ) : null}

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
