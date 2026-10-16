'use client';

import Link from 'next/link';
import { useMemo, useState, type FormEvent } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { Chip, Drawer, ErrorState, Row, type Tone } from '@/components/ui';
import { api, upload, type Page } from '@/lib/api';
import { courierName } from '@/lib/couriers';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { errorMessage, takaToPaisa } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

type Translate = (key: StringKey, vars?: Record<string, string | number>) => string;

export interface AgingBand {
  label: string;
  min_days: number;
  max_days: number | null;
  parcel_count: number;
  outstanding_paisa: number;
}

/** A receivables filter, set by clicking a courier or an aging band. */
export interface ReceivableFilter {
  provider: string;
  band: string;
  unpaidOnly: boolean;
}

interface Receivable {
  id: string;
  order_id: string;
  order_number: string | null;
  consignment_id: string;
  provider: string;
  status: string;
  collectible_paisa: number;
  settled_paisa: number;
  deduction_paisa: number;
  adjustment_paisa: number;
  outstanding_paisa: number;
  age_days: number | null;
  eligible_at: string | null;
  settled_at: string | null;
  status_reason: string | null;
}

interface LedgerEntry {
  id: string;
  occurred_at: string;
  event_type: string;
  amount_paisa: number;
  direction: string;
  bucket: string;
  source: string;
  source_ref: string | null;
  reversal_of: string | null;
  reason: string | null;
}

interface Payout {
  id: string;
  provider: string;
  provider_reference: string | null;
  source: string;
  status: string;
  total_paisa: number;
  applied_paisa: number;
  unexplained_paisa: number;
  paid_on: string | null;
  received_at: string;
  note: string | null;
  source_file_id: string | null;
}

interface Candidate {
  receivable_id: string;
  consignment_id: string;
  merchant_reference: string | null;
  outstanding_paisa: number;
  score: number;
  signals: string[];
  rejected_because: string | null;
}

interface PayoutLine {
  id: string;
  row_number: number;
  amount_paisa: number;
  applied_paisa: number;
  status: string;
  confidence: string | null;
  provider_consignment_id: string | null;
  tracking_code: string | null;
  merchant_reference: string | null;
  delivered_on: string | null;
  receivable_id: string | null;
  match_reason: string | null;
  candidates: Candidate[];
}

interface PayoutDetail extends Payout {
  lines: PayoutLine[];
  adjustments: {
    id: string;
    payout_line_id: string | null;
    type: string;
    amount_paisa: number;
    provider_label: string | null;
    recognized_rule: string | null;
    accepted_at?: string | null;
  }[];
}

interface StatementPreview {
  detected_headers: string[];
  column_mapping: Record<string, string>;
  fields: string[];
  row_count: number;
  invalid_row_count: number;
  total_paisa: number;
  rows: {
    row_number: number;
    amount_paisa: number | null;
    consignment_id: string | null;
    tracking_code: string | null;
    merchant_reference: string | null;
    delivered_on: string | null;
    fee_paisa: number | null;
    errors: string[];
  }[];
}

const COURIERS = ['steadfast', 'pathao', 'redx', 'manual'] as const;

export function bandKey(band: AgingBand): string {
  return `${band.min_days}-${band.max_days ?? ''}`;
}

/** The band in the reader's language, from its bounds rather than the API's English label. */
export function bandLabel(band: AgingBand, t: Translate): string {
  return band.max_days === null
    ? t('money.bandOver', { days: band.min_days - 1 })
    : t('money.bandRange', { min: band.min_days, max: band.max_days });
}

function receivableTone(status: string): Tone {
  if (status === 'SETTLED') return 'good';
  if (status === 'MISMATCHED' || status === 'DISPUTED') return 'bad';
  if (status === 'PARTIALLY_SETTLED' || status === 'ELIGIBLE' || status === 'PAYOUT_IDENTIFIED') return 'warn';
  return 'neutral';
}

function lineTone(status: string): Tone {
  if (status === 'MATCHED' || status === 'MANUAL_MATCHED') return 'good';
  if (status === 'SUGGESTED') return 'warn';
  if (status === 'UNMATCHED' || status === 'DUPLICATE') return 'bad';
  return 'neutral';
}

