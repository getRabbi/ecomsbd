'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';

import { Card, Chip, Row } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/money';
import { errorMessage, type RiskCheck as RiskResult } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { describeRisk } from '@/lib/states';

/**
 * Risk check by phone number — the seller's own finished orders, banded by
 * the backend (`GET /customers/risk-check`).
 *
 * Advisory only. It never blocks an order, never labels a person, and a thin
 * history is reported as thin rather than as low risk. External provider
 * results, where a provider is configured, live on the customer's page.
 */
export function RiskCheckCard({ autoFocus = false }: { autoFocus?: boolean }) {
  const { t, locale } = useSession();
  const [phone, setPhone] = useState('');
  const [result, setResult] = useState<RiskResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.get<RiskResult>('/customers/risk-check', { phone: phone.trim() }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const band = result ? describeRisk(result.state, locale) : null;

  return (
    <Card title={t('act.riskCheck')} hint={t('rc.hint')}>
      <form className="toolbar" style={{ padding: 0 }} onSubmit={check}>
        <input
          className="input"
          type="tel"
          required
          minLength={6}
          maxLength={24}
          autoFocus={autoFocus}
          placeholder="01XXXXXXXXX"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          aria-label={t('oc.phone')}
        />
        <button type="submit" className="btn btn--primary" disabled={busy}>
          {busy ? t('common.loading') : t('rc.check')}
        </button>
      </form>
      {error ? <p className="formerror" style={{ marginTop: 8 }}>{error}</p> : null}
      {result && band ? (
        <div style={{ marginTop: 12 }}>
          <Row label={t('orders.risk')} value={<Chip label={band.label} tone={band.tone} />} />
          {result.found ? (
            <>
              <Row
                label={t('orders.customer')}
                value={result.customer_id ? <Link href={`/customers/${result.customer_id}`}>{result.name ?? result.phone_masked}</Link> : result.phone_masked}
              />
              <Row label={t('cp.orders')} value={t('oc.history', { orders: result.order_count, delivered: result.delivered_count, returned: result.returned_count })} />
              <Row label={t('cp.cancelled')} value={result.cancelled_count} />
              {result.in_transit_count ? <Row label={t('cf.inTransit')} value={result.in_transit_count} /> : null}
              <Row label={t('cp.firstLast')} value={`${formatDate(result.first_order_at, { locale })} – ${formatDate(result.last_order_at, { locale })}`} />
            </>
          ) : (
            <p className="card__hint">{t('rc.notFound')}</p>
          )}
          {result.reasons.length > 0 ? (
            <ul className="card__hint" style={{ paddingLeft: 18 }}>
              {result.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          {result.checks_remaining !== null ? <p className="card__hint">{t('rc.remaining', { count: result.checks_remaining })}</p> : null}
          <p className="card__hint">{t('oc.riskAdvisory')}</p>
        </div>
      ) : null}
    </Card>
  );
}
