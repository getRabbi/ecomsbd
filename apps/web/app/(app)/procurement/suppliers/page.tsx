'use client';

import { useState, type FormEvent } from 'react';

import { ProcurementTabs } from '@/components/ProcurementTabs';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate, formatPaisa } from '@/lib/money';
import { problem, toPaisa, type PurchaseOrder, type Supplier } from '@/lib/procurement';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface SupplierDetail {
  supplier: Supplier;
  items: { id: string; product_id: string; variant_id: string | null; product_name: string; variant_name: string | null; supplier_sku: string | null; last_unit_cost_paisa: number | null; last_received_at: string | null; is_preferred: boolean }[];
  purchase_orders: PurchaseOrder[];
  can_manage: boolean;
}

const FIELDS = [
  ['name', 'pr.s.name', 160],
  ['contact_name', 'pr.s.contact', 160],
  ['phone', 'pr.s.phone', 32],
  ['email', 'pr.s.email', 254],
  ['address', 'pr.s.address', 500],
  ['notes', 'pr.s.notes', 1000],
] as const;

export default function SuppliersPage() {
  const { t, locale } = useSession();
  const [search, setSearch] = useState('');
  const list = useApi<{ items: Supplier[]; can_manage: boolean }>('/procurement/suppliers', { search: search || undefined });
  const [editing, setEditing] = useState<Supplier | 'new' | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<SupplierDetail>(selected ? `/procurement/suppliers/${selected}` : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const manage = !!list.data?.can_manage;

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      list.reload();
      detail.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {};
    for (const [key] of FIELDS) {
      const value = String(data.get(key) ?? '').trim();
      body[key] = value || null;
    }
    const terms = String(data.get('payment_terms_days') ?? '');
    body.payment_terms_days = terms === '' ? null : Number(terms);
    const lead = String(data.get('lead_time_days') ?? '');
    body.lead_time_days = lead === '' ? null : Number(lead);
    body.is_active = data.get('is_active') === 'on';
    void run(async () => {
      if (editing === 'new') await api.post('/procurement/suppliers', body);
      else if (editing) await api.patch(`/procurement/suppliers/${editing.id}`, body);
      setEditing(null);
    });
  };

  const text = problem(error, t);
  return (
    <>
      <PageHeader
        title={t('pr.tab.suppliers')}
        subtitle={t('pr.subtitle')}
        actions={manage ? <button type="button" className="btn btn--primary" onClick={() => setEditing('new')}>{t('pr.newSupplier')}</button> : null}
      />
      <div className="content">
        <ProcurementTabs />
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
        {text ? <p className="formerror">{text}</p> : null}
        <Card
          title={t('pr.tab.suppliers')}
          padded={false}
          actions={<input className="input" placeholder={t('pr.st.search')} value={search} onChange={(e) => setSearch(e.target.value)} />}
        >
          {(list.data?.items.length ?? 0) === 0 && !list.loading ? (
            <EmptyState title={t('pr.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('pr.s.name')}</th>
                    <th>{t('pr.col.phone')}</th>
                    <th className="num">{t('pr.col.openOrders')}</th>
                    <th className="num">{t('pr.col.balance')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.data?.items.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelected(s.id)}>{s.name}</button>
                        {!s.is_active ? <Chip label={t('pr.s.inactive')} /> : null}
                      </td>
                      <td>{s.phone ?? '—'}</td>
                      <td className="num">{s.open_orders ?? 0}</td>
                      <td className="num">{s.balance_paisa == null ? '—' : formatPaisa(s.balance_paisa, { locale })}</td>
                      <td>{manage ? <button type="button" className="btn btn--sm" onClick={() => setEditing(s)}>{t('pr.edit')}</button> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {detail.data ? (
          <Card title={`${detail.data.supplier.name} · ${t('pr.s.items')}`} hint={t('pr.s.preferredHint')} padded={false}>
            <div className="tablewrap">
              <table className="table">
                <tbody>
                  {detail.data.items.map((item) => (
                    <tr key={item.id}>
                      <td>{item.product_name}{item.variant_name ? ` (${item.variant_name})` : ''}</td>
                      <td>{item.supplier_sku ?? ''}</td>
                      <td className="num">{t('pr.s.lastCost')}: {item.last_unit_cost_paisa == null ? '—' : formatPaisa(item.last_unit_cost_paisa, { locale })}</td>
                      <td>{formatDate(item.last_received_at, { locale })}</td>
                      <td>
                        {item.is_preferred ? (
                          <Chip label={t('pr.s.preferred')} tone="good" />
                        ) : detail.data?.can_manage ? (
                          <button
                            type="button"
                            className="btn btn--sm"
                            disabled={busy}
                            onClick={() =>
                              void run(() =>
                                api.put(`/procurement/suppliers/${detail.data?.supplier.id}/items`, {
                                  product_id: item.product_id,
                                  variant_id: item.variant_id,
                                  supplier_sku: item.supplier_sku,
                                  unit_cost_paisa: item.last_unit_cost_paisa ?? toPaisa('0'),
                                  is_preferred: true,
                                }),
                              )
                            }
                          >
                            {t('pr.s.makePreferred')}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}
      </div>

      {editing ? (
        <Drawer title={editing === 'new' ? t('pr.newSupplier') : editing.name} onClose={() => setEditing(null)}>
          <form onSubmit={save} className="wizard__body">
            {FIELDS.map(([key, label, max]) => (
              <label key={key} className="field">
                <span className="field__label">{t(label)}</span>
                <input
                  className="input"
                  name={key}
                  required={key === 'name'}
                  maxLength={max}
                  type={key === 'email' ? 'email' : key === 'phone' ? 'tel' : 'text'}
                  defaultValue={editing === 'new' ? '' : String(editing[key] ?? '')}
                />
              </label>
            ))}
            <label className="field">
              <span className="field__label">{t('pr.s.terms')}</span>
              <input className="input" name="payment_terms_days" type="number" min={0} max={365} defaultValue={editing === 'new' ? '' : String(editing.payment_terms_days ?? '')} />
            </label>
            <label className="field">
              <span className="field__label">{t('pr.f.leadTime')}</span>
              <input className="input" name="lead_time_days" type="number" min={0} max={365} defaultValue={editing === 'new' ? '' : String(editing.lead_time_days ?? '')} />
            </label>
            <label><input type="checkbox" name="is_active" defaultChecked={editing === 'new' ? true : editing.is_active} /> {t('pr.s.active')}</label>
            <button type="submit" className="btn btn--primary" disabled={busy}>{t('pr.s.save')}</button>
          </form>
        </Drawer>
      ) : null}
    </>
  );
}
