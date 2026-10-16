'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';

import { Card, Chip, ErrorState, Row } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate, formatPaisa } from '@/lib/money';
import { errorMessage } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

interface CustomerDetail {
  id: string;
  name: string | null;
  phone_masked: string;
  flag: string;
  flag_reason: string | null;
  notes: string | null;
  is_repeat_buyer: boolean;
  order_count: number;
  delivered_count: number;
  returned_count: number;
  cancelled_count: number;
  success_rate_basis_points: number | null;
  realized_revenue_paisa: number;
  first_order_at: string | null;
  last_order_at: string | null;
  addresses: { id: string; raw_address: string; label: string | null; district: string | null; area: string | null; is_default: boolean }[];
}

const FLAGS = ['NONE', 'STARRED', 'BLOCKED'] as const;

/**
 * The customer's own record: identity, addresses, delivery history, the
 * seller's flag, and the audited phone reveal.
 *
 * The full number is never in a table or in browser storage. Reveal needs the
 * export permission and a reason, goes through the audited endpoint, and lives
 * only in this component's memory until the page is left.
 */
export function CustomerProfile({ id }: { id: string }) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const customer = useApi<CustomerDetail>(`/customers/${id}`);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [reason, setReason] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const data = customer.data;

  async function reveal(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ phone: string }>(`/customers/${id}/reveal-phone`, { reason: reason.trim() });
      setRevealed(result.phone);
      setRevealing(false);
      setReason('');
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (customer.error) return <Card><ErrorState error={customer.error} onRetry={customer.reload} /></Card>;
  if (!data) return <Card><p className="card__hint">{t('common.loading')}</p></Card>;

  const success = data.success_rate_basis_points === null ? '—' : `${Math.round(data.success_rate_basis_points / 100)}%`;

  return (
    <div className="grid2" style={{ marginBottom: 14 }}>
      <Card
        title={data.name || data.phone_masked}
        actions={
          <>
            {data.flag !== 'NONE' ? <Chip label={t(`cp.flag.${data.flag}` as 'cp.flag.NONE')} tone={data.flag === 'BLOCKED' ? 'bad' : 'good'} /> : null}{' '}
            {data.is_repeat_buyer ? <Chip label={t('cp.repeat')} tone="good" /> : null}
          </>
        }
      >
        <Row
          label={t('oc.phone')}
          value={
            <>
              {revealed ?? data.phone_masked}{' '}
              {revealed ? (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setRevealed(null)}>
                  {t('cp.hide')}
                </button>
              ) : can('customer.export') ? (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setRevealing((value) => !value)}>
                  {t('cp.reveal')}
                </button>
              ) : null}
            </>
          }
        />
        {revealing ? (
          <form className="card" style={{ padding: 10, margin: '6px 0' }} onSubmit={reveal}>
            <label className="field">
              <span className="field__label">{t('cp.revealReason')} *</span>
              <input className="input" required minLength={3} maxLength={200} value={reason} onChange={(event) => setReason(event.target.value)} />
            </label>
            <p className="card__hint">{t('cp.revealHint')}</p>
            <button type="submit" className="btn btn--sm" disabled={busy}>{t('cp.reveal')}</button>
          </form>
        ) : null}
        <Row label={t('cp.orders')} value={t('oc.history', { orders: data.order_count, delivered: data.delivered_count, returned: data.returned_count })} />
        <Row label={t('cp.cancelled')} value={data.cancelled_count} />
        <Row label={t('cust.success')} value={success} />
        {can('money.view') ? <Row label={t('cp.revenue')} value={formatPaisa(data.realized_revenue_paisa, { locale })} /> : null}
        <Row label={t('cp.firstLast')} value={`${formatDate(data.first_order_at, { locale })} – ${formatDate(data.last_order_at, { locale })}`} />
        {data.flag_reason ? <p className="card__hint">{data.flag_reason}</p> : null}
        <div className="toolbar" style={{ padding: '10px 0 0', flexWrap: 'wrap' }}>
          <Link className="btn btn--sm" href={`/orders?customer=${encodeURIComponent(data.id)}`}>
            {t('cp.allOrders')}
          </Link>
          {can('order.write') ? (
            <button type="button" className="btn btn--sm" onClick={() => setEditing((value) => !value)}>
              {t('cp.editFlag')}
            </button>
          ) : null}
        </div>
        {error ? <p className="formerror" role="alert">{error}</p> : null}
        {editing ? (
          <FlagForm
            customer={data}
            onSaved={() => {
              setEditing(false);
              customer.reload();
            }}
          />
        ) : null}
      </Card>

      <Card title={t('cp.addresses')}>
        {data.addresses.length === 0 ? <p className="card__hint">{t('cp.noAddresses')}</p> : null}
        {data.addresses.map((address) => (
          <div key={address.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--stroke)' }}>
            <div>
              {address.raw_address} {address.is_default ? <Chip label={t('cp.default')} /> : null}
            </div>
            <div className="table__sub">{[address.label, address.area, address.district].filter(Boolean).join(' · ')}</div>
          </div>
        ))}
        {data.notes ? (
          <>
            <h3 className="card__title" style={{ marginTop: 12 }}>{t('oc.note')}</h3>
            <p className="card__hint" style={{ whiteSpace: 'pre-wrap' }}>{data.notes}</p>
          </>
        ) : null}
      </Card>
    </div>
  );
}

function FlagForm({ customer, onSaved }: { customer: CustomerDetail; onSaved: () => void }) {
  const { t } = useSession();
  const [name, setName] = useState(customer.name ?? '');
  const [flag, setFlag] = useState(customer.flag);
  const [flagReason, setFlagReason] = useState(customer.flag_reason ?? '');
  const [notes, setNotes] = useState(customer.notes ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/customers/${customer.id}`, {
        name: name.trim() || undefined,
        notes,
        flag,
        flag_reason: flagReason.trim() || undefined,
      });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" style={{ padding: 10, marginTop: 10 }} onSubmit={save}>
      <label className="field">
        <span className="field__label">{t('oc.name')}</span>
        <input className="input" maxLength={160} value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="field">
        <span className="field__label">{t('cp.flagLabel')}</span>
        <select className="select" value={flag} onChange={(event) => setFlag(event.target.value)}>
          {FLAGS.map((value) => (
            <option key={value} value={value}>{t(`cp.flag.${value}` as 'cp.flag.NONE')}</option>
          ))}
        </select>
        <span className="field__hint">{t('cp.flagHint')}</span>
      </label>
      <label className="field">
        <span className="field__label">{t('cp.flagReason')}</span>
        <input className="input" maxLength={400} value={flagReason} onChange={(event) => setFlagReason(event.target.value)} />
      </label>
      <label className="field">
        <span className="field__label">{t('oc.note')}</span>
        <textarea className="textarea" rows={3} maxLength={4000} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </label>
      {error ? <p className="formerror">{error}</p> : null}
      <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>{t('set.save')}</button>
    </form>
  );
}
