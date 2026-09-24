'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState, Row } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { when, type ProviderCatalog } from '@/lib/risk';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Provider = ProviderCatalog['providers'][number];

/** Settings → Risk provider (V3.7). Owner only; the API enforces it. */
export default function RiskProviderPage() {
  const { t } = useSession();
  const catalog = useApi<ProviderCatalog>('/external-risk/providers');
  return (
    <>
      <PageHeader title={t('rp.title')} subtitle={t('rp.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        <Card>
          <p className="card__hint">{t('rp.privacy')}</p>
        </Card>
        {catalog.error ? <ErrorState error={catalog.error} onRetry={catalog.reload} /> : null}
        {catalog.data?.status === 'GATED' ? (
          <Card>
            <p>{t('rp.gated')}</p>
          </Card>
        ) : null}
        {catalog.data?.providers.map((provider) => (
          <ProviderCard key={provider.provider_id} provider={provider} onChange={catalog.reload} />
        ))}
        {!catalog.data && !catalog.error ? <p className="card__hint">{t('common.loading')}</p> : null}
      </div>
    </>
  );
}

function ProviderCard({ provider, onChange }: { provider: Provider; onChange: () => void }) {
  const { t, locale } = useSession();
  const connection = provider.connection;
  const [values, setValues] = useState<Record<string, string>>({});
  const [ttl, setTtl] = useState<number>(connection?.cache_ttl_hours ?? provider.cache_ttl_hours.default);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const path = `/external-risk/providers/${provider.provider_id}`;
  const filled = provider.credential_fields.every((f) => (values[f.name] ?? '').trim().length > 0);

  async function run(action: () => Promise<string | null>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      setMessage(await action());
      onChange();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(async () => {
      await api.put(path, { credentials: filled ? values : undefined, cache_ttl_hours: ttl });
      setValues({});
      return filled ? t('rp.saved') : null;
    });
  const test = () =>
    run(async () => {
      const result = await api.post<{ ok: boolean; error_code: string | null }>(`${path}/test`);
      return result.ok ? t('rp.testOk') : t('rp.testFailed', { reason: t(`risk.err.${result.error_code}` as StringKey) });
    });
  const toggle = (enabled: boolean) => run(async () => (await api.patch(`${path}/state`, { enabled }), null));
  const remove = () => run(async () => (await api.delete(path), null));

  return (
    <Card
      title={provider.name}
      hint={`${t('rp.contract')}: ${provider.official_contract}`}
      actions={connection ? <Chip label={connection.enabled ? t('rp.on') : t('rp.off')} tone={connection.enabled ? 'good' : 'neutral'} /> : null}
    >
      {error ? <ErrorState error={error} /> : null}
      {message ? <p className="card__hint">{message}</p> : null}
      <Row label={t('rp.capabilities')} value={provider.capabilities.map((c) => t(`rp.cap.${c}` as StringKey)).join(' · ')} />
      <h3 className="card__title" style={{ marginTop: 16 }}>{t('rp.credentials')}</h3>
      <p className="card__hint">
        {connection?.credentials_set ? t('rp.credentialsSet', { hint: connection.credential_hint ?? '' }) : t('rp.credentialsNote')}
      </p>
      {provider.credential_fields.map((field) => (
        <label key={field.name} style={{ display: 'block', marginTop: 8 }}>
          <span className="card__hint">{locale === 'bn' ? field.label_bn : field.label_en}</span>
          <input
            className="input"
            type={field.secret ? 'password' : 'text'}
            autoComplete="off"
            value={values[field.name] ?? ''}
            onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
          />
        </label>
      ))}
      <label style={{ display: 'block', marginTop: 8 }}>
        <span className="card__hint">{t('rp.cacheTtl')}</span>
        <input
          className="input"
          type="number"
          min={provider.cache_ttl_hours.min}
          max={provider.cache_ttl_hours.max}
          value={ttl}
          onChange={(e) => setTtl(Number(e.target.value))}
        />
      </label>
      <p className="card__hint">{t('rp.cacheHint')}</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy} onClick={() => void save()}>{t('rp.save')}</button>
        <button className="btn" disabled={busy || !connection?.credentials_set} onClick={() => void test()}>{t('rp.test')}</button>
        {connection?.enabled ? (
          <button className="btn" disabled={busy} onClick={() => void toggle(false)}>{t('rp.disable')}</button>
        ) : (
          <button className="btn" disabled={busy || connection?.last_test_result !== 'OK'} onClick={() => void toggle(true)}>{t('rp.enable')}</button>
        )}
        {connection?.credentials_set ? <button className="btn" disabled={busy} onClick={() => void remove()}>{t('rp.remove')}</button> : null}
      </div>
      {connection ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="card__title">{t('rp.health')}</h3>
          <Row label={t('rp.health')} value={t(`rp.health.${connection.health}` as StringKey)} />
          <Row label={t('rp.lastTest')} value={`${when(connection.last_tested_at, locale)}${connection.last_test_result && connection.last_test_result !== 'OK' ? ` · ${t(`risk.err.${connection.last_test_result}` as StringKey)}` : ''}`} />
          <Row label={t('rp.lastSuccess')} value={when(connection.last_success_at, locale)} />
          <Row label={t('rp.lastFailure')} value={`${when(connection.last_failure_at, locale)}${connection.last_error_code ? ` · ${t(`risk.err.${connection.last_error_code}` as StringKey)}` : ''}`} />
          {connection.rate_limited_until ? <Row label={t('rp.rateLimitedUntil')} value={when(connection.rate_limited_until, locale)} /> : null}
        </div>
      ) : null}
    </Card>
  );
}
