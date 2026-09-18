'use client';

import { useMemo } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, type Tone } from '@/components/ui';
import type { Page } from '@/lib/api';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface Case {
  id: string;
  kind: string;
  status: string;
  priority: string;
  amount_paisa: number;
  summary: string;
  opened_at: string;
  resolved_at: string | null;
}

/**
 * Reconciliation.
 *
 * The list of money that did not arrive, arrived short, or could not be placed.
 * Every case here was opened by the backend's reconciliation engine, which is
 * the only thing that may decide a payout matches a parcel — this screen shows
 * its conclusions and never reaches one of its own.
 *
 * Resolving a case is not offered here. It writes to an append-only financial
 * ledger and needs the context the mobile flow already gives: which parcel,
 * which payout line, and what the seller is asserting. A one-click "resolve"
 * on a desktop list is how a case gets closed against the wrong parcel.
 */
export default function ReconciliationPage() {
  const { t, locale } = useSession();
  const paging = useCursorStack();

  const query = useMemo(() => ({ limit: 50, cursor: paging.cursor }), [paging.cursor]);
  const { data, loading, error, reload } = useApi<Page<Case>>('/reconciliation/cases', query);

  const columns: Column<Case>[] = [
    {
      key: 'kind',
      header: t('recon.kind'),
      render: (row) => (
        <>
          <div className="table__primary">{humanise(row.kind)}</div>
          <div className="table__sub">{row.summary}</div>
        </>
      ),
    },
    {
      key: 'amount',
      header: t('recon.amount'),
      numeric: true,
      render: (row) => formatPaisa(row.amount_paisa, { locale }),
    },
    {
      key: 'status',
      header: t('recon.state'),
      render: (row) => <Chip label={humanise(row.status)} tone={toneForStatus(row.status)} />,
    },
    {
      key: 'opened',
      header: t('recon.opened'),
      render: (row) => formatDate(row.opened_at, { locale }),
    },
  ];

  return (
    <>
      <PageHeader title={t('recon.title')} subtitle={t('recon.subtitle')} />
      <div className="content">
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={data?.items ?? []}
            loading={loading}
            error={error}
            onRetry={reload}
            emptyTitle={t('recon.empty')}
            emptyHint={t('recon.emptyHint')}
            pagination={{
              canGoBack: paging.canGoBack,
              canGoForward: Boolean(data?.has_more && data?.next_cursor),
              onBack: paging.back,
              onForward: () => paging.forward(data?.next_cursor ?? null),
            }}
          />
        </Card>
      </div>
    </>
  );
}

/** `SHORT_PAYMENT` → `short payment`. Shown verbatim for a kind we do not know. */
function humanise(value: string): string {
  return value.replaceAll('_', ' ').toLowerCase();
}

function toneForStatus(status: string): Tone {
  switch (status) {
    case 'RESOLVED':
    case 'CLOSED':
      return 'good';
    case 'OPEN':
      return 'warn';
    default:
      return 'neutral';
  }
}
