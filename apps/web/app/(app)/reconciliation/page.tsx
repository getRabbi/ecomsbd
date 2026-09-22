'use client';

import { useMemo, useState } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, ErrorState, Row, Tile, type Tone } from '@/components/ui';
import { api, ApiError, type Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

/**
 * Reconciliation.
 *
 * The desktop review surface for what each courier should have paid, what it
 * paid, and what is different — parcel by parcel, with the cases that need a
 * person beside them.
 *
 * Every figure on this page arrives from the backend's reconciliation engine,
 * which derives it from the ledger's own records. This page filters, pages and
 * displays; it never adds anything up. Filtering and paging happen on the
 * server, so the browser only ever holds one page of a shop's history.
 *
 * Actions — accepting a courier's charges, closing, reopening or annotating a
 * case — are made from a drawer that shows the parcel's statement rows and the
 * case's history first, so a decision is never taken from a bare list row. The
 * backend enforces who may take them.
 */

type ItemStatus =
  | 'MATCHED'
  | 'PARTIAL'
  | 'UNMATCHED'
  | 'DUPLICATE'
  | 'AMOUNT_MISMATCH'
  | 'CHARGE_MISMATCH'
  | 'MISSING_COD'
  | 'RETURN_ADJUSTMENT'
  | 'NEEDS_REVIEW';

type CaseStatus = 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'DISMISSED';

const ITEM_STATUSES: ItemStatus[] = [
  'MATCHED',
  'PARTIAL',
  'AMOUNT_MISMATCH',
  'CHARGE_MISMATCH',
  'MISSING_COD',
  'RETURN_ADJUSTMENT',
  'NEEDS_REVIEW',
  'UNMATCHED',
  'DUPLICATE',
];

const CASE_STATUSES: CaseStatus[] = ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED'];

const CASE_KINDS = [
  'DELIVERED_BUT_UNPAID',
  'UNDERPAID',
  'OVERPAID',
  'UNKNOWN_DEDUCTION',
  'DUPLICATE_PAYOUT_LINE',
  'UNMAPPABLE_PAYOUT',
  'STALE_IN_TRANSIT',
  'RETURNED_NOT_RESTOCKED',
  'CHARGE_MISMATCH',
  'MISSING_COD',
  'RETURN_CHARGE_MISMATCH',
] as const;

const COURIERS = ['steadfast', 'pathao', 'redx', 'manual'] as const;

interface Summary {
  matched: number;
  discrepancies: number;
  unmatched: number;
  expected_paisa: number;
  actual_paisa: number;
  difference_paisa: number;
  unmatched_paisa: number;
  charges_pending_paisa: number;
  open_cases: number;
  open_case_paisa: number;
}

interface Item {
  id: string;
  status: ItemStatus;
  provider: string;
  case_id: string | null;
  case_status: CaseStatus | null;
  merchant_reference: string | null;
  tracking_code: string | null;
  settlement_date: string | null;
  expected_cod_paisa: number | null;
  actual_cod_paisa: number;
  expected_charge_paisa: number | null;
  expected_charge_source: string | null;
  actual_charge_paisa: number;
  expected_net_paisa: number | null;
  actual_net_paisa: number;
  difference_paisa: number | null;
  charges_pending_paisa: number;
  detail: { unknown_deduction?: boolean };
}

interface ItemDetail extends Item {
  lines: {
    id: string;
    row_number: number;
    amount_paisa: number;
    status: string;
    merchant_reference: string | null;
    tracking_code: string | null;
  }[];
  adjustments: {
    id: string;
    type: string;
    amount_paisa: number;
    provider_label: string | null;
    accepted_at?: string | null;
  }[];
}

interface Case {
  id: string;
  kind: string;
  status: CaseStatus;
  priority: string;
  amount_paisa: number;
  summary: string;
  opened_at: string;
  resolved_at: string | null;
  resolution: string | null;
}

interface CaseDetail extends Case {
  events: {
    id: string;
    action: string;
    note: string | null;
    actor_user_id: string | null;
    created_at: string;
  }[];
  item: Item | null;
}

export default function ReconciliationPage() {
  const { t, locale } = useSession();
  const [tab, setTab] = useState<'parcels' | 'cases'>('parcels');
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [openCaseId, setOpenCaseId] = useState<string | null>(null);

  const summary = useApi<Summary>('/reconciliation/summary');

  return (
    <>
      <PageHeader title={t('recon.title')} subtitle={t('rv.subtitle')} />
      <div className="content">
        {summary.data ? (
          <div className="tiles">
            <Tile
              label={t('rv.expected')}
              value={formatPaisa(summary.data.expected_paisa, { locale })}
              hint={t('rv.countsHint', {
                matched: summary.data.matched,
                discrepancies: summary.data.discrepancies,
                unmatched: summary.data.unmatched,
              })}
            />
            <Tile
              label={t('rv.actual')}
              value={formatPaisa(summary.data.actual_paisa, { locale })}
              hint={
                summary.data.unmatched_paisa > 0
                  ? t('rv.unplacedHint', {
                      amount: formatPaisa(summary.data.unmatched_paisa, { locale }),
                    })
                  : undefined
              }
            />
            <Tile
              label={t('rv.difference')}
              value={signedPaisa(summary.data.difference_paisa, locale)}
              hint={
                summary.data.charges_pending_paisa > 0
                  ? t('rv.pendingHint', {
                      amount: formatPaisa(summary.data.charges_pending_paisa, { locale }),
                    })
                  : undefined
              }
            />
            <Tile
              label={t('rv.openCases')}
              value={String(summary.data.open_cases)}
              hint={formatPaisa(summary.data.open_case_paisa, { locale })}
            />
          </div>
        ) : null}

        <div className="toolbar" role="tablist" style={{ padding: 0, marginBottom: 12 }}>
          {(['parcels', 'cases'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`btn btn--sm${tab === value ? ' btn--primary' : ''}`}
              onClick={() => setTab(value)}
            >
              {t(value === 'parcels' ? 'rv.tabParcels' : 'rv.tabCases')}
            </button>
          ))}
        </div>

        {tab === 'parcels' ? (
          <ItemsTable onOpen={setOpenItemId} />
        ) : (
          <CasesTable onOpen={setOpenCaseId} />
        )}
      </div>

      {openItemId ? (
        <ItemDrawer
          itemId={openItemId}
          onClose={() => setOpenItemId(null)}
          onOpenCase={(caseId) => {
            setOpenItemId(null);
            setOpenCaseId(caseId);
          }}
          onChanged={summary.reload}
        />
      ) : null}
      {openCaseId ? (
        <CaseDrawer
          caseId={openCaseId}
          onClose={() => setOpenCaseId(null)}
          onChanged={summary.reload}
        />
      ) : null}
    </>
  );
}

