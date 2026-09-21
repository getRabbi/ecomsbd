'use client';

import { useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Source = { id: string; name: string; provider: string; enabled: boolean; available: boolean; mapping: Record<string, string> };
export default function OrderSourcesPage() {
  const { locale } = useSession();
  const tx = (en: string, bn: string) => locale === 'bn' ? bn : en;
  const sources = useApi<{ items: Source[]; capabilities: { provider: string; available: boolean }[]; mapping_fields: string[] }>('/order-sources');
  const [selected, setSelected] = useState<string | null>(null);
  const history = useApi<{ items: { order_id: string; external_order_id: string; created_at: string }[] }>(selected ? `/order-sources/${selected}/history` : null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>) { setBusy(true); setError(null); try { await action(); sources.reload(); history.reload(); } catch(e) { setError(e); } finally { setBusy(false); } }
  return <><PageHeader title={tx('Order sources', 'অর্ডারের উৎস')} subtitle={tx('External orders become drafts through the same order service.', 'বাইরের অর্ডার একই নিয়মে খসড়া অর্ডার হিসেবে আসবে।')} />
    <div className="content">
      {error || sources.error ? <ErrorState error={error || sources.error} /> : null}
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Available connectors', 'সংযোগের অবস্থা')}</h2>
        {sources.data?.capabilities.map(c => <p key={c.provider}>{c.provider}: {c.available ? tx('Available', 'উপলভ্য') : tx('Official contract required', 'অফিশিয়াল চুক্তি প্রয়োজন')}</p>)}
        <form onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); void run(async () => { await api.post('/order-sources', { name: data.get('name'), provider: 'CUSTOM_PUSH', enabled: true, mapping: JSON.parse(String(data.get('mapping') || '{}')) }); form.reset(); }); }}>
          <label className="field">{tx('Source name', 'উৎসের নাম')}<input className="input" name="name" required maxLength={120} /></label>
          <label className="field">{tx('Column mapping (optional JSON)', 'কলাম ম্যাপিং (ঐচ্ছিক JSON)')}<textarea className="input" name="mapping" defaultValue="{}" /></label>
          <p>{tx('Leave {} for native order JSON. For flat rows map phone, product and amount (taka), for example:', 'অর্ডারের নিজস্ব JSON হলে {} রাখুন। সাধারণ সারির জন্য phone, product ও amount (টাকা) ম্যাপ করুন, যেমন:')}</p>
          <pre>{'{"phone":"Phone","product":"Item","amount":"Total"}'}</pre>
          <button className="btn btn--primary" disabled={busy}>{tx('Create custom source', 'নিজস্ব উৎস তৈরি')}</button>
        </form>
      </section>
      {sources.data?.items.map(s => <section className="card" key={s.id} style={{ padding: 20 }}>
        <h3>{s.name}</h3><p>{s.provider} · {s.enabled ? tx('Enabled', 'চালু') : tx('Disabled', 'বন্ধ')}</p><p>{tx('Source ID', 'উৎসের আইডি')}: <code>{s.id}</code></p>
        <button className="btn" disabled={busy || !s.available} onClick={() => void run(() => api.patch(`/order-sources/${s.id}`, { enabled: !s.enabled }))}>{s.enabled ? tx('Disable', 'বন্ধ করুন') : tx('Enable', 'চালু করুন')}</button>
        <button className="btn" onClick={() => setSelected(s.id)}>{tx('History / submit order', 'ইতিহাস / অর্ডার পাঠান')}</button>
      </section>)}
      {selected ? <section className="card" style={{ padding: 20 }}>
        <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void run(() => api.post(`/order-sources/${selected}/ingest`, { external_order_id: data.get('id'), payload: JSON.parse(String(data.get('payload'))) })); }}>
          <label className="field">{tx('External order ID', 'বাইরের অর্ডারের আইডি')}<input className="input" name="id" required maxLength={200} /></label>
          <label className="field">{tx('Order payload (JSON)', 'অর্ডারের তথ্য (JSON)')}<textarea className="input" name="payload" required rows={6} /></label>
          <p>{tx('Retries must keep the same external ID and payload. Existing orders are never overwritten.', 'আবার পাঠালে একই আইডি ও তথ্য রাখুন। আগের অর্ডার বদলানো হবে না।')}</p>
          <button className="btn" disabled={busy}>{tx('Submit draft order', 'খসড়া অর্ডার পাঠান')}</button>
        </form>
        {history.error ? <ErrorState error={history.error} /> : null}
        {history.data?.items.map(h => <p key={h.external_order_id}>{h.external_order_id} → {h.order_id}</p>)}
      </section> : null}
    </div>
  </>;
}
