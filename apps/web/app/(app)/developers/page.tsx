'use client';

import { useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Key = { id: string; name: string; scopes: string[]; revoked_at: string | null; rate_limit: number };
type Hook = { id: string; url: string; topics: string[]; enabled: boolean };
type Delivery = { id: string; topic: string; status: string; attempts: number; last_status: number | null; last_error: string | null };
export default function DevelopersPage() {
  const { locale } = useSession();
  const tx = (en: string, bn: string) => locale === 'bn' ? bn : en;
  const keys = useApi<{ items: Key[]; scopes: string[] }>('/developers/keys');
  const hooks = useApi<{ items: Hook[]; topics: string[] }>('/developers/webhooks');
  const deliveries = useApi<{ items: Delivery[] }>('/developers/deliveries');
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const attempts = useApi<{ items: { id: string; created_at: string; status_code: number | null; error: string | null }[] }>(selected ? `/developers/deliveries/${selected}/attempts` : null);
  async function run(action: () => Promise<unknown>) { setBusy(true); setError(null); try { await action(); keys.reload(); hooks.reload(); deliveries.reload(); attempts.reload(); } catch (e) { setError(e); } finally { setBusy(false); } }
  return <><PageHeader title={tx('Developer settings', 'ডেভেলপার সেটিংস')} subtitle={tx('Shop API access and signed event delivery.', 'শপের API অ্যাক্সেস ও স্বাক্ষরযুক্ত ইভেন্ট পাঠানো।')} />
    <div className="content">
      {error || keys.error || hooks.error ? <ErrorState error={error || keys.error || hooks.error} /> : null}
      {secret ? <section className="card" style={{ padding: 20 }} role="status"><h2>{tx('Copy now — shown only once', 'এখনই কপি করুন — একবারই দেখানো হবে')}</h2><code style={{ overflowWrap: 'anywhere' }}>{secret}</code><p><button className="btn" onClick={() => setSecret(null)}>{tx('I have saved it', 'সংরক্ষণ করেছি')}</button></p></section> : null}
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('API keys', 'API কী')}</h2>
        <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void run(async () => { const result = await api.post<{ key: string }>('/developers/keys', { name: data.get('name'), scopes: data.getAll('scopes'), rate_limit: Number(data.get('rate')) }); setSecret(result.key); }); }}>
          <label className="field">{tx('Name', 'নাম')}<input className="input" name="name" required maxLength={100} /></label>
          <label className="field">{tx('Requests per minute', 'প্রতি মিনিটে অনুরোধ')}<input className="input" name="rate" type="number" min={1} max={600} defaultValue={60} required /></label>
          <fieldset><legend>{tx('Scopes', 'অনুমতি')}</legend>{keys.data?.scopes.map(s => <label key={s} style={{ display: 'block' }}><input type="checkbox" name="scopes" value={s} /> {s}</label>)}</fieldset>
          <button className="btn btn--primary" disabled={busy || !!secret}>{tx('Create key', 'কী তৈরি')}</button>
        </form>
        {keys.data?.items.map(k => <article key={k.id}><h3>{k.name}</h3><p>{k.scopes.join(', ')} · {k.rate_limit}/min</p>{k.revoked_at ? <p>{tx('Revoked', 'বাতিল')}</p> : <button className="btn" disabled={busy} onClick={() => void run(() => api.delete(`/developers/keys/${k.id}`))}>{tx('Revoke key', 'কী বাতিল')}</button>}</article>)}
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Webhooks', 'ওয়েবহুক')}</h2>
        <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void run(async () => { const result = await api.post<{ signing_secret: string }>('/developers/webhooks', { url: data.get('url'), topics: data.getAll('topics') }); setSecret(result.signing_secret); }); }}>
          <label className="field">{tx('Public HTTPS endpoint', 'পাবলিক HTTPS ঠিকানা')}<input className="input" name="url" type="url" required maxLength={1000} placeholder="https://example.com/webhooks" /></label>
          <fieldset><legend>{tx('Events', 'ইভেন্ট')}</legend>{hooks.data?.topics.map(t => <label key={t} style={{ display: 'block' }}><input type="checkbox" name="topics" value={t} /> {t}</label>)}</fieldset>
          <button className="btn btn--primary" disabled={busy || !!secret}>{tx('Create webhook', 'ওয়েবহুক তৈরি')}</button>
        </form>
        {hooks.data?.items.map(h => <article key={h.id}><h3 style={{ overflowWrap: 'anywhere' }}>{h.url}</h3><p>{h.topics.join(', ')}</p><button className="btn" disabled={busy} onClick={() => void run(() => api.patch(`/developers/webhooks/${h.id}`, { enabled: !h.enabled }))}>{h.enabled ? tx('Disable', 'বন্ধ করুন') : tx('Enable', 'চালু করুন')}</button><button className="btn" disabled={busy || !h.enabled} onClick={() => void run(() => api.post(`/developers/webhooks/${h.id}/test`))}>{tx('Send test event', 'পরীক্ষামূলক ইভেন্ট পাঠান')}</button></article>)}
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Delivery history', 'পাঠানোর ইতিহাস')}</h2><button className="btn" onClick={deliveries.reload}>{tx('Refresh', 'হালনাগাদ')}</button>
        {deliveries.error ? <ErrorState error={deliveries.error} /> : null}
        {deliveries.data?.items.map(d => <article key={d.id}><p>{d.topic} · {d.status} · {d.attempts} · HTTP {d.last_status ?? '—'}</p><button className="btn" onClick={() => setSelected(d.id)}>{tx('Attempts', 'প্রচেষ্টা')}</button>{['FAILED', 'DISABLED'].includes(d.status) ? <button className="btn" disabled={busy} onClick={() => void run(() => api.post(`/developers/deliveries/${d.id}/retry`))}>{tx('Retry', 'আবার চেষ্টা')}</button> : null}</article>)}
        {attempts.data?.items.map(a => <p key={a.id}>{a.created_at}: {a.status_code ?? a.error}</p>)}
      </section>
      <details className="card" style={{ padding: 20 }}><summary>{tx('Integration contract', 'সংযোগের নিয়ম')}</summary>
        <p>{tx('Use Authorization: Bearer with your API key. POST requests require Idempotency-Key. Reuse it only for the same body.', 'API কী দিয়ে Authorization: Bearer ব্যবহার করুন। POST অনুরোধে Idempotency-Key দরকার। একই তথ্যের জন্য একই কী ব্যবহার করুন।')}</p>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{'GET/POST /public/v1/orders\nGET/POST /public/v1/customers\nGET/POST /public/v1/products\nGET /public/v1/inventory/{product_id}\nPOST /public/v1/inventory/{product_id}/adjustments\nPOST /public/v1/sources/{source_id}/orders'}</pre>
        <p>{tx('Verify X-Ecomsbd-Signature: t=timestamp,v1=hex HMAC-SHA256(secret, timestamp + "." + raw body). Reject timestamps older than 5 minutes and deduplicate X-Ecomsbd-Event-Id. Return 2xx after durable acceptance.', 'X-Ecomsbd-Signature যাচাই করুন: t=timestamp,v1=hex HMAC-SHA256(secret, timestamp + "." + raw body)। ৫ মিনিটের বেশি পুরোনো সময় বাতিল করুন এবং X-Ecomsbd-Event-Id দিয়ে ডুপ্লিকেট ঠেকান। স্থায়ীভাবে গ্রহণের পর 2xx দিন।')}</p>
      </details>
    </div>
  </>;
}