function statusLabel(t: Translate, prefix: string, status: string): string {
  const key = `${prefix}.${status}` as StringKey;
  const label = t(key);
  return label === key ? status.replaceAll('_', ' ').toLowerCase() : label;
}

// --------------------------------------------------------------------------- //
// Receivables
// --------------------------------------------------------------------------- //

/**
 * COD receivables, one parcel per row, filtered on the server.
 *
 * Every balance is the API's. A row opens the parcel's append-only ledger, and
 * corrections, write-offs and disputes are new ledger events through their own
 * endpoints — nothing is edited or deleted.
 */
export function ReceivablesTable({
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
  const [openId, setOpenId] = useState<string | null>(null);
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
  const rows = data?.items ?? [];

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
          <div className="table__sub">{courierName(row.provider, t('rv.manualCourier'))}</div>
        </>
      ),
    },
    {
      key: 'status',
      header: t('recon.state'),
      render: (row) => <Chip label={statusLabel(t, 'ml.rs', row.status)} tone={receivableTone(row.status)} />,
    },
    { key: 'collectible', header: t('money.collectible'), numeric: true, render: (row) => formatPaisa(row.collectible_paisa, { locale }) },
    { key: 'settled', header: t('money.received'), numeric: true, render: (row) => formatPaisa(row.settled_paisa, { locale }) },
    { key: 'deductions', header: t('money.deductions'), numeric: true, render: (row) => formatPaisa(row.deduction_paisa, { locale }) },
    {
      key: 'outstanding',
      header: t('money.balance'),
      numeric: true,
      // The API's own balance. Not collectible minus settled: deductions,
      // adjustments and write-offs all move it, and re-deriving it here would
      // disagree with the ledger for exactly the parcels that matter.
      render: (row) => formatPaisa(row.outstanding_paisa, { locale }),
    },
    { key: 'age', header: t('money.age'), numeric: true, render: (row) => (row.age_days === null ? '—' : t('cf.days', { days: row.age_days })) },
  ];

  const open = rows.find((row) => row.id === openId) ?? null;

  return (
    <>
      <DataTable
        columns={columns}
        rows={rows}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyTitle={t('money.emptyReceivables')}
        onRowClick={(row) => setOpenId(row.id)}
        toolbar={
          <>
            <select className="select" value={filter.provider} onChange={(event) => change({ provider: event.target.value })} aria-label={t('cf.courier')}>
              <option value="">{t('rv.anyCourier')}</option>
              {COURIERS.map((value) => (
                <option key={value} value={value}>
                  {courierName(value, t('rv.manualCourier'))}
                </option>
              ))}
            </select>
            <select className="select" value={filter.band} onChange={(event) => change({ band: event.target.value })} aria-label={t('money.age')}>
              <option value="">{t('money.anyAge')}</option>
              {bands.map((value) => (
                <option key={value.label} value={bandKey(value)}>
                  {bandLabel(value, t)}
                </option>
              ))}
            </select>
            <label className="card__hint" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={filter.unpaidOnly} onChange={(event) => change({ unpaidOnly: event.target.checked })} />
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
      {open ? <ReceivableDrawer receivable={open} onClose={() => setOpenId(null)} onChanged={reload} /> : null}
    </>
  );
}

