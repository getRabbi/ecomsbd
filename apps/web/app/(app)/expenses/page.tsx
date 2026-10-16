'use client';

import { useMemo, useState, type FormEvent } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, Row, Tile } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { errorMessage, takaToPaisa } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useSearchParamState } from '@/lib/url-state';
import { useApi } from '@/lib/useApi';

interface Expense {
  id: string;
  kind: string;
  amount_paisa: number;
  allocated_paisa: number;
  unallocated_paisa: number;
  period_start: string;
  period_end: string;
  description: string;
  product_id: string | null;
  preferred_method: string;
  allocated_at: string | null;
  created_at: string;
}

interface Profit {
  since: string;
  until: string;
  contribution_profit_paisa: number;
  ad_cost_paisa: number;
  unallocated_ad_spend_paisa: number;
  fixed_cost_paisa: number;
  operating_profit_paisa: number;
  quality: Record<string, number>;
}

const KINDS = ['AD_SPEND', 'FIXED', 'OTHER'] as const;
const METHODS = ['EQUAL_PER_DELIVERED_ORDER', 'PROPORTIONAL_TO_REVENUE'] as const;

function today(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Dhaka' }).format(new Date());
}

/**
 * Expenses — what the shop spent, and how much of it reached profit.
 *
 * Ad spend is the one kind the backend allocates onto delivered parcels; fixed
 * costs stay out of contribution profit and appear only in operating profit.
 * Allocation runs in the backend's profit engine and reports how much it could
 * place — this page never divides anything itself, and an allocation that
 * reached no parcel says so rather than pretending to be accounting-grade.
 */
