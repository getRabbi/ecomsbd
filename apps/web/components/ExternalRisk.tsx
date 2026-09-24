'use client';

import Link from 'next/link';
import { useState } from 'react';

import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { cellValue, when, type ProviderSection, type RiskProfile } from '@/lib/risk';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';
import { Card, Chip, ErrorState, Row, type Tone } from './ui';

const STATE_TONE: Record<string, Tone> = { LOW: 'good', MEDIUM: 'warn', HIGH: 'bad', INSUFFICIENT_DATA: 'neutral' };

/**
 * A customer's delivery risk as three separate sections (V3.7): the shop's own
 * history, what a connected provider reported, and anonymous network context.
 * They are never blended into one number.
 */
export function ExternalRisk({ customerId }: { customerId: string }) {
  const { t, locale } = useSession();
  const profile = useApi<RiskProfile>(`/customers/${customerId}/risk-profile`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function lookup(refresh: boolean) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body = await api.post<RiskProfile['external']>(`/external-risk/customers/${customerId}/lookup`, { refresh });
      const outcome = body.outcomes?.[0];
      if (outcome?.outcome === 'CACHED') setNotice(t('risk.ext.cached'));
      if (outcome?.outcome === 'SKIPPED' && outcome.error_code) setNotice(t('risk.ext.skipped', { reason: t(`risk.err.${outcome.error_code}` as StringKey) }));
      profile.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (profile.error) return <ErrorState error={profile.error} onRetry={profile.reload} />;
  const data = profile.data;
  if (!data) return <Card title={t('risk.title')}><p className="card__hint">{t('common.loading')}</p></Card>;
  const own = data.own_shop;
  const external = data.external;
  const network = data.network.cells.filter((c) => c.dimension === 'ALL');

  return (
    <div style={{ display: 'grid', gap: 18, marginTop: 18 }}>
      <Card title={t('risk.own.title')} hint={t('risk.own.hint')}>
        <Row label={t('risk.own.band')} value={<Chip label={t(`risk.state.${own.state}` as StringKey)} tone={STATE_TONE[own.state] ?? 'neutral'} />} />
        <Row label={t('risk.own.success')} value={own.success_rate_basis_points === null ? '—' : `${(own.success_rate_basis_points / 100).toFixed(0)}%`} />
        <Row label={t('risk.own.orders')} value={own.order_count} />
        <Row label={t('risk.own.delivered')} value={own.delivered_count} />
        <Row label={t('risk.own.returned')} value={own.returned_count} />
        <Row label={t('risk.own.cancelled')} value={own.cancelled_count} />
        <Row label={t('risk.own.inTransit')} value={own.in_transit_count} />
        {own.repeated_rto ? <p><Chip label={t('risk.own.repeatedRto')} tone="warn" /></p> : null}
        {own.recent.length ? (
          <>
            <p className="card__hint" style={{ marginTop: 12 }}>{t('risk.own.recent')}</p>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {own.recent.slice(0, 5).map((event) => (
                <li key={`${event.order_number}-${event.at}`}>{event.order_number} · {event.provider} · {event.outcome} · {when(event.at, locale)}</li>
              ))}
            </ul>
          </>
        ) : null}
      </Card>

      <Card title={t('risk.ext.title')} hint={t('risk.ext.hint')}>
        {error ? <ErrorState error={error} /> : null}
        {notice ? <p className="card__hint">{notice}</p> : null}
        {external.status === 'GATED' ? <p>{t('risk.ext.gated')}</p> : null}
        {external.status === 'NOT_CONFIGURED' ? (
          <p>{t('risk.ext.notConfigured')} <Link href="/settings/risk-provider">{t('risk.ext.setup')}</Link></p>
        ) : null}
        {external.providers.filter((p) => p.enabled).map((section) => (
          <ProviderFacts key={section.provider_id} section={section} />
        ))}
        {external.status === 'AVAILABLE' ? (
          data.can_lookup ? (
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className="btn btn--primary" disabled={busy} onClick={() => void lookup(false)}>{busy ? t('risk.ext.checking') : t('risk.ext.check')}</button>
              <button className="btn" disabled={busy} onClick={() => void lookup(true)}>{t('risk.ext.refresh')}</button>
            </div>
          ) : (
            <p className="card__hint">{t('risk.ext.noPermission')}</p>
          )
        ) : null}
      </Card>

      <Card title={t('risk.net.title')} hint={t('risk.net.hint')}>
        {data.network.period ? (
          <>
            <Row label={t('net.period')} value={`${data.network.period} (UTC)`} />
            {network.map((cell) => (
              <Row key={cell.metric} label={t(`net.metric.${cell.metric}` as StringKey)} value={cellValue(cell, t)} />
            ))}
          </>
        ) : (
          <p className="card__hint">{t('risk.net.none')}</p>
        )}
      </Card>
    </div>
  );
}

function ProviderFacts({ section }: { section: ProviderSection }) {
  const { t, locale } = useSession();
  const result = section.result;
  return (
    <div style={{ borderTop: '1px solid var(--stroke)', paddingTop: 12, marginTop: 12 }}>
      <p style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <strong>{section.name}</strong>
        <Chip label={t(`rp.health.${section.health}` as StringKey)} tone={section.health === 'HEALTHY' ? 'good' : section.health === 'UNKNOWN' ? 'neutral' : 'warn'} />
        {result ? <Chip label={result.freshness === 'FRESH' ? t('risk.ext.fresh') : t('risk.ext.stale')} tone={result.freshness === 'FRESH' ? 'good' : 'warn'} /> : null}
      </p>
      {result ? (
        <>
          <p>{result.status === 'FOUND' ? t('risk.ext.found') : t('risk.ext.notFound')}</p>
          {result.facts.map((fact) => (
            <Row
              key={fact.code}
              label={t(`risk.fact.${fact.code}` as StringKey)}
              value={fact.period_days ? `${fact.value} (${t('risk.ext.period', { n: fact.period_days })})` : fact.value}
            />
          ))}
          <p className="card__hint">
            {result.provider_observed_at ? `${t('risk.ext.observed', { when: when(result.provider_observed_at, locale) })} · ` : ''}
            {t('risk.ext.checked', { when: when(result.checked_at, locale) })}
            {result.sample_size !== null ? ` · ${t('risk.ext.sample', { n: result.sample_size })}` : ''}
            {result.confidence ? ` · ${t('risk.ext.confidence', { value: result.confidence })}` : ''}
          </p>
        </>
      ) : (
        <p className="card__hint">{t('risk.ext.none')}</p>
      )}
      {section.last_error ? (
        <p className="card__hint">{t('risk.ext.lastError', { reason: t(`risk.err.${section.last_error.code}` as StringKey) })}</p>
      ) : null}
    </div>
  );
}
