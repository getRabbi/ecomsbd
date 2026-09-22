'use client';
import { useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function NetworkPage() {
  const { locale } = useSession();
  const bn = locale === 'bn';
  const result = useApi<{ status: string; opted_in: boolean; period: string | null; message_en: string; message_bn: string; facts: { rto_percent_rounded?: number; delivery_percent_rounded?: number } }>('/network-intelligence');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function toggle() { setBusy(true); setError(null); try { await api.patch('/network-intelligence/preference', { opted_in: !result.data?.opted_in }); result.reload(); } catch(e) { setError(e); } finally { setBusy(false); } }
  return <><PageHeader title={bn ? 'বেনামি নেটওয়ার্ক বেঞ্চমার্ক' : 'Anonymous network benchmarks'} /><div className="content"><section className="card" style={{ padding: 20 }}>
    {error || result.error ? <ErrorState error={error || result.error} /> : null}
    <p>{result.data?.[bn ? 'message_bn' : 'message_en']}</p>
    <p>{bn ? 'শুধু পুরো মাসের সমষ্টিগত তথ্য। কোনো শপ, গ্রাহক বা ফোন খোঁজা যায় না। সম্মতি প্রত্যাহার করলে ভবিষ্যতের হিসাবে অংশ নেবে না; প্রকাশিত বেনামি ফল থাকবে।' : 'Only closed monthly aggregates. No shop, customer, or phone lookup. Opting out stops future contributions; published anonymous results remain.'}</p>
    {result.data?.status === 'COMPLETE' ? <><h2>{result.data.period} (UTC)</h2><p>RTO: {result.data.facts.rto_percent_rounded}%</p><p>{bn ? 'ডেলিভারি' : 'Delivery'}: {result.data.facts.delivery_percent_rounded}%</p></> : <p>{bn ? 'যথেষ্ট নিরাপদ নমুনা না পাওয়া পর্যন্ত বেঞ্চমার্ক বন্ধ আছে।' : 'Benchmarks are gated until a sufficient privacy-safe sample is available.'}</p>}
    <button className="btn" disabled={busy || !result.data} onClick={() => void toggle()}>{result.data?.opted_in ? (bn ? 'অংশগ্রহণ বন্ধ করুন' : 'Opt out') : (bn ? 'বেনামি সমষ্টিতে অংশ নিতে সম্মতি দিন' : 'Consent to anonymous aggregation')}</button>
  </section></div></>;
}
