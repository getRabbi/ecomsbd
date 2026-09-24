'use client';

import { useRef, useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Contact = {
  id: string; customer_id: string; channel: 'EMAIL' | 'WHATSAPP'; recipient_masked: string; consent: boolean;
  marketing_consent: boolean; marketing_opted_out_at: string | null; undeliverable_reason: string | null;
};
type Template = {
  key: string; purpose: 'TRANSACTIONAL' | 'MARKETING'; channel: 'EMAIL' | 'WHATSAPP';
  subject_en: string; subject_bn: string; body_en: string; body_bn: string;
  provider_status: string | null; builtin: boolean; archived: boolean;
};
type Message = { id: string; order_id: string | null; purpose: string; subject: string; body: string; status: string; attempts: number; last_error: string | null };
type ChannelRow = { kind: string; available: boolean; enabled: boolean; blocker: string | null; shop_blocker: string | null; reports: string[]; contact: boolean };
type ConsentRow = { id: string; scope: string; consent: boolean; source: string; evidence: string; created_at: string };

export default function MessagingPage() {
  const { locale } = useSession();
  const bn = locale === 'bn';
  const tx = (en: string, bangla: string) => bn ? bangla : en;
  const channels = useApi<{ items: ChannelRow[] }>('/messaging/channels');
  const customers = useApi<{ items: { id: string; name: string | null; phone_masked: string }[] }>('/customers');
  const contacts = useApi<{ items: Contact[] }>('/messaging/conversations');
  const templates = useApi<{ items: Template[] }>('/messaging/templates');
  const [contact, setContact] = useState<Contact | null>(null);
  const [customer, setCustomer] = useState('');
  const [channel, setChannel] = useState<'EMAIL' | 'WHATSAPP'>('EMAIL');
  const [useCustomerPhone, setUseCustomerPhone] = useState(true);
  const [template, setTemplate] = useState('order_update');
  const [order, setOrder] = useState('');
  const [newPurpose, setNewPurpose] = useState<'TRANSACTIONAL' | 'MARKETING'>('MARKETING');
  const [newChannel, setNewChannel] = useState<'EMAIL' | 'WHATSAPP'>('EMAIL');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string | null>(null);
  const orders = useApi<{ items: { id: string; order_number: string }[] }>(contact ? '/orders' : null, { customer_id: contact?.customer_id });
  const messages = useApi<{ items: Message[] }>(contact ? '/messaging/messages' : null, { conversation_id: contact?.id });
  const history = useApi<{ items: ConsentRow[] }>(contact ? `/messaging/conversations/${contact.id}/consents` : null);
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); contacts.reload(); channels.reload(); messages.reload(); templates.reload(); history.reload(); }
    catch (caught) { setError(caught); } finally { setBusy(false); }
  }
  const orderTemplates = templates.data?.items.filter(t => t.purpose === 'TRANSACTIONAL' && t.channel === contact?.channel) ?? [];
  const selectedTemplate = orderTemplates.find(t => t.key === template);
  const blockerText = (code: string | null) => {
    const copy: Record<string, [string, string]> = {
      EMAIL_OFFICIAL_PROVIDER_REQUIRED: ['Email sending is not set up on this deployment', 'এই ecomsbd-এ ইমেইল সেটআপ হয়নি'],
      META_APP_SETUP_REQUIRED: ['WhatsApp is not set up on this deployment', 'এই ecomsbd-এ WhatsApp সেটআপ হয়নি'],
      WHATSAPP_CONNECTION_REQUIRED: ['Link your WhatsApp Business number in Integrations', 'ইন্টিগ্রেশন থেকে WhatsApp Business নম্বর যুক্ত করুন'],
      MESSENGER_MARKETING_OPT_IN_REQUIRED: ['Messenger needs Meta’s per-customer marketing opt-in; not available', 'Messenger-এ Meta-র আলাদা মার্কেটিং সম্মতি লাগে; এখন নেই'],
      SMS_OFFICIAL_PROVIDER_REQUIRED: ['Needs an approved SMS sender', 'অনুমোদিত SMS প্রেরক প্রয়োজন'],
    };
    return code ? (copy[code]?.[bn ? 1 : 0] ?? code) : '';
  };
  const statusText = (value: string) => ({
    SENT: tx('Sent', 'পাঠানো'), DELIVERED: tx('Delivered', 'পৌঁছেছে'), READ: tx('Read', 'পড়েছেন'), FAILED: tx('Failed', 'ব্যর্থ'),
    SUPPRESSED: tx('Stopped (consent)', 'থামানো (সম্মতি)'), UNKNOWN: tx('Unknown', 'অজানা'), QUEUED: tx('Queued', 'সারিতে'), CANCELLED: tx('Cancelled', 'বাতিল'),
  } as Record<string, string>)[value] ?? value;
  return <>
    <PageHeader title={tx('Customer messaging', 'গ্রাহককে মেসেজ')} subtitle={tx('Order updates and offers, each with its own recorded consent.', 'অর্ডারের আপডেট ও অফার — প্রতিটির আলাদা সম্মতি সংরক্ষিত।')} />
    <div className="page-content">
      {error || channels.error || contacts.error ? <ErrorState error={error || channels.error || contacts.error} /> : null}
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Channels', 'মাধ্যম')}</h2>
        {channels.data?.items.map(c => <p key={c.kind}>
          <strong>{c.kind}</strong> · {c.available && !c.shop_blocker ? tx('Available', 'উপলভ্য') : blockerText(c.blocker ?? c.shop_blocker)}
          {c.available ? <> · {c.reports.length ? tx('Delivery receipts reported', 'ডেলিভারি জানা যায়') : tx('Delivery not reported', 'ডেলিভারি জানা যায় না')}</> : null}
          {' '}<button className="btn" disabled={busy || ((!c.available || !!c.shop_blocker) && !c.enabled)} onClick={() => void run(() => api.post(`/messaging/channels/${c.kind}`, { enabled: !c.enabled }))}>{c.enabled ? tx('Disable', 'বন্ধ করুন') : tx('Enable', 'চালু করুন')}</button>
        </p>)}
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Record contact and consent', 'যোগাযোগ ও সম্মতি সংরক্ষণ')}</h2>
        <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void run(async () => {
          const saved = await api.post<Contact>('/messaging/conversations', {
            customer_id: customer, channel,
            ...(channel === 'WHATSAPP' && useCustomerPhone ? { use_customer_phone: true } : { recipient: data.get('recipient') }),
            consent: data.get('consent') === 'on', marketing_consent: data.get('marketing') === 'on', evidence: data.get('evidence'),
          });
          setContact(saved);
        }); }}>
          <label className="field">{tx('Customer', 'গ্রাহক')}<select className="select" required value={customer} onChange={e => setCustomer(e.target.value)}><option value="">{tx('Select customer', 'গ্রাহক বাছুন')}</option>{customers.data?.items.map(c => <option key={c.id} value={c.id}>{c.name || c.phone_masked}</option>)}</select></label>
          <label className="field">{tx('Channel', 'মাধ্যম')}<select className="select" value={channel} onChange={e => setChannel(e.target.value as 'EMAIL' | 'WHATSAPP')}><option value="EMAIL">{tx('Email', 'ইমেইল')}</option><option value="WHATSAPP">WhatsApp</option></select></label>
          {channel === 'WHATSAPP' ? <label><input type="checkbox" checked={useCustomerPhone} onChange={e => setUseCustomerPhone(e.target.checked)} /> {tx('Use the customer’s saved phone number', 'গ্রাহকের সংরক্ষিত ফোন নম্বর ব্যবহার করুন')}</label> : null}
          {channel === 'EMAIL' || !useCustomerPhone ? <label className="field">{channel === 'EMAIL' ? tx('Email', 'ইমেইল') : tx('WhatsApp number', 'WhatsApp নম্বর')}<input className="input" name="recipient" type={channel === 'EMAIL' ? 'email' : 'tel'} required maxLength={254} /></label> : null}
          <label className="field">{tx('Consent evidence / reason', 'সম্মতির প্রমাণ / কারণ')}<input className="input" name="evidence" minLength={3} maxLength={500} required /></label>
          <label><input name="consent" type="checkbox" /> {tx('Customer agreed to order updates', 'গ্রাহক অর্ডারের আপডেট পেতে সম্মত')}</label><br />
          <label><input name="marketing" type="checkbox" /> {tx('Customer separately agreed to receive offers', 'গ্রাহক আলাদাভাবে অফার পেতে সম্মত')}</label>
          <p className="card__hint">{tx('Record offers consent only when the customer said yes to offers. Order-update consent never covers offers.', 'গ্রাহক অফারে হ্যাঁ বললেই শুধু অফারের সম্মতি দিন। অর্ডার আপডেটের সম্মতিতে অফার পড়ে না।')}</p>
          <button className="btn btn--primary" disabled={busy}>{tx('Save', 'সংরক্ষণ')}</button>
        </form>
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Conversations', 'কথোপকথন')}</h2>
        {contacts.data?.items.map(c => <button className="btn" key={c.id} onClick={() => { setContact(c); setOrder(''); setTemplate('order_update'); key.current = null; }}>{c.channel} · {c.recipient_masked} · {c.consent ? tx('Updates ✓', 'আপডেট ✓') : tx('No updates', 'আপডেট নয়')} · {c.marketing_consent ? tx('Offers ✓', 'অফার ✓') : tx('No offers', 'অফার নয়')}{c.undeliverable_reason ? ` · ${tx('Undeliverable', 'পৌঁছায় না')}` : ''}</button>)}
        {contact ? <>
          <h3>{contact.channel} · {contact.recipient_masked}</h3>
          <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); const scope = data.get('scope'); const evidence = data.get('evidence'); void run(async () => {
            const path = scope === 'MARKETING' ? 'marketing-consent' : 'consent';
            const current = scope === 'MARKETING' ? contact.marketing_consent : contact.consent;
            setContact(await api.patch<Contact>(`/messaging/conversations/${contact.id}/${path}`, { consent: !current, evidence }));
          }); }}>
            <label className="field">{tx('Consent change reason', 'সম্মতি পরিবর্তনের কারণ')}<input className="input" name="evidence" required minLength={3} maxLength={500} /></label>
            <label className="field">{tx('Which consent', 'কোন সম্মতি')}<select className="select" name="scope"><option value="TRANSACTIONAL">{contact.consent ? tx('Stop order updates', 'অর্ডার আপডেট বন্ধ') : tx('Allow order updates', 'অর্ডার আপডেট চালু')}</option><option value="MARKETING">{contact.marketing_consent ? tx('Stop offers', 'অফার বন্ধ') : tx('Allow offers', 'অফার চালু')}</option></select></label>
            <button className="btn" disabled={busy}>{tx('Record change', 'পরিবর্তন সংরক্ষণ')}</button>
          </form>
          <details><summary>{tx('Consent history', 'সম্মতির ইতিহাস')}</summary>{history.data?.items.map(h => <p key={h.id}>{new Date(h.created_at).toLocaleString(locale)} · {h.scope === 'MARKETING' ? tx('Offers', 'অফার') : tx('Order updates', 'অর্ডার আপডেট')} · {h.consent ? tx('Allowed', 'অনুমতি') : tx('Stopped', 'বন্ধ')} · {h.source} · {h.evidence}</p>)}</details>
          <label className="field">{tx('Order', 'অর্ডার')}<select className="select" value={order} onChange={e => { setOrder(e.target.value); key.current = null; }}><option value="">{tx('Select order', 'অর্ডার বাছুন')}</option>{orders.data?.items.map(o => <option key={o.id} value={o.id}>{o.order_number}</option>)}</select></label>
          <label className="field">{tx('Order-update template', 'অর্ডার আপডেট টেমপ্লেট')}<select className="select" value={template} onChange={e => { setTemplate(e.target.value); key.current = null; }}>{orderTemplates.map(t => <option key={t.key}>{t.key}</option>)}</select></label>
          <p>{selectedTemplate?.[bn ? 'body_bn' : 'body_en']}</p>
          <button className="btn btn--primary" disabled={busy || !order || !selectedTemplate || !contact.consent || !channels.data?.items.some(c => c.kind === contact.channel && c.available && c.enabled)} onClick={() => void run(async () => { key.current ??= crypto.randomUUID(); await api.post('/messaging/messages', { conversation_id: contact.id, order_id: order, template_key: template, locale, idempotency_key: key.current }); key.current = null; })}>{tx('Send order update', 'অর্ডারের আপডেট পাঠান')}</button>
          <button className="btn" onClick={messages.reload}>{tx('Refresh history', 'ইতিহাস হালনাগাদ')}</button>
          {messages.error ? <ErrorState error={messages.error} /> : null}
          {messages.data?.items.map(m => <article key={m.id}><h4>{m.subject || (m.purpose === 'MARKETING' ? tx('Offer', 'অফার') : tx('Order update', 'অর্ডার আপডেট'))}</h4><p>{m.body}</p><p>{tx('Status / attempts', 'অবস্থা / প্রচেষ্টা')}: {statusText(m.status)} / {m.attempts}{m.last_error ? ` (${m.last_error})` : ''}</p>{m.status === 'FAILED' && m.purpose === 'TRANSACTIONAL' ? <button className="btn" disabled={busy} onClick={() => void run(() => api.post(`/messaging/messages/${m.id}/retry`))}>{tx('Retry', 'আবার চেষ্টা')}</button> : null}</article>)}
        </> : null}
      </section>
      <section className="card" style={{ padding: 20 }}>
        <h2>{tx('Templates', 'টেমপ্লেট')}</h2>
        {templates.data?.items.map(t => <p key={t.key}><strong>{t.key}</strong> · {t.purpose === 'MARKETING' ? tx('Offer', 'অফার') : tx('Order update', 'অর্ডার আপডেট')} · {t.channel}{t.channel === 'WHATSAPP' ? ` · Meta: ${t.provider_status ?? '—'}` : ''}{!t.builtin ? <> {' '}<button className="btn btn--sm" disabled={busy} onClick={() => void run(() => api.patch(`/messaging/templates/${t.key}`, { archived: true }))}>{tx('Archive', 'আর্কাইভ')}</button></> : null}</p>)}
        <details><summary>{tx('Create template', 'টেমপ্লেট তৈরি')}</summary>
          <p className="card__hint">{newPurpose === 'MARKETING' ? tx('Offers may use {customer_name} and {shop_name}. Emails get an unsubscribe line automatically.', 'অফারে {customer_name} ও {shop_name} ব্যবহার করা যায়। ইমেইলে আনসাবস্ক্রাইব লাইন নিজে থেকেই যুক্ত হয়।') : tx('Order updates may use {customer_name}, {order_number} and {shop_name}.', 'অর্ডার আপডেটে {customer_name}, {order_number} ও {shop_name} ব্যবহার করা যায়।')}</p>
          {newChannel === 'WHATSAPP' ? <p className="card__hint">{tx('WhatsApp sends only templates Meta approved. Create it in WhatsApp Manager first, then enter its exact name and languages here; the text below is your preview. Include a way to opt out (for example “Reply STOP”).', 'WhatsApp-এ শুধু Meta-অনুমোদিত টেমপ্লেট যায়। আগে WhatsApp Manager-এ তৈরি করুন, তারপর এখানে হুবহু নাম ও ভাষা দিন; নিচের লেখা শুধু প্রিভিউ। বন্ধ করার উপায় রাখুন (যেমন “STOP লিখুন”)।')}</p> : null}
          <form onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const data = Object.fromEntries(new FormData(form)) as Record<string, string>; const variables = (data.variables ?? '').split(',').map(v => v.trim()).filter(Boolean); delete data.variables; for (const k of Object.keys(data)) if (data[k] === '') delete data[k]; void run(async () => { await api.post('/messaging/templates', { ...data, purpose: newPurpose, channel: newChannel, ...(newChannel === 'WHATSAPP' ? { variables } : {}) }); form.reset(); }); }}>
            <label className="field">{tx('Purpose', 'উদ্দেশ্য')}<select className="select" value={newPurpose} onChange={e => setNewPurpose(e.target.value as 'TRANSACTIONAL' | 'MARKETING')}><option value="MARKETING">{tx('Offer (marketing)', 'অফার (মার্কেটিং)')}</option><option value="TRANSACTIONAL">{tx('Order update', 'অর্ডার আপডেট')}</option></select></label>
            <label className="field">{tx('Channel', 'মাধ্যম')}<select className="select" value={newChannel} onChange={e => setNewChannel(e.target.value as 'EMAIL' | 'WHATSAPP')}><option value="EMAIL">{tx('Email', 'ইমেইল')}</option><option value="WHATSAPP">WhatsApp</option></select></label>
            <label className="field">{tx('Template key', 'টেমপ্লেট কী')}<input className="input" name="key" required pattern="[a-z][a-z0-9_]{2,79}" /></label>
            {newChannel === 'EMAIL' ? <>
              <label className="field">{tx('English subject', 'ইংরেজি বিষয়')}<input className="input" name="subject_en" required maxLength={160} /></label>
              <label className="field">{tx('Bangla subject', 'বাংলা বিষয়')}<input className="input" name="subject_bn" required maxLength={160} /></label>
            </> : <>
              <label className="field">{tx('Approved template name (Meta)', 'অনুমোদিত টেমপ্লেটের নাম (Meta)')}<input className="input" name="provider_name" required pattern="[a-z0-9_]{1,512}" /></label>
              <label className="field">{tx('Bangla language code (e.g. bn)', 'বাংলা ভাষার কোড (যেমন bn)')}<input className="input" name="provider_language_bn" pattern="[a-z]{2}(_[A-Z]{2})?" /></label>
              <label className="field">{tx('English language code (e.g. en_US)', 'ইংরেজি ভাষার কোড (যেমন en_US)')}<input className="input" name="provider_language_en" pattern="[a-z]{2}(_[A-Z]{2})?" /></label>
              <label className="field">{tx('Parameters in order {{1}}, {{2}}… (comma separated)', 'প্যারামিটার ক্রমানুসারে {{1}}, {{2}}… (কমা দিয়ে)')}<input className="input" name="variables" placeholder="customer_name, shop_name" /></label>
            </>}
            <label className="field">{tx('English message', 'ইংরেজি মেসেজ')}<textarea className="input" name="body_en" required maxLength={2000} /></label>
            <label className="field">{tx('Bangla message', 'বাংলা মেসেজ')}<textarea className="input" name="body_bn" required maxLength={2000} /></label>
            <button className="btn" disabled={busy}>{tx('Save template', 'টেমপ্লেট সংরক্ষণ')}</button>
          </form>
        </details>
      </section>
    </div>
  </>;
}
