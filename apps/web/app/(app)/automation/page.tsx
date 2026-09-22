'use client';

import { useState } from 'react';
import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Condition = { field: string; op: string; value: string | number };
type Rule = { id: string; name: string; trigger: string; conditions: Condition[]; action: string; config: Record<string, string | number>; enabled: boolean };
type Execution = { id: string; rule_id: string; action: string; status: string; attempts: number; last_error: string | null; created_at: string };
type Task = { id: string; text_en: string; text_bn: string; due_at: string; completed_at: string | null };
const actionLabels: Record<string, [string, string]> = {
  SEND_TEMPLATE: ['Send transactional template', 'অর্ডারের টেমপ্লেট পাঠান'],
  CREATE_FOLLOWUP: ['Create follow-up', 'ফলো-আপ তৈরি'],
  ADD_TAG: ['Add CRM tag', 'CRM ট্যাগ যোগ'], REMOVE_TAG: ['Remove CRM tag', 'CRM ট্যাগ সরান'],
  SELLER_NOTIFICATION: ['Notify seller', 'বিক্রেতাকে জানান'], CREATE_TASK: ['Create task', 'কাজ তৈরি'],
};

export default function AutomationPage() {
  const { locale } = useSession();
  const bn = locale === 'bn';
  const tx = (en: string, bangla: string) => bn ? bangla : en;
  const label = (action: string) => actionLabels[action]?.[bn ? 1 : 0] ?? action;
  const catalog = useApi<{ tags: { id: string; name: string }[]; templates: { key: string }[]; statuses: string[]; channels: string[] }>('/automation/catalog');
  const rules = useApi<{ items: Rule[] }>('/automation/rules');
  const history = useApi<{ items: Execution[] }>('/automation/executions');
  const tasks = useApi<{ items: Task[] }>('/automation/tasks');
  const [editing, setEditing] = useState<Rule | null>(null);
  const [action, setAction] = useState('CREATE_TASK');
  const [trigger, setTrigger] = useState('order.created');
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const attempts = useApi<{ items: { id: string; status: string; error: string | null; created_at: string }[] }>(selected ? `/automation/executions/${selected}/attempts` : null);
  async function run(work: () => Promise<unknown>) { setBusy(true); setError(null); try { await work(); rules.reload(); history.reload(); tasks.reload(); attempts.reload(); } catch(e) { setError(e); } finally { setBusy(false); } }
  function edit(rule: Rule | null) { setEditing(rule); setAction(rule?.action ?? 'CREATE_TASK'); setTrigger(rule?.trigger ?? 'order.created'); setConditions(rule?.conditions ?? []); }
  function condition(index: number, value: Partial<Condition>) { setConditions(conditions.map((c, i) => i === index ? { ...c, ...value } : c)); }
  const defaults = editing?.action === action ? editing.config : {};
  const textField = (name: string, title: string, max = 1000) => <label className="field" key={name}>{title}<textarea className="input" name={`config.${name}`} defaultValue={defaults[name] ?? ''} required maxLength={max} /></label>;
  return <><PageHeader title={tx('Automation rules', 'অটোমেশন নিয়ম')} subtitle={tx('Trigger + all conditions → one safe action. Rules apply to future events.', 'ট্রিগার + সব শর্ত → একটি কাজ। নতুন ইভেন্টে নিয়ম কাজ করবে।')} /><div className="content">
    {error || rules.error || catalog.error ? <ErrorState error={error || rules.error || catalog.error} /> : null}
    <section className="card" style={{ padding: 20 }}>
      <h2>{editing ? tx('Edit rule', 'নিয়ম সম্পাদনা') : tx('Create rule', 'নিয়ম তৈরি')}</h2>
      <form key={editing?.id ?? 'new'} onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); const config: Record<string, string | number> = {}; for (const [key, value] of data.entries()) { if (key.startsWith('config.')) { const name = key.slice(7); config[name] = name === 'due_hours' ? Number(value) : String(value); } } void run(async () => { const body = { name: data.get('name'), trigger, conditions, action, config, enabled: data.get('enabled') === 'on' }; if (editing) await api.put(`/automation/rules/${editing.id}`, body); else await api.post('/automation/rules', body); edit(null); form.reset(); }); }}>
        <label className="field">{tx('Rule name', 'নিয়মের নাম')}<input className="input" name="name" defaultValue={editing?.name ?? ''} required maxLength={100} /></label>
        <label className="field">{tx('Trigger', 'কখন')}<select className="select" value={trigger} onChange={e => setTrigger(e.target.value)}><option value="order.created">{tx('Order created', 'অর্ডার তৈরি হলে')}</option><option value="order.status_changed">{tx('Order status changed', 'অর্ডারের অবস্থা বদলালে')}</option></select></label>
        <fieldset><legend>{tx('All conditions must match', 'সব শর্ত মিলতে হবে')}</legend>
          {conditions.map((c, i) => <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            <select className="select" aria-label={tx('Condition field', 'শর্তের বিষয়')} value={c.field} onChange={e => condition(i, { field: e.target.value, op: 'eq', value: e.target.value === 'cod_amount_paisa' ? 0 : '' })}><option value="cod_amount_paisa">{tx('COD (paisa)', 'COD (পয়সা)')}</option><option value="status">{tx('Order status', 'অর্ডারের অবস্থা')}</option><option value="channel">{tx('Order channel', 'অর্ডারের মাধ্যম')}</option></select>
            <select className="select" aria-label={tx('Comparison', 'তুলনা')} value={c.op} onChange={e => condition(i, { op: e.target.value })}><option value="eq">{tx('Equals', 'সমান')}</option><option value="ne">{tx('Does not equal', 'সমান নয়')}</option>{c.field === 'cod_amount_paisa' ? <><option value="gte">≥</option><option value="lte">≤</option></> : null}</select>
            {c.field === 'cod_amount_paisa' ? <input className="input" aria-label={tx('Condition value', 'শর্তের মান')} type="number" min={0} required value={c.value} onChange={e => condition(i, { value: Number(e.target.value) })} /> : <select className="select" aria-label={tx('Condition value', 'শর্তের মান')} required value={c.value} onChange={e => condition(i, { value: e.target.value })}><option value="">{tx('Select', 'বাছুন')}</option>{(c.field === 'status' ? catalog.data?.statuses : catalog.data?.channels)?.map(v => <option key={v}>{v}</option>)}</select>}
            <button type="button" className="btn" onClick={() => setConditions(conditions.filter((_, n) => n !== i))}>{tx('Remove', 'সরান')}</button>
          </div>)}
          <button type="button" className="btn" disabled={conditions.length >= 8} onClick={() => setConditions([...conditions, { field: 'cod_amount_paisa', op: 'gte', value: 0 }])}>{tx('Add condition', 'শর্ত যোগ')}</button>
        </fieldset>
        <label className="field">{tx('Action', 'কাজ')}<select className="select" value={action} onChange={e => setAction(e.target.value)}>{Object.keys(actionLabels).map(a => <option value={a} key={a}>{label(a)}</option>)}</select></label>
        <div key={`${editing?.id}-${action}`}>
          {action === 'SEND_TEMPLATE' ? <><label className="field">{tx('Configured template', 'নির্ধারিত টেমপ্লেট')}<select className="select" name="config.template_key" required defaultValue={defaults.template_key ?? 'order_update'}>{catalog.data?.templates.map(t => <option key={t.key}>{t.key}</option>)}</select></label><label className="field">{tx('Message language', 'মেসেজের ভাষা')}<select className="select" name="config.locale" defaultValue={defaults.locale ?? locale}><option value="bn">বাংলা</option><option value="en">English</option></select></label><p>{tx('Email must be configured and enabled. Customer consent is checked again before sending.', 'ইমেইল সংযোগ চালু থাকতে হবে। পাঠানোর আগে গ্রাহকের সম্মতি আবার যাচাই হবে।')}</p></> : null}
          {action === 'CREATE_FOLLOWUP' ? textField('text', tx('Follow-up note', 'ফলো-আপ নোট')) : null}
          {['CREATE_TASK', 'SELLER_NOTIFICATION'].includes(action) ? <>{textField('text_en', tx('English text', 'ইংরেজি লেখা'))}{textField('text_bn', tx('Bangla text', 'বাংলা লেখা'))}</> : null}
          {action === 'SELLER_NOTIFICATION' ? <>{textField('title_en', tx('English title', 'ইংরেজি শিরোনাম'), 160)}{textField('title_bn', tx('Bangla title', 'বাংলা শিরোনাম'), 160)}</> : null}
          {['CREATE_FOLLOWUP', 'CREATE_TASK'].includes(action) ? <label className="field">{tx('Due after hours', 'কত ঘণ্টা পরে করতে হবে')}<input className="input" type="number" name="config.due_hours" min={1} max={720} defaultValue={defaults.due_hours ?? 24} required /></label> : null}
          {['ADD_TAG', 'REMOVE_TAG'].includes(action) ? <label className="field">{tx('CRM tag', 'CRM ট্যাগ')}<select className="select" name="config.tag_id" defaultValue={defaults.tag_id ?? ''} required><option value="">{tx('Select an existing tag', 'আগের ট্যাগ বাছুন')}</option>{catalog.data?.tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label> : null}
        </div>
        <label><input type="checkbox" name="enabled" defaultChecked={editing?.enabled ?? false} /> {tx('Enable this rule', 'নিয়মটি চালু করুন')}</label>
        <p><button className="btn btn--primary" disabled={busy}>{tx('Save rule', 'নিয়ম সংরক্ষণ')}</button>{editing ? <button type="button" className="btn" onClick={() => edit(null)}>{tx('Cancel edit', 'সম্পাদনা বাতিল')}</button> : null}</p>
      </form>
    </section>
    <section className="card" style={{ padding: 20 }}><h2>{tx('Rules', 'নিয়মগুলো')}</h2>{rules.data?.items.map(r => <article key={r.id}><h3>{r.name}</h3><p>{label(r.action)} · {r.enabled ? tx('Enabled', 'চালু') : tx('Disabled', 'বন্ধ')}</p><button className="btn" disabled={busy} onClick={() => void run(() => api.patch(`/automation/rules/${r.id}`, { enabled: !r.enabled }))}>{r.enabled ? tx('Disable', 'বন্ধ করুন') : tx('Enable', 'চালু করুন')}</button><button className="btn" onClick={() => edit(r)}>{tx('Edit', 'সম্পাদনা')}</button></article>)}</section>
    <section className="card" style={{ padding: 20 }}><h2>{tx('Execution history', 'চালানোর ইতিহাস')}</h2><button className="btn" onClick={history.reload}>{tx('Refresh', 'হালনাগাদ')}</button>{history.error ? <ErrorState error={history.error} /> : null}{history.data?.items.map(h => <article key={h.id}><p>{label(h.action)} · {h.status} · {tx('Attempts', 'প্রচেষ্টা')}: {h.attempts} {h.last_error ? `(${h.last_error})` : ''}</p><button className="btn" onClick={() => setSelected(h.id)}>{tx('Details', 'বিস্তারিত')}</button>{h.status === 'FAILED' ? <button className="btn" disabled={busy} onClick={() => void run(() => api.post(`/automation/executions/${h.id}/retry`))}>{tx('Retry', 'আবার চেষ্টা')}</button> : null}</article>)}{attempts.data?.items.map(a => <p key={a.id}>{a.created_at}: {a.status} {a.error}</p>)}</section>
    <section className="card" style={{ padding: 20 }}><h2>{tx('Tasks', 'কাজগুলো')}</h2>{tasks.error ? <ErrorState error={tasks.error} /> : null}{tasks.data?.items.map(t => <article key={t.id}><p>{t[bn ? 'text_bn' : 'text_en']} · {new Date(t.due_at).toLocaleString(locale)}</p><button className="btn" disabled={busy} onClick={() => void run(() => api.patch(`/automation/tasks/${t.id}`, { completed: !t.completed_at }))}>{t.completed_at ? tx('Reopen', 'আবার খুলুন') : tx('Mark complete', 'সম্পন্ন করুন')}</button></article>)}</section>
  </div></>;
}
