'use client';

import { useCallback, useState, type ReactNode } from 'react';

import { EmptyState, ErrorState, LoadingRows } from '@/components/ui';
import { useSession } from '@/lib/session';

/**
 * The table every list screen is built from.
 *
 * It exists so the things that are easy to get subtly wrong are got right once:
 * a loading state that does not blank the previous page, an error that shows
 * what the server said, an empty state that is distinguishable from a failed
 * one, and pagination that walks opaque cursors rather than inventing page
 * numbers the API never promised.
 *
 * Deliberately not a generic grid. No client-side sorting, no client-side
 * filtering, no "export everything" — all three would be lies on a table that
 * only holds one page, and the server is the only place that can see the rest.
 */

export interface Column<T> {
  key: string;
  header: string;
  /** Right-aligned and tabular. Use for anything a seller compares down a column. */
  numeric?: boolean;
  render: (row: T) => ReactNode;
}

export function DataTable<T extends { id: string }>({
  columns,
  rows,
  loading,
  error,
  onRetry,
  emptyTitle,
  emptyHint,
  onRowClick,
  selection,
  pagination,
  toolbar,
}: {
  columns: Column<T>[];
  rows: T[];
  loading: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyTitle: string;
  emptyHint?: string;
  onRowClick?: (row: T) => void;
  selection?: {
    selected: Set<string>;
    onToggle: (id: string) => void;
    onToggleAll: (checked: boolean) => void;
  };
  pagination?: {
    canGoBack: boolean;
    canGoForward: boolean;
    onBack: () => void;
    onForward: () => void;
  };
  toolbar?: ReactNode;
}) {
  const { t } = useSession();

  const allSelected =
    selection !== undefined &&
    rows.length > 0 &&
    rows.every((row) => selection.selected.has(row.id));

  // Only when there is nothing to fall back to. A failed refresh over a
  // populated table keeps the table: yesterday's rows beat an error page.
  if (error && rows.length === 0 && !loading) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }

  return (
    <>
      {toolbar ? <div className="toolbar">{toolbar}</div> : null}

      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              {selection ? (
                <th style={{ width: 36 }}>
                  <input
                    type="checkbox"
                    checked={allSelected}
                    disabled={rows.length === 0}
                    onChange={(event) => selection.onToggleAll(event.target.checked)}
                    // This page, not the whole shop.
                    aria-label={`${t('common.selected')} — ${rows.length}`}
                  />
                </th>
              ) : null}
              {columns.map((column) => (
                <th key={column.key} className={column.numeric ? 'num' : undefined}>
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>

          {loading ? (
            <LoadingRows rows={8} columns={columns.length + (selection ? 1 : 0)} />
          ) : (
            <tbody>
              {rows.map((row) => {
                const isSelected = selection?.selected.has(row.id) ?? false;
                return (
                  <tr
                    key={row.id}
                    data-selected={isSelected}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    style={onRowClick ? { cursor: 'pointer' } : undefined}
                  >
                    {selection ? (
                      <td onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => selection.onToggle(row.id)}
                          aria-label={row.id}
                        />
                      </td>
                    ) : null}
                    {columns.map((column) => (
                      <td key={column.key} className={column.numeric ? 'num' : undefined}>
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          )}
        </table>

        {!loading && rows.length === 0 ? (
          <EmptyState title={emptyTitle} hint={emptyHint} />
        ) : null}
      </div>

      {pagination ? (
        <div className="pagination">
          <button
            type="button"
            className="btn btn--sm"
            disabled={!pagination.canGoBack || loading}
            onClick={pagination.onBack}
          >
            {t('common.previous')}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!pagination.canGoForward || loading}
            onClick={pagination.onForward}
          >
            {t('common.next')}
          </button>
        </div>
      ) : null}
    </>
  );
}

/**
 * Cursor paging state.
 *
 * A stack of the cursors walked, because the API's cursor is opaque: there is
 * no page number to jump to, and inventing an offset would be guessing at
 * something the server never promised.
 */
export function useCursorStack() {
  const [stack, setStack] = useState<string[]>([]);

  const cursor = stack.length > 0 ? stack[stack.length - 1] : undefined;

  const forward = useCallback((next: string | null) => {
    if (next) {
      setStack((current) => [...current, next]);
    }
  }, []);

  const back = useCallback(() => setStack((current) => current.slice(0, -1)), []);
  const reset = useCallback(() => setStack([]), []);

  return { cursor, canGoBack: stack.length > 0, forward, back, reset };
}
