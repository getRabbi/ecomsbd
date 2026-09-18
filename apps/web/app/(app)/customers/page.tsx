'use client';

import { useMemo, useState } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui';
import type { Page } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

interface Customer {
  id: string;
  name: string | null;
  /** Masked by the API. The full number is never sent to a client. */
  phone_masked: string;
  flag: string;
  order_count: number;
  delivered_count: number;
  returned_count: number;
  success_rate_basis_points: number | null;
  realized_revenue_paisa: number;
}

/**
 * Customers.
 *
 * Phone numbers are shown masked, because that is the only form the API sends.
 * Revealing one is a separate, audited endpoint on the phone — a desktop list
 * of four hundred customers is exactly where a full contact export should not
 * be one click away.
 *
 * The success rate is the API's own figure, in basis points, divided only for
 * display. It is not recomputed from the delivered and returned counts on the
 * row: those are counts of terminal parcels and the rate has its own
 * definition, so deriving it here would quietly disagree with the phone.
 */
export default function CustomersPage() {
  const { t, locale } = useSession();
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search);
  const paging = useCursorStack();

  const query = useMemo(
    () => ({ limit: 50, cursor: paging.cursor, search: debounced.trim() || undefined }),
    [paging.cursor, debounced],
  );

  const { data, loading, error, reload } = useApi<Page<Customer>>('/customers', query);
  const rows = data?.items ?? [];

  const columns: Column<Customer>[] = [
    {
      key: 'name',
      header: t('cust.name'),
      render: (row) => (
        <>
          <div className="table__primary">{row.name ?? '—'}</div>
          <div className="table__sub">{row.phone_masked}</div>
        </>
      ),
    },
    { key: 'orders', header: t('cust.orders'), numeric: true, render: (row) => row.order_count },
    {
      key: 'delivered',
      header: t('cust.delivered'),
      numeric: true,
      render: (row) => row.delivered_count,
    },
    {
      key: 'returned',
      header: t('cust.returned'),
      numeric: true,
      render: (row) => row.returned_count,
    },
    {
      key: 'success',
      header: t('cust.success'),
      numeric: true,
      render: (row) =>
        row.success_rate_basis_points === null
          ? '—'
          : `${(row.success_rate_basis_points / 100).toFixed(0)}%`,
    },
    {
      key: 'revenue',
      header: t('cust.revenue'),
      numeric: true,
      render: (row) => formatPaisa(row.realized_revenue_paisa, { locale }),
    },
  ];

  return (
    <>
      <PageHeader title={t('cust.title')} subtitle={t('cust.subtitle')} />
      <div className="content">
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={rows}
            loading={loading}
            error={error}
            onRetry={reload}
            emptyTitle={t('cust.empty')}
            toolbar={
              <input
                className="input input--search"
                type="search"
                placeholder={t('cust.searchHint')}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  paging.reset();
                }}
                aria-label={t('common.search')}
              />
            }
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
