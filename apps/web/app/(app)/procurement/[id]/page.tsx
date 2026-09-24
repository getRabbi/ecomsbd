'use client';

import Link from 'next/link';
import { use, useRef, useState } from 'react';

import { PurchaseOrderForm } from '@/components/PurchaseOrderForm';
import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState, Row, Tile } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { METHODS, REJECT_REASONS, newKey, problem, statusTone, toPaisa, type PoDetail } from '@/lib/procurement';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function PurchaseOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <PurchaseOrderDetail key={id} id={id} />;
}

type Counts = Record<string, { accepted: string; rejected: string; reason: string }>;

function PurchaseOrderDetail({ id }: { id: string }) {
  const { t, locale } = useSession();
  const detail = useApi<PoDetail>(`/procurement/purchase-orders/${id}`);
  const [editing, setEditing] = useState(false);
  const [counts, setCounts] = useState<Counts>({});
  const [supplierRef, setSupplierRef] = useState('');
  const [overReason, setOverReason] = useState('');
  const [payment, setPayment] = useState({ amount: '', method: 'BKASH', reference: '' });
  const [cancelReason, setCancelReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  // One key per intended receipt / payment: a double click or a retry after
  // a lost response records it once.
  const receiptKey = useRef(newKey());
  const paymentKey = useRef(newKey());

  const data = detail.data;
  if (!data) {
    return <div className="content">{detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}</div>;
  }
  const po = data.purchase_order;
  const open = po.status === 'ORDERED' || po.status === 'PARTIALLY_RECEIVED';

  async function act(work: () => Promise<unknown>, message?: string) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await work();
      if (message) setDone(message);
      detail.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const receive = () =>
    act(async () => {
      const lines = data.lines
        .map((line) => {
          const c = counts[line.id] ?? { accepted: '', rejected: '', reason: '' };
          return {
            line_id: line.id,
            accepted: Number(c.accepted || 0),
            rejected: Number(c.rejected || 0),
            reject_reason: Number(c.rejected || 0) > 0 ? c.reason || 'DAMAGED' : null,
          };
        })
        .filter((l) => l.accepted + l.rejected > 0);
      await api.post(`/procurement/purchase-orders/${id}/receive`, {
        lines,
        idempotency_key: receiptKey.current,
        supplier_reference: supplierRef || null,
        over_receipt_reason: overReason || null,
      });
      receiptKey.current = newKey();
      setCounts({});
      setSupplierRef('');
      setOverReason('');
    }, t('pr.r.done'));

  const pay = () =>
    act(async () => {
      await api.post(`/procurement/purchase-orders/${id}/payments`, {
        amount_paisa: toPaisa(payment.amount),
        method: payment.method,
        reference: payment.reference || null,
        idempotency_key: paymentKey.current,
      });
      paymentKey.current = newKey();
      setPayment({ amount: '', method: payment.method, reference: '' });
    });

  const text = problem(error, t);
  const payable = po.payable;
  return (
    <>
      <PageHeader
        title={`${po.number} · ${po.supplier_name ?? ''}`}
        subtitle={po.source === 'AUTOMATION' ? t('auto.cfg.draftPoNote') : undefined}
        actions={<Chip label={t(`pr.status.${po.status}` as StringKey)} tone={statusTone(po.status)} />}
      />
      <div className="content">
        <Link href="/procurement">← {t('pr.tab.orders')}</Link>
        {text ? <p className="formerror">{text}</p> : error ? <ErrorState error={error} /> : null}
        {done ? <p role="status" className="text--good">{done}</p> : null}

        <div className="tiles">
          <Tile label={t('pr.col.total')} value={formatPaisa(po.total_paisa, { locale })} />
          <Tile label={t('pr.col.received')} value={formatPaisa(po.received_value_paisa, { locale })} />
          {payable ? (
            <Tile
              label={t('pr.totals.outstanding')}
              value={formatPaisa(payable.balance_paisa, { locale })}
              hint={
                payable.overdue
                  ? t('pr.pay.overdue')
                  : payable.advance_paisa
                    ? t('pr.pay.advance', { amount: formatPaisa(payable.advance_paisa, { locale }) })
                    : t(`pr.pay.${payable.status}` as StringKey)
              }
            />
          ) : null}
          <Tile label={t('pr.col.expected')} value={formatDate(po.expected_at, { locale })} />
        </div>

        {data.can_manage && (po.status === 'DRAFT' || open) ? (
          <Card title={po.status === 'DRAFT' ? t('pr.order') : t('pr.cancel')} hint={po.status === 'DRAFT' ? t('pr.orderHint') : undefined}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
              {po.status === 'DRAFT' ? (
                <>
                  <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void act(() => api.post(`/procurement/purchase-orders/${id}/order`))}>{t('pr.order')}</button>
                  <button type="button" className="btn" onClick={() => setEditing(!editing)}>{t('pr.edit')}</button>
                </>
              ) : null}
              <label className="field">
                <span className="field__label">{t('pr.cancelReason')}</span>
                <input className="input" minLength={3} maxLength={300} value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} />
              </label>
              <button type="button" className="btn btn--ghost" disabled={busy || cancelReason.trim().length < 3} onClick={() => void act(() => api.post(`/procurement/purchase-orders/${id}/cancel`, { reason: cancelReason }))}>{t('pr.cancel')}</button>
            </div>
          </Card>
        ) : null}

        {editing && po.status === 'DRAFT' ? (
          <PurchaseOrderForm initial={data} onSaved={() => { setEditing(false); detail.reload(); }} />
        ) : null}

        <Card title={open && data.can_receive ? t('pr.receive') : t('pr.col.item')} hint={open && data.can_receive ? t('pr.receiveHint') : undefined} padded={false}>
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('pr.col.item')}</th>
                  <th className="num">{t('pr.col.ordered')}</th>
                  <th className="num">{t('pr.col.receivedQty')}</th>
                  <th className="num">{t('pr.col.rejected')}</th>
                  <th className="num">{t('pr.col.remaining')}</th>
                  <th className="num">{t('pr.col.unitCost')}</th>
                  {open && data.can_receive ? <><th>{t('pr.r.accept')}</th><th>{t('pr.r.reject')}</th><th>{t('pr.r.reason')}</th></> : null}
                </tr>
              </thead>
              <tbody>
                {data.lines.map((line) => {
                  const c = counts[line.id] ?? { accepted: '', rejected: '', reason: 'DAMAGED' };
                  const set = (patch: Partial<typeof c>) => setCounts({ ...counts, [line.id]: { ...c, ...patch } });
                  return (
                    <tr key={line.id}>
                      <td>{line.description}</td>
                      <td className="num">{line.quantity_ordered}</td>
                      <td className="num">{line.quantity_received}</td>
                      <td className="num">{line.quantity_rejected}</td>
                      <td className="num">{line.remaining}</td>
                      <td className="num">{formatPaisa(line.unit_cost_paisa, { locale })}</td>
                      {open && data.can_receive ? (
                        <>
                          <td><input className="input" type="number" min={0} value={c.accepted} placeholder={String(line.remaining)} onChange={(e) => set({ accepted: e.target.value })} style={{ width: 90 }} aria-label={t('pr.r.accept')} /></td>
                          <td><input className="input" type="number" min={0} value={c.rejected} onChange={(e) => set({ rejected: e.target.value })} style={{ width: 90 }} aria-label={t('pr.r.reject')} /></td>
                          <td>
                            <select className="select" value={c.reason} onChange={(e) => set({ reason: e.target.value })} aria-label={t('pr.r.reason')}>
                              {REJECT_REASONS.map((r) => <option key={r} value={r}>{t(`pr.r.reason.${r}` as StringKey)}</option>)}
                            </select>
                          </td>
                        </>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {open && data.can_receive ? (
            <div className="card__body" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
              <label className="field">
                <span className="field__label">{t('pr.r.supplierRef')}</span>
                <input className="input" maxLength={120} value={supplierRef} onChange={(e) => setSupplierRef(e.target.value)} />
              </label>
              {data.can_manage ? (
                <label className="field">
                  <span className="field__label">{t('pr.r.over')}</span>
                  <input className="input" maxLength={300} value={overReason} onChange={(e) => setOverReason(e.target.value)} />
                  <span className="card__hint">{t('pr.r.overHint')}</span>
                </label>
              ) : null}
              <button type="button" className="btn btn--primary" disabled={busy || !Object.values(counts).some((c) => Number(c.accepted || 0) + Number(c.rejected || 0) > 0)} onClick={() => void receive()}>{t('pr.r.submit')}</button>
            </div>
          ) : null}
        </Card>

        <div className="grid2">
          <Card title={t('pr.receipts')}>
            {data.receipts.map((r) => (
              <Row
                key={r.id}
                label={`${formatDate(r.received_at, { locale, withTime: true })}${r.supplier_reference ? ` · ${r.supplier_reference}` : ''}`}
                value={`+${r.accepted_units}${r.rejected_units ? ` / −${r.rejected_units}` : ''} · ${formatPaisa(r.value_paisa, { locale })}`}
              />
            ))}
          </Card>
          {payable ? (
            <Card title={t('pr.payments')} hint={t('pr.p.separate')}>
              {data.payments.map((p) => (
                <Row key={p.id} label={`${formatDate(p.paid_at, { locale })} · ${t(`pr.p.method.${p.method}` as StringKey)}${p.reference ? ` · ${p.reference}` : ''}`} value={formatPaisa(p.amount_paisa, { locale })} />
              ))}
              {data.can_pay && po.status !== 'DRAFT' ? (
                <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                  <label className="field">
                    <span className="field__label">{t('pr.p.amount')}</span>
                    <input className="input" inputMode="decimal" value={payment.amount} onChange={(e) => setPayment({ ...payment, amount: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field__label">{t('pr.p.method')}</span>
                    <select className="select" value={payment.method} onChange={(e) => setPayment({ ...payment, method: e.target.value })}>
                      {METHODS.map((m) => <option key={m} value={m}>{t(`pr.p.method.${m}` as StringKey)}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span className="field__label">{t('pr.p.reference')}</span>
                    <input className="input" maxLength={120} value={payment.reference} onChange={(e) => setPayment({ ...payment, reference: e.target.value })} />
                  </label>
                  <button type="button" className="btn btn--primary" disabled={busy || toPaisa(payment.amount || '0') <= 0} onClick={() => void pay()}>{t('pr.pay')}</button>
                </div>
              ) : null}
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}