export default function ExpensesPage() {
  const { t, locale } = useSession();
  const { can } = useShop();
  const paging = useCursorStack();
  const [kind, setKind] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [creating, setCreating] = useSearchParamState('new');
  const [openId, setOpenId] = useState<string | null>(null);

  const query = useMemo(
    () => ({ limit: 50, cursor: paging.cursor, kind: kind || undefined, since: since || undefined, until: until || undefined }),
    [paging.cursor, kind, since, until],
  );
  const { data, loading, error, reload } = useApi<Page<Expense>>('/expenses', query);
  const profit = useApi<Profit>('/analytics/profit', { since: since || undefined, until: until || undefined });
  const rows = data?.items ?? [];
  const open = rows.find((row) => row.id === openId) ?? null;

  const columns: Column<Expense>[] = [
    {
      key: 'description',
      header: t('ex.description'),
      render: (row) => (
        <>
          <div className="table__primary">{row.description}</div>
          <div className="table__sub">{formatDate(row.created_at, { locale })}</div>
        </>
      ),
    },
    { key: 'kind', header: t('ex.kind'), render: (row) => <Chip label={t(`ex.kind.${row.kind}` as StringKey)} /> },
    { key: 'period', header: t('ex.period'), render: (row) => `${formatDate(row.period_start, { locale })} – ${formatDate(row.period_end, { locale })}` },
    { key: 'amount', header: t('cf.amount'), numeric: true, render: (row) => formatPaisa(row.amount_paisa, { locale }) },
    {
      key: 'allocation',
      header: t('ex.allocation'),
      render: (row) =>
        row.kind !== 'AD_SPEND' ? (
          <span className="table__sub">{t('ex.notAllocated')}</span>
        ) : row.allocated_at ? (
          <Chip label={t('ex.allocated', { amount: formatPaisa(row.allocated_paisa, { locale }) })} tone={row.unallocated_paisa > 0 ? 'warn' : 'good'} />
        ) : (
          <Chip label={t('ex.pending')} tone="warn" />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title={t('nav.expenses')}
        subtitle={t('ex.subtitle')}
        actions={
          can('money.reconcile') ? (
            <button type="button" className="btn btn--primary" onClick={() => setCreating('1')}>
              {t('act.addExpense')}
            </button>
          ) : null
        }
      />
      <div className="content">
        {profit.data ? (
          <div className="tiles">
            <Tile label={t('ex.contribution')} value={formatPaisa(profit.data.contribution_profit_paisa, { locale })} hint={t('ex.contributionHint')} />
            <Tile label={t('ex.adCost')} value={formatPaisa(profit.data.ad_cost_paisa, { locale })} hint={profit.data.unallocated_ad_spend_paisa > 0 ? t('ex.unallocatedHint', { amount: formatPaisa(profit.data.unallocated_ad_spend_paisa, { locale }) }) : undefined} />
            <Tile label={t('ex.fixed')} value={formatPaisa(profit.data.fixed_cost_paisa, { locale })} hint={t('ex.fixedHint')} />
            <Tile label={t('ex.operating')} value={formatPaisa(profit.data.operating_profit_paisa, { locale })} hint={t('ex.operatingHint', { since: formatDate(profit.data.since, { locale }), until: formatDate(profit.data.until, { locale }) })} />
          </div>
        ) : null}

        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={rows}
            loading={loading}
            error={error}
            onRetry={reload}
            emptyTitle={t('ex.empty')}
            emptyHint={t('ex.emptyHint')}
            onRowClick={(row) => setOpenId(row.id)}
            toolbar={
              <>
                <select className="select" value={kind} aria-label={t('ex.kind')} onChange={(event) => { setKind(event.target.value); paging.reset(); }}>
                  <option value="">{t('common.all')}</option>
                  {KINDS.map((value) => (
                    <option key={value} value={value}>{t(`ex.kind.${value}` as StringKey)}</option>
                  ))}
                </select>
                <label className="card__hint">
                  {t('ins.from')}{' '}
                  <input className="input" type="date" value={since} onChange={(event) => { setSince(event.target.value); paging.reset(); }} />
                </label>
                <label className="card__hint">
                  {t('ins.to')}{' '}
                  <input className="input" type="date" value={until} onChange={(event) => { setUntil(event.target.value); paging.reset(); }} />
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
        </Card>
        <p className="card__hint" style={{ marginTop: 10 }}>{t('ex.explain')}</p>
      </div>

      {creating !== null ? (
        <ExpenseForm
          onClose={() => setCreating(null)}
          onSaved={() => {
            setCreating(null);
            reload();
            profit.reload();
          }}
        />
      ) : null}
      {open ? (
        <ExpenseDrawer
          expense={open}
          onClose={() => setOpenId(null)}
          onChanged={() => {
            reload();
            profit.reload();
          }}
        />
      ) : null}
    </>
  );
}

function ExpenseForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { t } = useSession();
  const [kind, setKind] = useState<string>('AD_SPEND');
  const [amount, setAmount] = useState('');
  const [start, setStart] = useState(today);
  const [end, setEnd] = useState(today);
  const [description, setDescription] = useState('');
  const [method, setMethod] = useState<string>('EQUAL_PER_DELIVERED_ORDER');
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
      await api.post('/expenses', {
        kind,
        amount_paisa: paisa,
        period_start: start,
        period_end: end,
        description: description.trim(),
        preferred_method: method,
      });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('act.addExpense')} onClose={onClose}>
      <form onSubmit={save}>
        <label className="field">
          <span className="field__label">{t('ex.kind')}</span>
          <select className="select" value={kind} onChange={(event) => setKind(event.target.value)}>
            {KINDS.map((value) => (
              <option key={value} value={value}>{t(`ex.kind.${value}` as StringKey)}</option>
            ))}
          </select>
          <span className="field__hint">{t(`ex.kindHint.${kind}` as StringKey)}</span>
        </label>
        <label className="field">
          <span className="field__label">{t('cf.amount')} *</span>
          <input className="input" inputMode="decimal" required autoFocus value={amount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('ex.description')} *</span>
          <input className="input" required maxLength={200} value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <div className="formgrid">
          <label className="field">
            <span className="field__label">{t('ins.from')}</span>
            <input className="input" type="date" required value={start} onChange={(event) => setStart(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('ins.to')}</span>
            <input className="input" type="date" required value={end} onChange={(event) => setEnd(event.target.value)} />
          </label>
        </div>
        {kind === 'AD_SPEND' ? (
          <label className="field">
            <span className="field__label">{t('ex.method')}</span>
            <select className="select" value={method} onChange={(event) => setMethod(event.target.value)}>
              {METHODS.map((value) => (
                <option key={value} value={value}>{t(`ex.method.${value}` as StringKey)}</option>
              ))}
            </select>
          </label>
        ) : null}
        {error ? <p className="formerror">{error}</p> : null}
        <div className="formactions">
          <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" className="btn btn--primary" disabled={busy}>{busy ? t('set.saving') : t('ml.record')}</button>
        </div>
      </form>
    </Drawer>
  );
}

function ExpenseDrawer({ expense, onClose, onChanged }: { expense: Expense; onClose: () => void; onChanged: () => void }) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const [method, setMethod] = useState(expense.preferred_method);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ parcel_count: number; allocated_paisa: number; unallocated_paisa: number; reached_nothing: boolean } | null>(null);

  async function allocate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post(`/expenses/${expense.id}/allocate`, { method, reason: reason.trim() || undefined }));
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={expense.description} onClose={onClose}>
      <Row label={t('ex.kind')} value={t(`ex.kind.${expense.kind}` as StringKey)} />
      <Row label={t('cf.amount')} value={formatPaisa(expense.amount_paisa, { locale })} />
      <Row label={t('ex.period')} value={`${formatDate(expense.period_start, { locale })} – ${formatDate(expense.period_end, { locale })}`} />
      {expense.kind === 'AD_SPEND' ? (
        <>
          <Row label={t('ex.allocatedLabel')} value={formatPaisa(expense.allocated_paisa, { locale })} />
          <Row label={t('ex.unallocated')} value={formatPaisa(expense.unallocated_paisa, { locale })} />
          <Row label={t('ex.lastAllocated')} value={formatDate(expense.allocated_at, { locale, withTime: true })} />
          {can('money.reconcile') ? (
            <form className="card" style={{ padding: 10, marginTop: 12 }} onSubmit={allocate}>
              <strong>{expense.allocated_at ? t('ex.reallocate') : t('ex.allocate')}</strong>
              <p className="card__hint">{t('ex.allocateHint')}</p>
              <label className="field">
                <span className="field__label">{t('ex.method')}</span>
                <select className="select" value={method} onChange={(event) => setMethod(event.target.value)}>
                  {METHODS.map((value) => (
                    <option key={value} value={value}>{t(`ex.method.${value}` as StringKey)}</option>
                  ))}
                </select>
              </label>
              {expense.allocated_at ? (
                <label className="field">
                  <span className="field__label">{t('ml.reason')}</span>
                  <input className="input" maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
                </label>
              ) : null}
              {error ? <p className="formerror">{error}</p> : null}
              <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>{t('ex.allocate')}</button>
            </form>
          ) : null}
          {result ? (
            <div className={`notice ${result.reached_nothing ? 'notice--warn' : 'notice--good'}`} style={{ marginTop: 10 }}>
              {result.reached_nothing
                ? t('ex.reachedNothing')
                : t('ex.allocationResult', { parcels: result.parcel_count, allocated: formatPaisa(result.allocated_paisa, { locale }), left: formatPaisa(result.unallocated_paisa, { locale }) })}
            </div>
          ) : null}
        </>
      ) : (
        <p className="card__hint" style={{ marginTop: 10 }}>{t(`ex.kindHint.${expense.kind}` as StringKey)}</p>
      )}
    </Drawer>
  );
}
