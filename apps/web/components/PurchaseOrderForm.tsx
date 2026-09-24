'use client';

import { useState, type FormEvent } from 'react';

import { Card, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import { fromPaisa, problem, toPaisa, type PoDetail, type Supplier, type Warehouse } from '@/lib/procurement';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface ProductHit {
  id: string;
  name: string;
  sku: string | null;
  cost_paisa: number;
  has_variants: boolean;
  variants: { id: string; name: string; cost_paisa: number | null; is_active: boolean }[];
}

interface Draft {
  product_id: string;
  variant_id: string | null;
  label: string;
  quantity: number;
  cost: string;
  update_cost: boolean;
}

function toInput(value: string | null): string {
  return value ? value.slice(0, 10) : '';
}

function toIso(value: string): string | null {
  return value ? new Date(`${value}T12:00:00`).toISOString() : null;
}

export function PurchaseOrderForm({ initial, onSaved }: { initial?: PoDetail; onSaved: (detail: PoDetail) => void }) {
  const { t, locale } = useSession();
  const suppliers = useApi<{ items: Supplier[] }>('/procurement/suppliers', { active: true });
  const warehouses = useApi<{ items: Warehouse[] }>('/procurement/warehouses');
  const po = initial?.purchase_order;
  const [supplier, setSupplier] = useState(po?.supplier_id ?? '');
  const [warehouse, setWarehouse] = useState(po?.warehouse_id ?? '');
  const [expected, setExpected] = useState(toInput(po?.expected_at ?? null));
  const [due, setDue] = useState(toInput(po?.payable?.due_at ?? null));
  const [reference, setReference] = useState(po?.reference ?? '');
  const [notes, setNotes] = useState(po?.notes ?? '');
  const [lines, setLines] = useState<Draft[]>(
    initial?.lines.map((line) => ({
      product_id: line.product_id,
      variant_id: line.variant_id,
      label: line.description,
      quantity: line.quantity_ordered,
      cost: fromPaisa(line.unit_cost_paisa),
      update_cost: line.update_cost,
    })) ?? [],
  );
  const [search, setSearch] = useState('');
  const hits = useApi<{ items: ProductHit[] }>(search.trim().length >= 2 ? '/products' : null, { search, limit: 10 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const locked = !!po && po.status !== 'DRAFT';

  function add(product: ProductHit, variant: ProductHit['variants'][number] | null) {
    if (lines.some((l) => l.product_id === product.id && l.variant_id === (variant?.id ?? null))) return;
    const cost = variant?.cost_paisa ?? product.cost_paisa;
    setLines([
      ...lines,
      {
        product_id: product.id,
        variant_id: variant?.id ?? null,
        label: variant ? `${product.name} (${variant.name})` : product.name,
        quantity: 1,
        cost: fromPaisa(cost),
        update_cost: true,
      },
    ]);
    setSearch('');
  }

  const total = lines.reduce((sum, l) => sum + l.quantity * toPaisa(l.cost), 0);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      supplier_id: supplier,
      warehouse_id: warehouse || null,
      expected_at: toIso(expected),
      payment_due_at: toIso(due),
      reference: reference || null,
      notes: notes || null,
      lines: lines.map((l) => ({
        product_id: l.product_id,
        variant_id: l.variant_id,
        quantity: l.quantity,
        unit_cost_paisa: toPaisa(l.cost),
        update_cost: l.update_cost,
      })),
    };
    (po
      ? api.put<PoDetail>(`/procurement/purchase-orders/${po.id}`, { ...body, version: po.version })
      : api.post<PoDetail>('/procurement/purchase-orders', body)
    )
      .then(onSaved)
      .catch(setError)
      .finally(() => setBusy(false));
  };

  const text = problem(error, t);
  return (
    <form onSubmit={submit} style={{ display: 'grid', gap: 16 }}>
      {suppliers.error ? <ErrorState error={suppliers.error} onRetry={suppliers.reload} /> : null}
      <Card title={po ? `${po.number} · ${t('pr.edit')}` : t('pr.newPo')}>
        <div className="grid2">
          <label className="field">
            <span className="field__label">{t('pr.f.supplier')}</span>
            <select className="select" required disabled={locked} value={supplier} onChange={(e) => setSupplier(e.target.value)}>
              <option value="">—</option>
              {suppliers.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field__label">{t('pr.f.location')}</span>
            <select className="select" disabled={locked} value={warehouse} onChange={(e) => setWarehouse(e.target.value)}>
              {warehouses.data?.items.filter((w) => w.is_active).map((w) => (
                <option key={w.id ?? 'main'} value={w.is_default ? '' : (w.id ?? '')}>{w.is_default ? t('pr.f.mainLocation') : w.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">{t('pr.f.expected')}</span>
            <input className="input" type="date" value={expected} onChange={(e) => setExpected(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('pr.f.paymentDue')}</span>
            <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('pr.f.reference')}</span>
            <input className="input" maxLength={120} value={reference} onChange={(e) => setReference(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('pr.f.notes')}</span>
            <input className="input" maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
        </div>
      </Card>
      <Card title={t('pr.col.item')} hint={t('pr.f.updateCostHint')} padded={false}>
        {!locked ? (
          <div className="card__body">
            <label className="field">
              <span className="field__label">{t('pr.f.searchItem')}</span>
              <input className="input" value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>
            {hits.data?.items.map((product) => (
              <div key={product.id} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', margin: '4px 0' }}>
                <strong>{product.name}</strong>
                {product.has_variants ? (
                  product.variants.filter((v) => v.is_active).map((v) => (
                    <button key={v.id} type="button" className="btn btn--sm" onClick={() => add(product, v)}>+ {v.name}</button>
                  ))
                ) : (
                  <button type="button" className="btn btn--sm" onClick={() => add(product, null)}>+</button>
                )}
              </div>
            ))}
          </div>
        ) : null}
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('pr.col.item')}</th>
                <th className="num">{t('pr.f.quantity')}</th>
                <th className="num">{t('pr.f.unitCost')}</th>
                <th>{t('pr.f.updateCost')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lines.map((line, index) => {
                const update = (patch: Partial<Draft>) => setLines(lines.map((l, i) => (i === index ? { ...l, ...patch } : l)));
                return (
                  <tr key={`${line.product_id}:${line.variant_id}`}>
                    <td>{line.label}</td>
                    <td className="num">
                      <input className="input" type="number" min={1} max={1000000} disabled={locked} value={line.quantity} onChange={(e) => update({ quantity: Number(e.target.value) })} style={{ width: 100 }} />
                    </td>
                    <td className="num">
                      <input className="input" inputMode="decimal" pattern="\d+(\.\d{1,2})?" disabled={locked} value={line.cost} onChange={(e) => update({ cost: e.target.value })} style={{ width: 120 }} />
                    </td>
                    <td><input type="checkbox" disabled={locked} checked={line.update_cost} onChange={(e) => update({ update_cost: e.target.checked })} /></td>
                    <td>{!locked ? <button type="button" className="btn btn--sm btn--ghost" onClick={() => setLines(lines.filter((_, i) => i !== index))}>{t('pr.f.remove')}</button> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="card__body"><strong>{t('pr.f.total')}: {formatPaisa(total, { locale })}</strong></p>
      </Card>
      {text ? <p className="formerror">{text}</p> : error ? <ErrorState error={error} /> : null}
      <p>
        <button type="submit" className="btn btn--primary" disabled={busy || !supplier || lines.length === 0}>{t('pr.saveDraft')}</button>
      </p>
    </form>
  );
}