function ItemsTable({ onOpen }: { onOpen: (id: string) => void }) {
  const { t, locale } = useSession();
  const paging = useCursorStack();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<string>('discrepancies');
  const [provider, setProvider] = useState('');
  const [caseStatus, setCaseStatus] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debouncedSearch = useDebounced(search);

  const query = useMemo(
    () => ({
      limit: 50,
      cursor: paging.cursor,
      q: debouncedSearch || undefined,
      discrepancies_only: status === 'discrepancies' ? true : undefined,
      status: status && status !== 'discrepancies' ? status : undefined,
      provider: provider || undefined,
      case_status: caseStatus || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }),
    [paging.cursor, debouncedSearch, status, provider, caseStatus, dateFrom, dateTo],
  );
  const { data, loading, error, reload } = useApi<Page<Item>>('/reconciliation/items', query);

  // Any filter change starts again from the first page: a cursor from one
  // filter means nothing under another.
  const change =
    <T,>(setter: (value: T) => void) =>
    (value: T) => {
      setter(value);
      paging.reset();
    };

  const columns: Column<Item>[] = [
    {
      key: 'parcel',
      header: t('rv.parcel'),
      render: (row) => (
        <>
          <div className="table__primary">
            {row.merchant_reference ?? row.tracking_code ?? '—'}
          </div>
          {row.tracking_code && row.merchant_reference ? (
            <div className="table__sub">{row.tracking_code}</div>
          ) : null}
        </>
      ),
    },
    { key: 'courier', header: t('rv.courier'), render: (row) => courierName(row.provider, t) },
    {
      key: 'date',
      header: t('rv.date'),
      render: (row) => formatDate(row.settlement_date, { locale }),
    },
    {
      key: 'status',
      header: t('rv.status'),
      render: (row) => <Chip label={t(`rv.st.${row.status}` as StringKey)} tone={itemTone(row.status)} />,
    },
    {
      key: 'expected',
      header: t('rv.expected'),
      numeric: true,
      render: (row) => formatPaisa(row.expected_net_paisa, { locale }),
    },
    {
      key: 'actual',
      header: t('rv.actual'),
      numeric: true,
      render: (row) => formatPaisa(row.actual_net_paisa, { locale }),
    },
    {
      key: 'difference',
      header: t('rv.difference'),
      numeric: true,
      render: (row) => (
        <span className={differenceClass(row.difference_paisa)}>
          {row.difference_paisa === null ? '—' : signedPaisa(row.difference_paisa, locale)}
        </span>
      ),
    },
    {
      key: 'case',
      header: t('rv.case'),
      render: (row) =>
        row.case_status ? (
          <Chip label={t(`rv.cs.${row.case_status}` as StringKey)} tone={caseTone(row.case_status)} />
        ) : (
          '—'
        ),
    },
  ];

  return (
    <Card padded={false}>
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        loading={loading}
        error={error}
        onRetry={reload}
        onRowClick={(row) => onOpen(row.id)}
        emptyTitle={t('rv.emptyItems')}
        emptyHint={t('rv.emptyItemsHint')}
        toolbar={
          <>
            <input
              className="input input--search"
              type="search"
              placeholder={t('rv.searchHint')}
              value={search}
              onChange={(event) => change(setSearch)(event.target.value)}
              aria-label={t('common.search')}
            />
            <select
              className="select"
              value={status}
              onChange={(event) => change(setStatus)(event.target.value)}
              aria-label={t('rv.status')}
            >
              <option value="discrepancies">{t('rv.discrepanciesOnly')}</option>
              <option value="">{t('common.all')}</option>
              {ITEM_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {t(`rv.st.${value}` as StringKey)}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={provider}
              onChange={(event) => change(setProvider)(event.target.value)}
              aria-label={t('rv.courier')}
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
              value={caseStatus}
              onChange={(event) => change(setCaseStatus)(event.target.value)}
              aria-label={t('rv.case')}
            >
              <option value="">{t('rv.anyCase')}</option>
              {CASE_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {t(`rv.cs.${value}` as StringKey)}
                </option>
              ))}
            </select>
            <input
              className="input"
              type="date"
              value={dateFrom}
              onChange={(event) => change(setDateFrom)(event.target.value)}
              aria-label={t('rv.from')}
              title={t('rv.from')}
            />
            <input
              className="input"
              type="date"
              value={dateTo}
              onChange={(event) => change(setDateTo)(event.target.value)}
              aria-label={t('rv.to')}
              title={t('rv.to')}
            />
          </>
        }
        pagination={{
          canGoBack: paging.canGoBack,
          canGoForward: Boolean(data?.has_more && data?.next_cursor),
          onBack: paging.back,
          onForward: () => paging.forward(data?.next_cursor ?? null),
        }}
      />
    </Card>
  );
}

