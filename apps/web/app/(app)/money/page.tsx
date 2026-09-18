'use client';

import { useMemo, useState } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Tile } from '@/components/ui';
import type { Page } from '@/lib/api';
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

/**
 * Money.
 *
 * Every figure on this screen is one the API computed from the ledger. Nothing
 * here adds anything up — not the outstanding total, not the deductions, not
 * the aging bands. Two implementations of a ledger is two answers to "how much
 * am I owed?", and the seller has no way to know which one is wrong.
 *
 * That is also why the tiles are not derived from the receivables table below
 * them: the table holds one page, and a total of one page is not a total.
 */
export default function MoneyPage() {
  const { t, locale } = useSession();
  const [tab, setTab] = useState<'receivables' | 'payouts'>('receivables');

  const summary = useApi<MoneySummary>('/money/summary');
  const aging = useApi<AgingBand[]>('/money/aging');

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
                ? `${formatPaisa(summary.data.unknown_deduction_paisa, { locale })} unexplained`
                : undefined
            }
          />
        </div>

        {aging.data && aging.data.length > 0 ? (
          <Card title={t('money.aging')} padded={false}>
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
                    <tr key={band.label}>
                      <td>{band.label}</td>
                      <td className="num">{band.parcel_count}</td>
                      <td className="num">{formatPaisa(band.outstanding_paisa, { locale })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
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

            {tab === 'receivables' ? <ReceivablesTable /> : <PayoutsTable />}
          </Card>
        </div>
      </div>
    </>
  );
}

function ReceivablesTable() {
  const { t, locale } = useSession();
  const paging = useCursorStack();
  const query = useMemo(() => ({ limit: 50, cursor: paging.cursor }), [paging.cursor]);
  const { data, loading, error, reload } = useApi<Page<Receivable>>('/money/receivables', query);

  const columns: Column<Receivable>[] = [
    {
      key: 'parcel',
      header: t('money.parcel'),
      render: (row) => (
        <>
          <div className="table__primary">{row.order_number ?? row.order_id.slice(0, 8)}</div>
          <div className="table__sub">{row.provider}</div>
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
      render: (row) => (row.age_days === null ? '—' : `${row.age_days}d`),
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
      pagination={{
        canGoBack: paging.canGoBack,
        canGoForward: Boolean(data?.has_more && data?.next_cursor),
        onBack: paging.back,
        onForward: () => paging.forward(data?.next_cursor ?? null),
      }}
    />
  );
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
