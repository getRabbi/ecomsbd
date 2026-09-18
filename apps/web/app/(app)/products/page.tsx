'use client';

import { useMemo, useState } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip } from '@/components/ui';
import type { Page } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

interface Product {
  id: string;
  name: string;
  sku: string | null;
  cost_paisa: number;
  default_selling_price_paisa: number;
  stock_tracking_enabled: boolean;
  stock_on_hand: number;
  low_stock_threshold: number | null;
  is_low_stock: boolean;
  is_active: boolean;
}

/**
 * Products.
 *
 * `is_low_stock` is the API's own judgement, not a comparison done here. The
 * threshold is per product and can be null, and a shop that turns off stock
 * tracking has no low-stock state at all — encoding those rules a second time
 * in the browser would mean two answers to "should I reorder?".
 */
export default function ProductsPage() {
  const { t, locale } = useSession();
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search);
  const paging = useCursorStack();

  const query = useMemo(
    () => ({ limit: 50, cursor: paging.cursor, search: debounced.trim() || undefined }),
    [paging.cursor, debounced],
  );

  const { data, loading, error, reload } = useApi<Page<Product>>('/products', query);
  const rows = data?.items ?? [];

  const columns: Column<Product>[] = [
    {
      key: 'name',
      header: t('prod.name'),
      render: (row) => (
        <>
          <div className="table__primary">{row.name}</div>
          {row.sku ? <div className="table__sub">{row.sku}</div> : null}
        </>
      ),
    },
    {
      key: 'cost',
      header: t('prod.cost'),
      numeric: true,
      render: (row) => formatPaisa(row.cost_paisa, { locale }),
    },
    {
      key: 'price',
      header: t('prod.price'),
      numeric: true,
      render: (row) => formatPaisa(row.default_selling_price_paisa, { locale }),
    },
    {
      key: 'stock',
      header: t('prod.stock'),
      numeric: true,
      render: (row) =>
        row.stock_tracking_enabled ? (
          <>
            {row.stock_on_hand}
            {row.is_low_stock ? (
              <>
                {' '}
                <Chip label={t('prod.lowStock')} tone="warn" />
              </>
            ) : null}
          </>
        ) : (
          // Not zero. A shop that does not track stock has no number here, and
          // showing 0 would read as "sold out".
          '—'
        ),
    },
  ];

  return (
    <>
      <PageHeader title={t('prod.title')} subtitle={t('prod.subtitle')} />
      <div className="content">
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={rows}
            loading={loading}
            error={error}
            onRetry={reload}
            emptyTitle={t('prod.empty')}
            toolbar={
              <input
                className="input input--search"
                type="search"
                placeholder={t('prod.searchHint')}
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
