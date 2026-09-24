'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Row, type Tone } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Key = {
  id: string;
  name: string;
  scopes: string[];
  rate_limit: number;
  state: 'ACTIVE' | 'REVOKED' | 'EXPIRED';
  expires_at: string | null;
  last_used_at: string | null;
  connection_id: string | null;
};
type Hook = { id: string; url: string; topics: string[]; enabled: boolean };
type Delivery = { id: string; endpoint_id: string; topic: string; status: string; attempts: number; last_status: number | null; last_error: string | null; created_at: string };
type Health = {
  api: { state: 'ACTIVE' | 'NO_ACTIVE_KEY'; active_keys: number; last_request_at: string | null; expiring_soon: string[] };
  webhooks: (Hook & { state: 'ACTIVE' | 'FAILING' | 'DISABLED'; failed_24h: number; latest_delivery: Delivery | null; latest_error: { status: string; last_status: number | null; last_error: string | null; at: string } | null })[];
  connections: { id: string; name: string; health: string; state: string; last_success_at: string | null }[];
};
type Samples = {
  order: Record<string, unknown>;
  events: Record<string, Record<string, unknown>>;
  signature_example: { secret: string; timestamp: number; body: string; header: string };
};
type Tab = 'keys' | 'webhooks' | 'deliveries' | 'requests' | 'health' | 'tools';
const TABS: Tab[] = ['keys', 'webhooks', 'deliveries', 'requests', 'health', 'tools'];
const STATE_TONE: Record<string, Tone> = { ACTIVE: 'good', REVOKED: 'neutral', EXPIRED: 'warn', FAILING: 'bad', DISABLED: 'neutral', DELIVERED: 'good', FAILED: 'bad', RETRY: 'warn' };

function useWhen() {
  const { locale } = useSession();
  return (value: string | null | undefined) =>
    value ? new Date(value).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '—';
}

/** Developer portal (V3.8): owner only; the API enforces it. */
export default function DevelopersPage() {
  const { t } = useSession();
  const [tab, setTab] = useState<Tab>('keys');
  const [secret, setSecret] = useState<string | null>(null);
  return (
    <>
      <PageHeader title={t('dev.title')} subtitle={t('dev.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        <div role="tablist" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {TABS.map((key) => (
            <button key={key} role="tab" aria-selected={tab === key} className={`btn btn--sm${tab === key ? ' btn--primary' : ''}`} onClick={() => setTab(key)}>
              {t(`dev.tab.${key}`)}
            </button>
          ))}
        </div>
        {secret ? (
          <Card title={t('dev.once')} hint={t('dev.onceHint')}>
            <code style={{ overflowWrap: 'anywhere' }}>{secret}</code>
            <p><button className="btn" onClick={() => setSecret(null)}>{t('dev.saved')}</button></p>
          </Card>
        ) : null}
        {tab === 'keys' ? <Keys onSecret={setSecret} locked={!!secret} /> : null}
        {tab === 'webhooks' ? <Webhooks onSecret={setSecret} locked={!!secret} /> : null}
        {tab === 'deliveries' ? <Deliveries /> : null}
        {tab === 'requests' ? <Requests /> : null}
        {tab === 'health' ? <HealthView /> : null}
        {tab === 'tools' ? <Tools /> : null}
      </div>
    </>
  );
}