function CasesTable({ onOpen }: { onOpen: (id: string) => void }) {
  const { t, locale } = useSession();
  const paging = useCursorStack();
  const [status, setStatus] = useState<string>('OPEN');
  const [kind, setKind] = useState('');

  const query = useMemo(
    () => ({
      limit: 50,
      cursor: paging.cursor,
      status: status || undefined,
      kind: kind || undefined,
    }),
    [paging.cursor, status, kind],
  );
  const { data, loading, error, reload } = useApi<Page<Case>>('/reconciliation/cases', query);

  const columns: Column<Case>[] = [
    {
      key: 'kind',
      header: t('recon.kind'),
      render: (row) => (
        <>
          <div className="table__primary">{kindLabel(row.kind, t)}</div>
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
      render: (row) => (
        <Chip label={t(`rv.cs.${row.status}` as StringKey)} tone={caseTone(row.status)} />
      ),
    },
    {
      key: 'opened',
      header: t('recon.opened'),
      render: (row) => formatDate(row.opened_at, { locale }),
    },
  ];

  return (
    <Card padded={false}>
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        loading={loading}
        error={error}
        onRetry={reload}
        onRowClick={(row) => onOpen(row.id)}
        emptyTitle={t('recon.empty')}
        emptyHint={t('recon.emptyHint')}
        toolbar={
          <>
            <select
              className="select"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                paging.reset();
              }}
              aria-label={t('recon.state')}
            >
              <option value="">{t('common.all')}</option>
              {CASE_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {t(`rv.cs.${value}` as StringKey)}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                paging.reset();
              }}
              aria-label={t('recon.kind')}
            >
              <option value="">{t('rv.anyKind')}</option>
              {CASE_KINDS.map((value) => (
                <option key={value} value={value}>
                  {kindLabel(value, t)}
                </option>
              ))}
            </select>
          </>
        }
        pagination={{
          canGoBack: paging.canGoBack,
          canGoForward: Boolean(data?.has_more && data?.next_cursor),
          onBack: paging.back,
          onForward: () => paging.forward(data?.next_cursor ?? null),
        }}
      />
    </Card>
  );
}

