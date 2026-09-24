'use client';

import { useRef, useState, type FormEvent } from 'react';

import { ProcurementTabs } from '@/components/ProcurementTabs';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/money';
import { newKey, problem, type StockRow, type Warehouse } from '@/lib/procurement';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function StockPage() {
  const { t, locale } = useSession();
  const [search, setSearch] = useState('');
  const [low, setLow] = useState(false);
  const stock = useApi<{ items: StockRow[] }>('/procurement/stock', { search: search || undefined, low: low || undefined });
  const warehouses = useApi<{ items: Warehouse[]; can_manage: boolean }>('/procurement/warehouses');
  const transfers = useApi<{ items: { id: string; number: string; from_warehouse_id: string; to_warehouse_id: string; units: number; completed_at: string }[]; can_transfer: boolean }>('/procurement/transfers');
  const [move, setMove] = useState<{ item: StockRow; from: string; to: string; quantity: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const moveKey = useRef(newKey());
  const locations = warehouses.data?.items ?? [];
  const name = (id: string | null) => locations.find((w) => w.id === id)?.name ?? t('pr.loc.main');

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      stock.reload();
      warehouses.reload();
      transfers.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const addLocation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    void run(async () => {
      await api.post('/procurement/warehouses', { name: data.get('name'), code: data.get('code') });
      form.reset();
    });
  };

  const text = problem(error, t);
  return (
    <>
      <PageHeader title={t('pr.tab.stock')} subtitle={t('pr.st.noReservation')} />
      <div className="content">
        <ProcurementTabs />
        {stock.error ? <ErrorState error={stock.error} onRetry={stock.reload} /> : null}
        {text ? <p className="formerror">{text}</p> : null}
        <Card
          title={t('pr.tab.stock')}
          padded={false}
          actions={
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input className="input" placeholder={t('pr.st.search')} value={search} onChange={(e) => setSearch(e.target.value)} />
              <label><input type="checkbox" checked={low} onChange={(e) => setLow(e.target.checked)} /> {t('pr.st.low')}</label>
            </span>
          }
        >
          {(stock.data?.items.length ?? 0) === 0 && !stock.loading ? (
            <EmptyState title={t('pr.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('pr.col.item')}</th>
                    <th className="num">{t('pr.col.onHand')}</th>
                    <th className="num">{t('pr.col.incoming')}</th>
                    <th>{t('pr.col.locations')}</th>
                    <th className="num">{t('pr.col.damaged')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {stock.data?.items.map((row) => (
                    <tr key={`${row.product_id}:${row.variant_id}`}>
                      <td>
                        {row.name}{row.variant_name ? ` (${row.variant_name})` : ''}
                        {row.low ? <> <Chip label={t('pr.st.lowBadge')} tone="warn" /></> : null}
                      </td>
                      <td className="num">{row.on_hand}</td>
                      <td className="num">{row.incoming || '—'}</td>
                      <td>{row.locations.filter((l) => l.is_default || l.quantity).map((l) => `${l.is_default ? t('pr.loc.main') : l.name}: ${l.quantity}`).join(' · ')}</td>
                      <td className="num">{row.damaged_30d + row.rejected_on_receipt_30d || '—'}</td>
                      <td>
                        {transfers.data?.can_transfer && locations.length > 1 ? (
                          <button type="button" className="btn btn--sm" onClick={() => setMove({ item: row, from: '', to: locations.find((w) => !w.is_default)?.id ?? '', quantity: '1' })}>{t('pr.tr.new')}</button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {move ? (
          <Card title={`${t('pr.tr.new')}: ${move.item.name}${move.item.variant_name ? ` (${move.item.variant_name})` : ''}`}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
              {(['from', 'to'] as const).map((side) => (
                <label className="field" key={side}>
                  <span className="field__label">{t(side === 'from' ? 'pr.tr.from' : 'pr.tr.to')}</span>
                  <select className="select" value={move[side]} onChange={(e) => setMove({ ...move, [side]: e.target.value })}>
                    {locations.filter((w) => w.is_active).map((w) => <option key={w.id ?? 'main'} value={w.is_default ? '' : (w.id ?? '')}>{w.is_default ? t('pr.loc.main') : w.name}</option>)}
                  </select>
                </label>
              ))}
              <label className="field">
                <span className="field__label">{t('pr.f.quantity')}</span>
                <input className="input" type="number" min={1} value={move.quantity} onChange={(e) => setMove({ ...move, quantity: e.target.value })} />
              </label>
              <button
                type="button"
                className="btn btn--primary"
                disabled={busy || move.from === move.to}
                onClick={() =>
                  void run(async () => {
                    await api.post('/procurement/transfers', {
                      from_warehouse_id: move.from || null,
                      to_warehouse_id: move.to || null,
                      lines: [{ product_id: move.item.product_id, variant_id: move.item.variant_id, quantity: Number(move.quantity) }],
                      idempotency_key: moveKey.current,
                    });
                    moveKey.current = newKey();
                    setMove(null);
                  })
                }
              >
                {t('pr.tr.submit')}
              </button>
            </div>
          </Card>
        ) : null}

        <div className="grid2">
          <Card title={t('pr.loc.title')} hint={t('pr.loc.hint')}>
            {locations.map((w) => (
              <p key={w.id ?? 'main'}>
                <strong>{w.is_default ? t('pr.loc.main') : w.name}</strong> · {w.code}{!w.is_active ? ` · ${t('pr.s.inactive')}` : ''}
                {warehouses.data?.can_manage && !w.is_default && w.is_active && w.id ? (
                  <> <button type="button" className="btn btn--sm btn--ghost" disabled={busy} onClick={() => void run(() => api.patch(`/procurement/warehouses/${w.id}`, { name: w.name, code: w.code, is_active: false }))}>{t('pr.loc.close')}</button></>
                ) : null}
              </p>
            ))}
            {warehouses.data?.can_manage ? (
              <form onSubmit={addLocation} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
                <label className="field"><span className="field__label">{t('pr.loc.name')}</span><input className="input" name="name" required maxLength={80} /></label>
                <label className="field"><span className="field__label">{t('pr.loc.code')}</span><input className="input" name="code" required maxLength={24} pattern="[A-Za-z0-9_-]+" /></label>
                <button type="submit" className="btn" disabled={busy}>{t('pr.loc.add')}</button>
              </form>
            ) : null}
          </Card>
          <Card title={t('pr.tr.title')}>
            {transfers.data?.items.map((tr) => (
              <p key={tr.id}>{tr.number} · {name(tr.from_warehouse_id)} → {name(tr.to_warehouse_id)} · {t('pr.tr.units', { units: tr.units })} · {formatDate(tr.completed_at, { locale })}</p>
            ))}
          </Card>
        </div>
      </div>
    </>
  );
}