function ReceivableDrawer({ receivable, onClose, onChanged }: { receivable: Receivable; onClose: () => void; onChanged: () => void }) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const ledger = useApi<LedgerEntry[]>(`/money/receivables/${receivable.id}/ledger`);
  const [action, setAction] = useState<'' | 'correction' | 'write-off' | 'dispute'>('');
  const [amount, setAmount] = useState('');
  const [increases, setIncreases] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    let body: Record<string, unknown> = { reason: reason.trim() };
    if (action === 'correction') {
      const paisa = takaToPaisa(amount);
      if (!paisa) {
        setError(t('oc.badAmount'));
        return;
      }
      body = { ...body, amount_paisa: paisa, increases_balance: increases };
    }
    setBusy(true);
    try {
      await api.post(`/money/receivables/${receivable.id}/${action}`, body);
      setNotice(t('ml.recorded'));
      setAction('');
      setReason('');
      setAmount('');
      ledger.reload();
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={`${t('money.parcel')} ${receivable.order_number ?? ''}`} onClose={onClose}>
      <Row label={t('recon.state')} value={<Chip label={statusLabel(t, 'ml.rs', receivable.status)} tone={receivableTone(receivable.status)} />} />
      <Row label={t('orders.courier')} value={courierName(receivable.provider, t('rv.manualCourier'))} />
      <Row label={t('money.collectible')} value={formatPaisa(receivable.collectible_paisa, { locale })} />
      <Row label={t('money.received')} value={formatPaisa(receivable.settled_paisa, { locale })} />
      <Row label={t('money.deductions')} value={formatPaisa(receivable.deduction_paisa, { locale })} />
      <Row label={t('ml.adjustments')} value={formatPaisa(receivable.adjustment_paisa, { locale })} />
      <Row label={<strong>{t('money.balance')}</strong>} value={<strong>{formatPaisa(receivable.outstanding_paisa, { locale })}</strong>} />
      {receivable.status_reason ? <p className="card__hint">{receivable.status_reason}</p> : null}
      <p>
        <Link href={`/orders?order=${receivable.order_id}`}>{t('ml.openOrder')}</Link>
      </p>

      <h3 className="card__title" style={{ marginTop: 14 }}>{t('ml.ledger')}</h3>
      <p className="card__hint">{t('ml.ledgerHint')}</p>
      {ledger.error ? <ErrorState error={ledger.error} onRetry={ledger.reload} /> : null}
      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('ins.date')}</th>
              <th>{t('ml.event')}</th>
              <th className="num">{t('cf.amount')}</th>
            </tr>
          </thead>
          <tbody>
            {(ledger.data ?? []).map((entry) => (
              <tr key={entry.id}>
                <td>{formatDate(entry.occurred_at, { locale, withTime: true })}</td>
                <td>
                  {entry.event_type.replaceAll('_', ' ').toLowerCase()}
                  <div className="table__sub">
                    {[entry.bucket, entry.source, entry.reversal_of ? t('ml.reversal') : null, entry.reason].filter(Boolean).join(' · ')}
                  </div>
                </td>
                <td className="num">
                  {formatPaisa(entry.amount_paisa, { locale })}
                  <div className="table__sub">{entry.direction.toLowerCase()}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {notice ? <div className="notice notice--good" style={{ marginTop: 10 }}>{notice}</div> : null}

      {can('money.correct') || can('money.reconcile') ? (
        <div className="toolbar" style={{ padding: '12px 0 0', flexWrap: 'wrap' }}>
          {can('money.correct') ? (
            <>
              <button type="button" className="btn btn--sm" onClick={() => setAction('correction')}>{t('ml.correct')}</button>
              <button type="button" className="btn btn--sm" onClick={() => setAction('write-off')}>{t('ml.writeOff')}</button>
            </>
          ) : null}
          {can('money.reconcile') ? (
            <button type="button" className="btn btn--sm" onClick={() => setAction('dispute')}>{t('ml.dispute')}</button>
          ) : null}
        </div>
      ) : null}

      {action ? (
        <form className="card" style={{ padding: 10, marginTop: 10 }} onSubmit={submit}>
          <strong>{t(`ml.action.${action}` as StringKey)}</strong>
          <p className="card__hint">{t(`ml.actionHint.${action}` as StringKey)}</p>
          {action === 'correction' ? (
            <>
              <label className="field">
                <span className="field__label">{t('cf.amount')}</span>
                <input className="input" inputMode="decimal" required value={amount} onChange={(event) => setAmount(event.target.value)} />
              </label>
              <label className="card__hint" style={{ display: 'flex', gap: 6 }}>
                <input type="checkbox" checked={increases} onChange={(event) => setIncreases(event.target.checked)} />
                {t('ml.increases')}
              </label>
            </>
          ) : null}
          <label className="field">
            <span className="field__label">{t('ml.reason')} *</span>
            <input className="input" required minLength={5} maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          {error ? <p className="formerror">{error}</p> : null}
          <div className="toolbar" style={{ padding: 0 }}>
            <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>{t('ml.record')}</button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAction('')}>{t('common.cancel')}</button>
          </div>
        </form>
      ) : null}
    </Drawer>
  );
}

// --------------------------------------------------------------------------- //
// Payouts
// --------------------------------------------------------------------------- //