function ItemDrawer({
  itemId,
  onClose,
  onOpenCase,
  onChanged,
}: {
  itemId: string;
  onClose: () => void;
  onOpenCase: (caseId: string) => void;
  onChanged: () => void;
}) {
  const { t, locale } = useSession();
  const { data, loading, error, reload } = useApi<ItemDetail>(`/reconciliation/items/${itemId}`);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function acceptCharges(item: ItemDetail) {
    const amount = formatPaisa(item.charges_pending_paisa, { locale });
    if (!window.confirm(t('rv.acceptConfirm', { amount }))) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/reconciliation/items/${item.id}/accept-charges`, {});
      setMessage(t('rv.acceptDone'));
      reload();
      onChanged();
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : t('common.couldNotLoad'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('rv.parcelDetail')} onClose={onClose}>
      {loading ? <p className="card__hint">{t('common.loading')}</p> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}
      {data ? (
        <>
          <Row
            label={t('rv.parcel')}
            value={data.merchant_reference ?? data.tracking_code ?? '—'}
          />
          <Row label={t('rv.courier')} value={courierName(data.provider, t)} />
          <Row
            label={t('rv.status')}
            value={<Chip label={t(`rv.st.${data.status}` as StringKey)} tone={itemTone(data.status)} />}
          />

          <div className="tablewrap" style={{ marginTop: 14 }}>
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th className="num">{t('rv.expected')}</th>
                  <th className="num">{t('rv.actual')}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{t('rv.cod')}</td>
                  <td className="num">{formatPaisa(data.expected_cod_paisa, { locale })}</td>
                  <td className="num">{formatPaisa(data.actual_cod_paisa, { locale })}</td>
                </tr>
                <tr>
                  <td>{t('rv.charge')}</td>
                  <td className="num">{formatPaisa(data.expected_charge_paisa, { locale })}</td>
                  <td className="num">{formatPaisa(data.actual_charge_paisa, { locale })}</td>
                </tr>
                <tr>
                  <td>
                    <strong>{t('rv.net')}</strong>
                  </td>
                  <td className="num">
                    <strong>{formatPaisa(data.expected_net_paisa, { locale })}</strong>
                  </td>
                  <td className="num">
                    <strong>{formatPaisa(data.actual_net_paisa, { locale })}</strong>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <Row
            label={t('rv.difference')}
            value={
              <span className={differenceClass(data.difference_paisa)}>
                {data.difference_paisa === null ? '—' : signedPaisa(data.difference_paisa, locale)}
              </span>
            }
          />
          {data.expected_cod_paisa !== null && data.expected_charge_paisa === null ? (
            <p className="card__hint">{t('rv.chargeUnverified')}</p>
          ) : null}
          {data.detail?.unknown_deduction ? (
            <p className="card__hint">{t('rv.unknownDeduction')}</p>
          ) : null}

          {data.lines.length > 0 ? (
            <>
              <h3 className="card__title" style={{ marginTop: 18 }}>
                {t('rv.lines')}
              </h3>
              {data.lines.map((line) => (
                <Row
                  key={line.id}
                  label={t('rv.row', { n: line.row_number })}
                  value={formatPaisa(line.amount_paisa, { locale })}
                />
              ))}
            </>
          ) : null}

          {data.adjustments.length > 0 ? (
            <>
              <h3 className="card__title" style={{ marginTop: 18 }}>
                {t('rv.charges')}
              </h3>
              {data.adjustments.map((adjustment) => (
                <Row
                  key={adjustment.id}
                  label={`${adjustment.provider_label ?? adjustment.type} · ${
                    adjustment.accepted_at ? t('rv.accepted') : t('rv.pending')
                  }`}
                  value={formatPaisa(adjustment.amount_paisa, { locale })}
                />
              ))}
            </>
          ) : null}

          <div className="toolbar" style={{ padding: 0, marginTop: 18 }}>
            {data.charges_pending_paisa > 0 ? (
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={busy}
                onClick={() => acceptCharges(data)}
              >
                {t('rv.acceptCharges')} ({formatPaisa(data.charges_pending_paisa, { locale })})
              </button>
            ) : null}
            {data.case_id ? (
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => onOpenCase(data.case_id as string)}
              >
                {t('rv.openCase')}
              </button>
            ) : null}
          </div>
          {message ? <p className="card__hint">{message}</p> : null}
        </>
      ) : null}
    </Drawer>
  );
}

function CaseDrawer({
  caseId,
  onClose,
  onChanged,
}: {
  caseId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t, locale } = useSession();
  const { data, loading, error, reload } = useApi<CaseDetail>(`/reconciliation/cases/${caseId}`);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setNote('');
      reload();
      onChanged();
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : t('common.couldNotLoad'));
    } finally {
      setBusy(false);
    }
  }

  const isOpen = data ? data.status === 'OPEN' || data.status === 'IN_PROGRESS' : false;
  const hasNote = note.trim().length > 0;

  return (
    <Drawer title={t('rv.caseDetail')} onClose={onClose}>
      {loading ? <p className="card__hint">{t('common.loading')}</p> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}
      {data ? (
        <>
          <Row label={t('recon.kind')} value={kindLabel(data.kind, t)} />
          <Row label={t('recon.amount')} value={formatPaisa(data.amount_paisa, { locale })} />
          <Row
            label={t('recon.state')}
            value={<Chip label={t(`rv.cs.${data.status}` as StringKey)} tone={caseTone(data.status)} />}
          />
          <p className="card__hint" style={{ marginTop: 10 }}>
            {data.summary}
          </p>

          {data.item ? (
            <>
              <Row label={t('rv.expected')} value={formatPaisa(data.item.expected_net_paisa, { locale })} />
              <Row label={t('rv.actual')} value={formatPaisa(data.item.actual_net_paisa, { locale })} />
              <Row
                label={t('rv.difference')}
                value={
                  <span className={differenceClass(data.item.difference_paisa)}>
                    {data.item.difference_paisa === null
                      ? '—'
                      : signedPaisa(data.item.difference_paisa, locale)}
                  </span>
                }
              />
            </>
          ) : null}

          <h3 className="card__title" style={{ marginTop: 18 }}>
            {t('rv.history')}
          </h3>
          {data.events.map((event) => (
            <Row
              key={event.id}
              label={`${t(`rv.ev.${event.action}` as StringKey)}${event.note ? ` — ${event.note}` : ''}`}
              value={
                event.actor_user_id
                  ? formatDate(event.created_at, { locale, withTime: true })
                  : t('rv.automatic')
              }
            />
          ))}

          <label className="card__hint" htmlFor="case-note" style={{ display: 'block', marginTop: 18 }}>
            {t('rv.note')}
          </label>
          <textarea
            id="case-note"
            className="input"
            rows={3}
            style={{ width: '100%', resize: 'vertical' }}
            placeholder={t('rv.notePlaceholder')}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={400}
          />
          {isOpen && !hasNote ? <p className="card__hint">{t('rv.noteRequired')}</p> : null}

          <div className="toolbar" style={{ padding: 0, marginTop: 12 }}>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy || !hasNote}
              onClick={() =>
                act(() => api.post(`/reconciliation/cases/${data.id}/notes`, { note: note.trim() }))
              }
            >
              {t('rv.addNote')}
            </button>
            {isOpen ? (
              <>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  disabled={busy || !hasNote}
                  onClick={() =>
                    act(() =>
                      api.patch(`/reconciliation/cases/${data.id}`, {
                        status: 'RESOLVED',
                        resolution: note.trim(),
                      }),
                    )
                  }
                >
                  {t('rv.resolve')}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy || !hasNote}
                  onClick={() =>
                    act(() =>
                      api.patch(`/reconciliation/cases/${data.id}`, {
                        status: 'DISMISSED',
                        resolution: note.trim(),
                      }),
                    )
                  }
                >
                  {t('rv.dismiss')}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn btn--sm"
                disabled={busy}
                onClick={() =>
                  act(() =>
                    api.patch(`/reconciliation/cases/${data.id}`, {
                      status: 'OPEN',
                      resolution: note.trim() || null,
                    }),
                  )
                }
              >
                {t('rv.reopen')}
              </button>
            )}
          </div>
          {message ? <p className="card__hint">{message}</p> : null}
        </>
      ) : null}
    </Drawer>
  );
}

type Translate = (key: StringKey, vars?: Record<string, string | number>) => string;

function kindLabel(kind: string, t: Translate): string {
  return (CASE_KINDS as readonly string[]).includes(kind) ? t(`rv.k.${kind}` as StringKey) : kind;
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

/** Formatting only: the sign is the server's, shown explicitly for gains. */
function signedPaisa(paisa: number, locale: string): string {
  const text = formatPaisa(paisa, { locale });
  return paisa > 0 ? `+${text}` : text;
}

function differenceClass(paisa: number | null): string | undefined {
  if (paisa === null || paisa === 0) {
    return undefined;
  }
  return paisa < 0 ? 'text--bad' : 'text--good';
}

function itemTone(status: ItemStatus): Tone {
  switch (status) {
    case 'MATCHED':
      return 'good';
    case 'UNMATCHED':
    case 'NEEDS_REVIEW':
      return 'warn';
    case 'DUPLICATE':
    case 'RETURN_ADJUSTMENT':
      return 'neutral';
    default:
      return 'bad';
  }
}

function caseTone(status: CaseStatus): Tone {
  switch (status) {
    case 'RESOLVED':
      return 'good';
    case 'OPEN':
      return 'warn';
    case 'IN_PROGRESS':
      return 'bad';
    default:
      return 'neutral';
  }
}
