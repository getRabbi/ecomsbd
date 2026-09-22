'use client';

import { useRef, useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Contact = { id: string; customer_id: string; channel: string; recipient_masked: string; consent: boolean };
type Template = { key: string; subject_en: string; subject_bn: string; body_en: string; body_bn: string };
type Message = { id: string; order_id: string; subject: string; body: string; status: string; attempts: number; last_error: string | null };

export default function MessagingPage() {
  const { locale } = useSession();
  const bn = locale === 'bn';
  const tx = (en: string, bangla: string) => bn ? bangla : en;
  const channels = useApi<{ items: { kind: string; available: boolean; enabled: boolean; blocker: string | null }[] }>('/messaging/channels');
  const customers = useApi<{ items: { id: string; name: string | null; phone_masked: string }[] }>('/customers');
  const contacts = useApi<{ items: Contact[] }>('/messaging/conversations');
  const templates = useApi<{ items: Template[] }>('/messaging/templates');
  const [contact, setContact] = useState<Contact | null>(null);
  const [customer, setCustomer] = useState('');
  const [template, setTemplate] = useState('order_update');
  const [order, setOrder] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const orders = useApi<{ items: { id: string; order_number: string }[] }>(contact ? '/orders' : null, { customer_id: contact?.customer_id });
  const messages = useApi<{ items: Message[] }>(contact ? '/messaging/messages' : null, { conversation_id: contact?.id });
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); contacts.reload(); channels.reload(); messages.reload(); templates.reload(); }
    catch (caught) { setError(caught); } finally { setBusy(false); }
  }
  const selectedTemplate = templates.data?.items.find(t => t.key === template);
  return <>
    <PageHeader title={tx('Customer messaging', 'গ্রাহককে মেসেজ')} subtitle={tx('Transactional order updates with recorded consent.', 'সম্মতিসহ অর্ডার সম্পর্কে মেসেজ পাঠান।')} />
    <div className="page-content">
      {error || channels.error || contacts.error ? <ErrorState error={error || channels.error || contacts.error} /> : null}
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Channels', 'মাধ্যম')}</h2>
        {channels.data?.items.map(c => <p key={c.kind}>{c.kind} · {c.available ? tx('Available', 'উপলভ্য') : tx('Official provider required', 'অফিশিয়াল প্রোভাইডার প্রয়োজন')} <button className="btn" disabled={busy || (!c.available && !c.enabled)} onClick={() => void run(() => api.post(`/messaging/channels/${c.kind}`, { enabled: !c.enabled }))}>{c.enabled ? tx('Disable', 'বন্ধ করুন') : tx('Enable', 'চালু করুন')}</button></p>)}
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Record contact and consent', 'যোগাযোগ ও সম্মতি সংরক্ষণ')}</h2>
        <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void run(async () => { const saved = await api.post<Contact>('/messaging/conversations', { customer_id: customer, recipient: data.get('recipient'), consent: data.get('consent') === 'on', evidence: data.get('evidence') }); setContact(saved); }); }}>
          <label className="field">{tx('Customer', 'গ্রাহক')}<select className="select" required value={customer} onChange={e => setCustomer(e.target.value)}><option value="">{tx('Select customer', 'গ্রাহক বাছুন')}</option>{customers.data?.items.map(c => <option key={c.id} value={c.id}>{c.name || c.phone_masked}</option>)}</select></label>
          <label className="field">{tx('Email', 'ইমেইল')}<input className="input" name="recipient" type="email" required maxLength={254} /></label>
          <label className="field">{tx('Consent evidence / reason', 'সম্মতির প্রমাণ / কারণ')}<input className="input" name="evidence" minLength={3} maxLength={500} required /></label>
          <label><input name="consent" type="checkbox" /> {tx('Customer agreed to transactional messages', 'গ্রাহক অর্ডারের মেসেজ পেতে সম্মত')}</label>
          <button className="btn btn--primary" disabled={busy}>{tx('Save', 'সংরক্ষণ')}</button>
        </form>
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Conversations', 'কথোপকথন')}</h2>
        {contacts.data?.items.map(c => <button className="btn" key={c.id} onClick={() => { setContact(c); setOrder(''); key.current = null; }}>{c.recipient_masked} · {c.consent ? tx('Consented', 'সম্মতি আছে') : tx('Opted out', 'সম্মতি নেই')}</button>)}
        {contact ? <>
          <h3>{contact.recipient_masked}</h3>
          <form onSubmit={e => { e.preventDefault(); const evidence = new FormData(e.currentTarget).get('evidence'); void run(async () => setContact(await api.patch<Contact>(`/messaging/conversations/${contact.id}/consent`, { consent: !contact.consent, evidence }))); }}>
            <label className="field">{tx('Consent change reason', 'সম্মতি পরিবর্তনের কারণ')}<input className="input" name="evidence" required minLength={3} maxLength={500} /></label>
            <button className="btn" disabled={busy}>{contact.consent ? tx('Record opt-out', 'মেসেজ বন্ধের অনুরোধ সংরক্ষণ') : tx('Record consent', 'সম্মতি সংরক্ষণ')}</button>
          </form>
          <label className="field">{tx('Order', 'অর্ডার')}<select className="select" value={order} onChange={e => { setOrder(e.target.value); key.current = null; }}><option value="">{tx('Select order', 'অর্ডার বাছুন')}</option>{orders.data?.items.map(o => <option key={o.id} value={o.id}>{o.order_number}</option>)}</select></label>
          <label className="field">{tx('Template', 'টেমপ্লেট')}<select className="select" value={template} onChange={e => { setTemplate(e.target.value); key.current = null; }}>{templates.data?.items.map(t => <option key={t.key}>{t.key}</option>)}</select></label>
          <p>{selectedTemplate?.[bn ? 'body_bn' : 'body_en']}</p>
          <button className="btn btn--primary" disabled={busy || !order || !contact.consent || !channels.data?.items.some(c => c.kind === contact.channel && c.available && c.enabled)} onClick={() => void run(async () => { key.current ??= crypto.randomUUID(); await api.post('/messaging/messages', { conversation_id: contact.id, order_id: order, template_key: template, locale, idempotency_key: key.current }); key.current = null; })}>{tx('Send transactional message', 'অর্ডারের মেসেজ পাঠান')}</button>
          <button className="btn" onClick={messages.reload}>{tx('Refresh history', 'ইতিহাস হালনাগাদ')}</button>
          {messages.error ? <ErrorState error={messages.error} /> : null}
          {messages.data?.items.map(m => <article key={m.id}><h4>{m.subject}</h4><p>{m.body}</p><p>{tx('Status / attempts', 'অবস্থা / প্রচেষ্টা')}: {m.status} / {m.attempts}</p>{m.status === 'FAILED' ? <button className="btn" disabled={busy} onClick={() => void run(() => api.post(`/messaging/messages/${m.id}/retry`))}>{tx('Retry', 'আবার চেষ্টা')}</button> : null}</article>)}
        </> : null}
      </section>
      <details className="card" style={{ padding: 20 }}><summary>{tx('Create transactional template', 'অর্ডারের টেমপ্লেট তৈরি')}</summary>
        <p>{tx('Use {customer_name} and {order_number}. Supply both languages.', '{customer_name} ও {order_number} ব্যবহার করুন। দুই ভাষায় লিখুন।')}</p>
        <form onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const payload = Object.fromEntries(new FormData(form)); void run(async () => { await api.post('/messaging/templates', payload); form.reset(); }); }}>
          {(['key', 'subject_en', 'subject_bn', 'body_en', 'body_bn'] as const).map((name, i) => <label className="field" key={name}>{[tx('Template key', 'টেমপ্লেট কী'), tx('English subject', 'ইংরেজি বিষয়'), tx('Bangla subject', 'বাংলা বিষয়'), tx('English message', 'ইংরেজি মেসেজ'), tx('Bangla message', 'বাংলা মেসেজ')][i]}<textarea className="input" name={name} required maxLength={name === 'key' ? 80 : name.startsWith('subject') ? 160 : 2000} /></label>)}
          <button className="btn" disabled={busy}>{tx('Save template', 'টেমপ্লেট সংরক্ষণ')}</button>
        </form>
      </details>
    </div>
  </>;
}