/**
 * Courier payouts, with statement import and manual entry.
 *
 * Importing is two steps on purpose: the API parses the file and shows what it
 * found — the detected columns, the mapping it chose, every row it could not
 * read — before a single paisa moves. Matching then runs in the backend's
 * reconciliation engine, never here.
 */
export function PayoutsTable() {
  const { t, locale } = useSession();
  const { can } = useShop();
  const paging = useCursorStack();
  const [openId, setOpenId] = useState<string | null>(null);
  const [mode, setMode] = useState<'' | 'upload' | 'manual'>('');
  const query = useMemo(() => ({ limit: 50, cursor: paging.cursor }), [paging.cursor]);
  const { data, loading, error, reload } = useApi<Page<Payout>>('/payouts', query);

  const columns: Column<Payout>[] = [
    {
      key: 'provider',
      header: t('cour.provider'),
      render: (row) => (
        <>
          <div className="table__primary">{courierName(row.provider, t('rv.manualCourier'))}</div>
          {row.provider_reference ? <div className="table__sub">{row.provider_reference}</div> : null}
        </>
      ),
    },
    { key: 'paid_on', header: t('money.paidOn'), render: (row) => formatDate(row.paid_on ?? row.received_at, { locale }) },
    { key: 'status', header: t('recon.state'), render: (row) => <Chip label={statusLabel(t, 'ml.ps', row.status)} tone={row.status === 'RECONCILED' ? 'good' : row.status === 'PARTIALLY_RECONCILED' ? 'warn' : 'neutral'} /> },
    { key: 'source', header: t('ml.source'), render: (row) => <span className="table__sub">{row.source.replaceAll('_', ' ').toLowerCase()}</span> },
    { key: 'total', header: t('money.total'), numeric: true, render: (row) => formatPaisa(row.total_paisa, { locale }) },
    { key: 'applied', header: t('money.applied'), numeric: true, render: (row) => formatPaisa(row.applied_paisa, { locale }) },
    {
      key: 'unexplained',
      header: t('money.unexplained'),
      numeric: true,
      render: (row) =>
        row.unexplained_paisa > 0 ? (
          // Money that arrived but could not be tied to a parcel. Kept visible
          // rather than folded into the total, because a seller has to act on it.
          <Chip label={formatPaisa(row.unexplained_paisa, { locale })} tone="warn" />
        ) : (
          formatPaisa(0, { locale })
        ),
    },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyTitle={t('money.emptyPayouts')}
        onRowClick={(row) => setOpenId(row.id)}
        toolbar={
          can('money.reconcile') ? (
            <>
              <button type="button" className="btn btn--primary btn--sm" onClick={() => setMode('upload')}>{t('ml.upload')}</button>
              <button type="button" className="btn btn--sm" onClick={() => setMode('manual')}>{t('ml.manual')}</button>
            </>
          ) : undefined
        }
        pagination={{
          canGoBack: paging.canGoBack,
          canGoForward: Boolean(data?.has_more && data?.next_cursor),
          onBack: paging.back,
          onForward: () => paging.forward(data?.next_cursor ?? null),
        }}
      />
      {mode === 'upload' ? (
        <StatementUpload
          onClose={() => setMode('')}
          onImported={(id) => {
            setMode('');
            reload();
            setOpenId(id);
          }}
        />
      ) : null}
      {mode === 'manual' ? (
        <ManualPayout
          onClose={() => setMode('')}
          onSaved={(id) => {
            setMode('');
            reload();
            setOpenId(id);
          }}
        />
      ) : null}
      {openId ? <PayoutDrawer payoutId={openId} onClose={() => setOpenId(null)} onChanged={reload} /> : null}
    </>
  );
}