function useAction(reload: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

function Keys({ onSecret, locked }: { onSecret: (s: string) => void; locked: boolean }) {
  const { t } = useSession();
  const when = useWhen();
  const keys = useApi<{ items: Key[]; scopes: string[] }>('/developers/keys');
  const { busy, error, run } = useAction(keys.reload);
  return (
    <>
      <Card title={t('dev.key.create')} hint={t('dev.key.scopesHint')}>
        {error || keys.error ? <ErrorState error={error || keys.error} /> : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const data = new FormData(e.currentTarget);
            const days = Number(data.get('days'));
            void run(async () => {
              const result = await api.post<{ key: string }>('/developers/keys', {
                name: data.get('name'),
                scopes: data.getAll('scopes'),
                rate_limit: Number(data.get('rate')),
                expires_in_days: days > 0 ? days : undefined,
              });
              onSecret(result.key);
            });
          }}
        >
          <label className="field">{t('dev.key.name')}<input className="input" name="name" required maxLength={100} /></label>
          <label className="field">{t('dev.key.rate')}<input className="input" name="rate" type="number" min={1} max={600} defaultValue={60} required /></label>
          <label className="field">{t('dev.key.expiry')}<input className="input" name="days" type="number" min={1} max={730} /></label>
          <fieldset><legend>{t('dev.key.scopes')}</legend>{keys.data?.scopes.map((s) => <label key={s} style={{ display: 'block' }}><input type="checkbox" name="scopes" value={s} /> <code>{s}</code></label>)}</fieldset>
          <button className="btn btn--primary" disabled={busy || locked}>{t('dev.key.create')}</button>
        </form>
      </Card>
      <Card padded={false}>
        {keys.data && keys.data.items.length === 0 ? <EmptyState title={t('dev.key.empty')} /> : (
          <table className="table">
            <thead><tr><th>{t('dev.key.name')}</th><th>{t('dev.key.scopes')}</th><th>{t('dev.key.lastUsed')}</th><th>{t('dev.key.expires')}</th><th /></tr></thead>
            <tbody>
              {keys.data?.items.map((k) => (
                <tr key={k.id}>
                  <td>{k.name} <Chip label={t(`dev.key.state.${k.state}`)} tone={STATE_TONE[k.state]} />{k.connection_id ? <div className="table__sub">{t('dev.key.website')}</div> : null}</td>
                  <td><code>{k.scopes.join(', ')}</code><div className="table__sub">{k.rate_limit}/min</div></td>
                  <td>{k.last_used_at ? when(k.last_used_at) : t('dev.key.never')}</td>
                  <td>{k.expires_at ? when(k.expires_at) : t('dev.key.noExpiry')}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {k.state === 'ACTIVE' ? (
                      <>
                        <button className="btn btn--sm" disabled={busy || locked} onClick={() => window.confirm(t('dev.key.rotateConfirm')) && void run(async () => onSecret((await api.post<{ key: string }>(`/developers/keys/${k.id}/rotate`)).key))}>{t('dev.key.rotate')}</button>{' '}
                        <button className="btn btn--sm" disabled={busy} onClick={() => window.confirm(t('dev.key.revokeConfirm')) && void run(() => api.delete(`/developers/keys/${k.id}`))}>{t('dev.key.revoke')}</button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function Webhooks({ onSecret, locked }: { onSecret: (s: string) => void; locked: boolean }) {
  const { t } = useSession();
  const hooks = useApi<{ items: Hook[]; topics: string[] }>('/developers/webhooks');
  const { busy, error, run } = useAction(hooks.reload);
  const [tested, setTested] = useState<string | null>(null);
  return (
    <>
      <Card title={t('dev.hook.create')}>
        {error || hooks.error ? <ErrorState error={error || hooks.error} /> : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const data = new FormData(e.currentTarget);
            void run(async () => onSecret((await api.post<{ signing_secret: string }>('/developers/webhooks', { url: data.get('url'), topics: data.getAll('topics') })).signing_secret));
          }}
        >
          <label className="field">{t('dev.hook.url')}<input className="input" name="url" type="url" required maxLength={1000} placeholder="https://example.com/webhooks/ecomsbd" /></label>
          <fieldset><legend>{t('dev.hook.events')}</legend>{hooks.data?.topics.map((s) => <label key={s} style={{ display: 'block' }}><input type="checkbox" name="topics" value={s} /> <code>{s}</code></label>)}</fieldset>
          <button className="btn btn--primary" disabled={busy || locked}>{t('dev.hook.create')}</button>
        </form>
      </Card>
      {hooks.data && hooks.data.items.length === 0 ? <Card><EmptyState title={t('dev.hook.empty')} /></Card> : null}
      {hooks.data?.items.map((h) => (
        <Card key={h.id} title={h.url} actions={<Chip label={h.enabled ? t('dev.health.state.ACTIVE') : t('dev.health.state.DISABLED')} tone={h.enabled ? 'good' : 'neutral'} />}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              void run(() => api.patch(`/developers/webhooks/${h.id}`, { topics: data.getAll('topics') }));
            }}
          >
            <fieldset><legend>{t('dev.hook.events')}</legend>{hooks.data?.topics.map((s) => <label key={s} style={{ display: 'inline-block', marginRight: 12 }}><input type="checkbox" name="topics" value={s} defaultChecked={h.topics.includes(s)} /> <code>{s}</code></label>)}</fieldset>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              <button className="btn btn--sm btn--primary" disabled={busy}>{t('dev.hook.saveEvents')}</button>
              <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(() => api.patch(`/developers/webhooks/${h.id}`, { enabled: !h.enabled }))}>{h.enabled ? t('dev.hook.disable') : t('dev.hook.enable')}</button>
              <button type="button" className="btn btn--sm" disabled={busy || locked} onClick={() => window.confirm(t('dev.hook.rotateConfirm')) && void run(async () => onSecret((await api.post<{ signing_secret: string }>(`/developers/webhooks/${h.id}/rotate-secret`)).signing_secret))}>{t('dev.hook.rotate')}</button>
              <button type="button" className="btn btn--sm" disabled={busy || !h.enabled} onClick={() => void run(async () => { await api.post(`/developers/webhooks/${h.id}/test`); setTested(h.id); })}>{t('dev.hook.test')}</button>
            </div>
            {tested === h.id ? <p className="card__hint">{t('dev.hook.testDone')}</p> : null}
          </form>
        </Card>
      ))}
    </>
  );
}

function Deliveries() {
  const { t } = useSession();
  const when = useWhen();
  const [status, setStatus] = useState('');
  const deliveries = useApi<{ items: Delivery[] }>('/developers/deliveries', { status: status || undefined });
  const [selected, setSelected] = useState<string | null>(null);
  const attempts = useApi<{ items: { id: string; created_at: string; status_code: number | null; error: string | null }[] }>(selected ? `/developers/deliveries/${selected}/attempts` : null);
  const { busy, error, run } = useAction(deliveries.reload);
  return (
    <Card padded={false}>
      <div className="card__body" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <label>{t('dev.del.filter')} <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t('dev.del.all')}</option>
          {['FAILED', 'RETRY', 'PENDING', 'DELIVERED', 'DISABLED'].map((s) => <option key={s} value={s}>{t(`dev.del.status.${s}` as StringKey)}</option>)}
        </select></label>
      </div>
      {error || deliveries.error ? <ErrorState error={error || deliveries.error} /> : null}
      {deliveries.data && deliveries.data.items.length === 0 ? <EmptyState title={t('dev.del.empty')} /> : (
        <table className="table">
          <tbody>
            {deliveries.data?.items.map((d) => (
              <tr key={d.id}>
                <td><code>{d.topic}</code><div className="table__sub">{when(d.created_at)}</div></td>
                <td><Chip label={t(`dev.del.status.${d.status}` as StringKey)} tone={STATE_TONE[d.status] ?? 'neutral'} /></td>
                <td>{d.attempts} · HTTP {d.last_status ?? '—'}{d.last_error ? ` · ${d.last_error}` : ''}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn btn--sm" onClick={() => setSelected(d.id)}>{t('dev.del.attempts')}</button>{' '}
                  {['FAILED', 'DISABLED'].includes(d.status) ? <button className="btn btn--sm" disabled={busy} onClick={() => void run(() => api.post(`/developers/deliveries/${d.id}/retry`))}>{t('dev.del.retry')}</button> : null}
                  {selected === d.id ? attempts.data?.items.map((a) => <div key={a.id} className="table__sub">{when(a.created_at)}: {a.status_code ?? a.error}</div>) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Requests() {
  const { t } = useSession();
  const when = useWhen();
  const requests = useApi<{ items: { id: string; endpoint: string; idempotency_key: string; created_at: string }[] }>('/developers/requests');
  return (
    <Card hint={t('dev.req.hint')} title={t('dev.tab.requests')} padded={false}>
      {requests.error ? <ErrorState error={requests.error} /> : null}
      {requests.data && requests.data.items.length === 0 ? <EmptyState title={t('dev.req.empty')} /> : (
        <table className="table">
          <thead><tr><th>{t('dev.req.endpoint')}</th><th>{t('dev.req.key')}</th><th>{t('dev.req.when')}</th></tr></thead>
          <tbody>{requests.data?.items.map((r) => <tr key={r.id}><td><code>{r.endpoint}</code></td><td><code>{r.idempotency_key}</code></td><td>{when(r.created_at)}</td></tr>)}</tbody>
        </table>
      )}
    </Card>
  );
}

function HealthView() {
  const { t } = useSession();
  const when = useWhen();
  const health = useApi<Health>('/developers/health');
  if (health.error) return <ErrorState error={health.error} onRetry={health.reload} />;
  const data = health.data;
  if (!data) return <p className="card__hint">{t('common.loading')}</p>;
  return (
    <>
      <Card title={t('dev.health.api')}>
        <Row label={t('dev.health.api')} value={<Chip label={t(`dev.health.api.${data.api.state}`, { n: data.api.active_keys })} tone={data.api.state === 'ACTIVE' ? 'good' : 'warn'} />} />
        <Row label={t('dev.health.lastRequest')} value={when(data.api.last_request_at)} />
        {data.api.expiring_soon.length ? <p className="card__hint">{t('dev.health.expiring', { n: data.api.expiring_soon.length })}</p> : null}
      </Card>
      <Card title={t('dev.health.webhooks')}>
        {data.webhooks.length === 0 ? <p className="card__hint">{t('dev.health.none')}</p> : null}
        {data.webhooks.map((w) => (
          <div key={w.id} style={{ borderTop: '1px solid var(--stroke)', paddingTop: 10, marginTop: 10 }}>
            <p><code style={{ overflowWrap: 'anywhere' }}>{w.url}</code> <Chip label={t(`dev.health.state.${w.state}`)} tone={STATE_TONE[w.state]} /></p>
            {w.failed_24h ? <p className="text--bad">{t('dev.health.failed24h', { n: w.failed_24h })}</p> : null}
            <Row label={t('dev.health.latest')} value={w.latest_delivery ? `${w.latest_delivery.topic} · ${t(`dev.del.status.${w.latest_delivery.status}` as StringKey)} · ${when(w.latest_delivery.created_at)}` : '—'} />
            <Row label={t('dev.health.latestError')} value={w.latest_error ? `HTTP ${w.latest_error.last_status ?? '—'} ${w.latest_error.last_error ?? ''} · ${when(w.latest_error.at)}` : '—'} />
          </div>
        ))}
      </Card>
      <Card title={t('dev.health.connections')} hint={t('dev.health.connectionsHint')}>
        {data.connections.length === 0 ? <p className="card__hint">{t('dev.health.none')}</p> : null}
        {data.connections.map((c) => <Row key={c.id} label={c.name} value={`${c.health} · ${c.state} · ${when(c.last_success_at)}`} />)}
      </Card>
    </>
  );
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signed = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(signed), (b) => b.toString(16).padStart(2, '0')).join('');
}

function Tools() {
  const { t } = useSession();
  const samples = useApi<Samples>('/developers/samples');
  const [keyText, setKeyText] = useState('');
  const [check, setCheck] = useState<{ valid: boolean; state: string; name?: string; scopes?: string[] } | null>(null);
  const [topic, setTopic] = useState('order.delivered');
  const [secret, setSecret] = useState('');
  const [body, setBody] = useState('');
  const [header, setHeader] = useState('');
  const [useClock, setUseClock] = useState(true);
  const [verdict, setVerdict] = useState<'ok' | 'bad' | 'old' | null>(null);
  const { busy, error, run } = useAction(() => undefined);

  async function verify() {
    const match = /^t=(\d+),v1=([0-9a-f]{64})$/.exec(header.trim());
    if (!match) return setVerdict('bad');
    const expected = await hmacHex(secret, `${match[1]}.${body}`);
    if (expected !== match[2]) return setVerdict('bad');
    const age = Math.abs(Math.floor(Date.now() / 1000) - Number(match[1]));
    setVerdict(useClock && age > 300 ? 'old' : 'ok');
  }

  return (
    <>
      <Card title={t('dev.tools.check')} hint={t('dev.tools.checkHint')}>
        {error ? <ErrorState error={error} /> : null}
        <form onSubmit={(e) => { e.preventDefault(); void run(async () => { setCheck(await api.post('/developers/keys/check', { key: keyText })); setKeyText(''); }); }}>
          <input className="input" type="password" autoComplete="off" value={keyText} onChange={(e) => setKeyText(e.target.value)} placeholder="ec_live_…" />
          <button className="btn" disabled={busy || keyText.length < 10} style={{ marginTop: 8 }}>{t('dev.tools.checkRun')}</button>
        </form>
        {check ? <p>{check.valid ? t('dev.tools.valid', { name: check.name ?? '', scopes: (check.scopes ?? []).join(', ') }) : t('dev.tools.invalid', { state: check.state })}</p> : null}
      </Card>
      <Card title={t('dev.tools.samples')} hint={t('dev.tools.samplesHint')}>
        {samples.error ? <ErrorState error={samples.error} /> : null}
        <h3 className="card__title">{t('dev.tools.sampleOrder')} — <code>POST /public/v1/sources/&#123;source_id&#125;/orders</code></h3>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{samples.data ? JSON.stringify(samples.data.order, null, 2) : '…'}</pre>
        <h3 className="card__title">{t('dev.tools.sampleEvent')}</h3>
        <select className="select" value={topic} onChange={(e) => setTopic(e.target.value)}>
          {Object.keys(samples.data?.events ?? {}).map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{samples.data ? JSON.stringify(samples.data.events[topic], null, 2) : '…'}</pre>
        <p className="card__hint">{t('dev.tools.docs')}</p>
      </Card>
      <Card title={t('dev.tools.verify')} hint={t('dev.tools.verifyHint')}>
        <label className="field">{t('dev.tools.secret')}<input className="input" type="password" autoComplete="off" value={secret} onChange={(e) => { setSecret(e.target.value); setVerdict(null); }} /></label>
        <label className="field">{t('dev.tools.body')}<textarea className="input" rows={4} value={body} onChange={(e) => { setBody(e.target.value); setVerdict(null); }} /></label>
        <label className="field">{t('dev.tools.header')}<input className="input" value={header} onChange={(e) => { setHeader(e.target.value); setVerdict(null); }} placeholder="t=…,v1=…" /></label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}><input type="checkbox" checked={useClock} onChange={(e) => setUseClock(e.target.checked)} /> {t('dev.tools.now')}</label>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button className="btn btn--primary" onClick={() => void verify()}>{t('dev.tools.verifyRun')}</button>
          <button className="btn" disabled={!samples.data} onClick={() => { const ex = samples.data!.signature_example; setSecret(ex.secret); setBody(ex.body); setHeader(ex.header); setUseClock(false); setVerdict(null); }}>{t('dev.tools.useExample')}</button>
        </div>
        {verdict ? <p className={verdict === 'ok' ? undefined : 'text--bad'}>{t(verdict === 'ok' ? 'dev.tools.verifyOk' : verdict === 'old' ? 'dev.tools.verifyOld' : 'dev.tools.verifyBad')}</p> : null}
      </Card>
    </>
  );
}