function StatementUpload({ onClose, onImported }: { onClose: () => void; onImported: (payoutId: string) => void }) {
  const { t, locale } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [provider, setProvider] = useState('steadfast');
  const [reference, setReference] = useState('');
  const [paidOn, setPaidOn] = useState('');
  const [note, setNote] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function form(extra: Record<string, string>) {
    const body = new FormData();
    if (file) body.append('file', file);
    if (Object.keys(mapping).length > 0) body.append('mapping', JSON.stringify(mapping));
    for (const [key, value] of Object.entries(extra)) {
      if (value) body.append(key, value);
    }
    return body;
  }

  async function runPreview() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await upload<StatementPreview>('/payouts/preview', form({}));
      setPreview(result);
      setMapping(result.column_mapping);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function runImport() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const payout = await upload<{ id: string }>('/payouts/import', form({ provider, reference: reference.trim(), paid_on: paidOn, note: note.trim() }));
      onImported(payout.id);
    } catch (caught) {
      // A statement already imported is refused by the API, and that refusal is shown as it is.
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('ml.upload')} wide onClose={onClose}>
      <p className="card__hint">{t('ml.uploadHint')}</p>
      <div className="formgrid">
        <label className="field formgrid__full">
          <span className="field__label">{t('ml.file')}</span>
          <input
            className="input"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setPreview(null);
              setMapping({});
            }}
          />
        </label>
        <label className="field">
          <span className="field__label">{t('cour.provider')}</span>
          <select className="select" value={provider} onChange={(event) => setProvider(event.target.value)}>
            {COURIERS.map((value) => (
              <option key={value} value={value}>{courierName(value, t('rv.manualCourier'))}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">{t('money.paidOn')}</span>
          <input className="input" type="date" value={paidOn} onChange={(event) => setPaidOn(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('ml.reference')}</span>
          <input className="input" maxLength={160} value={reference} onChange={(event) => setReference(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.note')}</span>
          <input className="input" maxLength={400} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
      </div>
      <button type="button" className="btn btn--sm" disabled={!file || busy} onClick={() => void runPreview()}>
        {busy && !preview ? t('common.loading') : t('ml.preview')}
      </button>

      {preview ? (
        <>
          <h3 className="card__title" style={{ marginTop: 14 }}>{t('ml.mapping')}</h3>
          <p className="card__hint">{t('ml.mappingHint')}</p>
          <div className="formgrid">
            {preview.fields.map((field) => (
              <label key={field} className="field">
                <span className="field__label">{field.replaceAll('_', ' ')}</span>
                <select className="select" value={mapping[field] ?? ''} onChange={(event) => setMapping((current) => ({ ...current, [field]: event.target.value }))}>
                  <option value="">—</option>
                  {preview.detected_headers.map((header) => (
                    <option key={header} value={header}>{header}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void runPreview()}>{t('ml.repreview')}</button>
          <Row label={t('ml.rows')} value={preview.row_count} />
          <Row label={t('ml.invalidRows')} value={preview.invalid_row_count ? <Chip label={String(preview.invalid_row_count)} tone="warn" /> : 0} />
          <Row label={t('money.total')} value={formatPaisa(preview.total_paisa, { locale })} />
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t('ml.reference')}</th>
                  <th className="num">{t('cf.amount')}</th>
                  <th>{t('ml.problems')}</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 50).map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>{row.tracking_code ?? row.consignment_id ?? row.merchant_reference ?? '—'}</td>
                    <td className="num">{formatPaisa(row.amount_paisa, { locale })}</td>
                    <td className={row.errors.length ? 'text--bad' : undefined}>{row.errors.join('; ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="formactions">
            <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void runImport()}>{t('ml.import')}</button>
          </div>
        </>
      ) : null}
      {error ? <p className="formerror" role="alert">{error}</p> : null}
    </Drawer>
  );
}

function ManualPayout({ onClose, onSaved }: { onClose: () => void; onSaved: (payoutId: string) => void }) {
  const { t } = useSession();
  const [provider, setProvider] = useState('manual');
  const [amount, setAmount] = useState('');
  const [paidOn, setPaidOn] = useState('');
  const [reference, setReference] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    const paisa = takaToPaisa(amount);
    if (!paisa) {
      setError(t('oc.badAmount'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payout = await api.post<{ id: string }>('/payouts/manual', {
        provider,
        total_paisa: paisa,
        paid_on: paidOn || undefined,
        reference: reference.trim() || undefined,
        note: note.trim() || undefined,
      });
      onSaved(payout.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('ml.manual')} onClose={onClose}>
      <form onSubmit={save}>
        <p className="card__hint">{t('ml.manualHint')}</p>
        <label className="field">
          <span className="field__label">{t('cour.provider')}</span>
          <select className="select" value={provider} onChange={(event) => setProvider(event.target.value)}>
            {COURIERS.map((value) => (
              <option key={value} value={value}>{courierName(value, t('rv.manualCourier'))}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">{t('cf.amount')} *</span>
          <input className="input" inputMode="decimal" required value={amount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('money.paidOn')}</span>
          <input className="input" type="date" value={paidOn} onChange={(event) => setPaidOn(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('ml.reference')}</span>
          <input className="input" maxLength={160} value={reference} onChange={(event) => setReference(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.note')}</span>
          <input className="input" maxLength={400} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        {error ? <p className="formerror">{error}</p> : null}
        <div className="formactions">
          <button type="submit" className="btn btn--primary" disabled={busy}>{t('ml.record')}</button>
        </div>
      </form>
    </Drawer>
  );
}

/**
 * One payout, line by line: what each line matched, how sure the engine was,
 * the candidates it considered (rejected ones included, with why), and the
 * manual match or unmatch a person can record — both audited and reversible,
 * never an edit.
 */
function PayoutDrawer({ payoutId, onClose, onChanged }: { payoutId: string; onClose: () => void; onChanged: () => void }) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const payout = useApi<PayoutDetail>(`/payouts/${payoutId}`);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lineId, setLineId] = useState<string | null>(null);
  const data = payout.data;
  const canReconcile = can('money.reconcile');

  async function run(work: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await work());
      payout.reload();
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const line = data?.lines.find((row) => row.id === lineId) ?? null;

  return (
    <Drawer title={t('ml.payout')} wide onClose={onClose}>
      {payout.error ? <ErrorState error={payout.error} onRetry={payout.reload} /> : null}
      {!data ? <p className="card__hint">{t('common.loading')}</p> : null}
      {data ? (
        <>
          <Row label={t('cour.provider')} value={courierName(data.provider, t('rv.manualCourier'))} />
          <Row label={t('money.paidOn')} value={formatDate(data.paid_on ?? data.received_at, { locale })} />
          <Row label={t('ml.reference')} value={data.provider_reference ?? '—'} />
          <Row label={t('recon.state')} value={<Chip label={statusLabel(t, 'ml.ps', data.status)} />} />
          <Row label={t('money.total')} value={formatPaisa(data.total_paisa, { locale })} />
          <Row label={t('money.applied')} value={formatPaisa(data.applied_paisa, { locale })} />
          <Row label={t('money.unexplained')} value={data.unexplained_paisa > 0 ? <Chip label={formatPaisa(data.unexplained_paisa, { locale })} tone="warn" /> : formatPaisa(0, { locale })} />
          {data.note ? <p className="card__hint">{data.note}</p> : null}

          {notice ? <div className="notice notice--good">{notice}</div> : null}
          {error ? <p className="formerror" role="alert">{error}</p> : null}

          {canReconcile ? (
            <div className="toolbar" style={{ padding: '8px 0', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const result = await api.post<{ exact_matches: number; suggested: number; unresolved: number; cases_opened: number; shadow: boolean }>(`/reconciliation/payouts/${data.id}/reconcile`);
                    return t('ml.reconciled', { exact: result.exact_matches, suggested: result.suggested, unresolved: result.unresolved, cases: result.cases_opened }) + (result.shadow ? ` · ${t('ml.shadow')}` : '');
                  })
                }
              >
                {t('ml.reconcile')}
              </button>
              {data.adjustments.some((row) => !row.accepted_at) ? (
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await api.post<{ accepted_paisa: number }>(`/reconciliation/payouts/${data.id}/accept-charges`, {});
                      return t('ml.chargesAccepted', { amount: formatPaisa(result.accepted_paisa, { locale }) });
                    })
                  }
                >
                  {t('ml.acceptCharges')}
                </button>
              ) : null}
            </div>
          ) : null}

          <h3 className="card__title" style={{ marginTop: 10 }}>{t('ml.lines')}</h3>
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t('ml.reference')}</th>
                  <th className="num">{t('cf.amount')}</th>
                  <th>{t('recon.state')}</th>
                </tr>
              </thead>
              <tbody>
                {data.lines.map((row) => (
                  <tr key={row.id} className="table__row--clickable" data-selected={row.id === lineId} onClick={() => setLineId(row.id === lineId ? null : row.id)}>
                    <td>{row.row_number}</td>
                    <td>
                      {row.tracking_code ?? row.provider_consignment_id ?? row.merchant_reference ?? '—'}
                      {row.match_reason ? <div className="table__sub">{row.match_reason}</div> : null}
                    </td>
                    <td className="num">{formatPaisa(row.amount_paisa, { locale })}</td>
                    <td>
                      <Chip label={statusLabel(t, 'ml.ls', row.status)} tone={lineTone(row.status)} />
                      {row.confidence ? <div className="table__sub">{row.confidence.toLowerCase()}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {line ? <LineActions line={line} busy={busy} canReconcile={canReconcile} run={run} /> : null}

          {data.adjustments.length > 0 ? (
            <>
              <h3 className="card__title" style={{ marginTop: 14 }}>{t('ml.adjustments')}</h3>
              {data.adjustments.map((row) => (
                <Row
                  key={row.id}
                  label={`${row.type.replaceAll('_', ' ').toLowerCase()}${row.provider_label ? ` · ${row.provider_label}` : ''}`}
                  value={
                    <>
                      {formatPaisa(row.amount_paisa, { locale })} {row.accepted_at ? <Chip label={t('ml.accepted')} tone="good" /> : row.type === 'UNKNOWN_DEDUCTION' ? <Chip label={t('ml.unknownDeduction')} tone="warn" /> : null}
                    </>
                  }
                />
              ))}
            </>
          ) : null}
        </>
      ) : null}
    </Drawer>
  );
}

function LineActions({
  line,
  busy,
  canReconcile,
  run,
}: {
  line: PayoutLine;
  busy: boolean;
  canReconcile: boolean;
  run: (work: () => Promise<string>) => Promise<void>;
}) {
  const { t, locale } = useSession();
  const [receivableId, setReceivableId] = useState('');
  const [reason, setReason] = useState('');
  const matched = line.status === 'MATCHED' || line.status === 'MANUAL_MATCHED';

  return (
    <div className="card" style={{ padding: 10, marginTop: 10 }}>
      <strong>{t('ml.lineTitle', { n: line.row_number })}</strong>
      {line.candidates.length > 0 ? (
        <>
          <p className="card__hint">{t('ml.candidatesHint')}</p>
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>{t('ml.reference')}</th>
                  <th className="num">{t('money.balance')}</th>
                  <th className="num">{t('ml.score')}</th>
                  <th>{t('ml.evidence')}</th>
                </tr>
              </thead>
              <tbody>
                {line.candidates.map((candidate) => (
                  <tr key={candidate.receivable_id}>
                    <td>
                      {!matched && !candidate.rejected_because ? (
                        <input type="radio" name={`cand-${line.id}`} checked={receivableId === candidate.receivable_id} onChange={() => setReceivableId(candidate.receivable_id)} aria-label={candidate.merchant_reference ?? candidate.receivable_id} />
                      ) : null}
                    </td>
                    <td>{candidate.merchant_reference ?? candidate.consignment_id.slice(0, 8)}</td>
                    <td className="num">{formatPaisa(candidate.outstanding_paisa, { locale })}</td>
                    <td className="num">{candidate.score}</td>
                    <td className="table__sub">
                      {candidate.signals.join(', ') || '—'}
                      {candidate.rejected_because ? <div className="text--bad">{candidate.rejected_because}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="card__hint">{t('ml.noCandidates')}</p>
      )}

      {canReconcile ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (matched) {
              void run(async () => {
                await api.post(`/reconciliation/lines/${line.id}/unmatch`, { reason: reason.trim() });
                return t('ml.unmatched');
              });
            } else if (receivableId) {
              void run(async () => {
                await api.post(`/reconciliation/lines/${line.id}/match`, { receivable_id: receivableId, reason: reason.trim() });
                return t('ml.matched');
              });
            }
          }}
        >
          <label className="field">
            <span className="field__label">{t('ml.reason')} *</span>
            <input className="input" required minLength={3} maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <button type="submit" className="btn btn--sm" disabled={busy || (!matched && !receivableId)}>
            {matched ? t('ml.unmatch') : t('ml.match')}
          </button>
          <p className="card__hint">{matched ? t('ml.unmatchHint') : t('ml.matchHint')}</p>
        </form>
      ) : null}
    </div>
  );
}
