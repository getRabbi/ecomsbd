'use client';

import { useState } from 'react';
import { useCursorStack } from '@/components/DataTable';
import { api, type Page } from '@/lib/api';
import { crmKey, type Activity, type Customer, type FollowUp, type Tag } from '@/lib/crm';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';
import { Card, ErrorState } from './ui';

export function CrmDefinitions({ definitions }: { definitions: Record<string, number | null> }) {
  const { t, locale } = useSession();
  const vars = Object.fromEntries(Object.entries(definitions).filter((entry): entry is [string, number] => entry[1] !== null));
  return <details><summary>{t('crm.definitions')}</summary><p>{t('crm.rules', vars)}</p><p>{t('crm.highRule', vars)}</p>
    {definitions.high_value_threshold_paisa != null && <p>{t('crm.threshold')}: {formatPaisa(definitions.high_value_threshold_paisa, { locale })}</p>}
  </details>;
}

export function CrmSelect({ kind = 'tags', value, onChange, refresh = 0 }: { kind?: 'tags' | 'members'; value: string; onChange: (id: string) => void; refresh?: number }) {
  const { t } = useSession();
  const paging = useCursorStack();
  const { data, loading, error, reload } = useApi<Page<Tag>>(`/customers/crm/${kind}`, { limit: 50, cursor: paging.cursor, refresh });
  return <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
    <select className="input" aria-label={t(kind === 'tags' ? 'crm.tags' : 'crm.assignee')} value={value} disabled={loading} onChange={e => onChange(e.target.value)}>
      <option value="">{t(kind === 'tags' ? 'crm.tags' : 'crm.unassigned')}</option>
      {data?.items.map(row => <option key={row.id} value={row.id}>{row.name || t('crm.member')}</option>)}
    </select>
    {paging.canGoBack && <button className="btn" type="button" onClick={() => { onChange(''); paging.back(); }}>{t('crm.previous')}</button>}
    {data?.has_more && <button className="btn" type="button" onClick={() => { onChange(''); paging.forward(data.next_cursor); }}>{t(kind === 'tags' ? 'crm.moreTags' : 'crm.moreMembers')}</button>}
    {Boolean(error) && <button className="btn" type="button" onClick={reload}>{t('common.retry')}</button>}
  </span>;
}

export function CustomerWorkspace({ id }: { id: string }) {
  const { t, locale } = useSession();
  const { data: customer, error, reload } = useApi<Customer>(`/customers/${id}/crm`);
  const [tab, setTab] = useState('overview');
  const [tag, setTag] = useState('');
  const [tagName, setTagName] = useState('');
  const [refreshTags, setRefreshTags] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(false);
  const [version, setVersion] = useState(0);
  async function mutate(work: () => Promise<unknown>) {
    setBusy(true); setFailure(false);
    try { await work(); reload(); setVersion(n => n + 1); } catch { setFailure(true); } finally { setBusy(false); }
  }
  const date = (value: string | null) => value ? new Date(value).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-BD') : '—';
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!customer) return <p role="status">{t('crm.loading')}</p>;
  const money = (value: number | null) => value === null ? t('crm.noValue') : formatPaisa(value, { locale });
  return <>
    <Card title={customer.name || customer.phone_masked} hint={customer.phone_masked}>
      <div className="toolbar" style={{ padding: 0 }}>
        {['overview', 'orders', 'notes', 'followups', 'timeline'].map(name => <button type="button" className="btn" aria-pressed={tab === name} key={name} onClick={() => setTab(name)}>{t(crmKey(name))}</button>)}
      </div>
    </Card>
    {failure && <p role="alert">{t('crm.error')}</p>}
    {tab === 'overview' && <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>
        <Card title={t('crm.overview')}>
          <dl className="crm-facts">{[
            ['firstOrder', date(customer.first_order_at)], ['lastOrder', date(customer.last_order_at)],
            ['orderCount', customer.order_count], ['delivered', customer.delivered_count], ['returned', customer.returned_count],
            ['cancelled', customer.cancelled_count], ['active', customer.active_orders],
            ['success', customer.success_rate_basis_points == null ? '—' : `${customer.success_rate_basis_points / 100}%`],
          ].map(([label, value]) => <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 20, padding: '8px 0' }}><dt>{t(crmKey(String(label)))}</dt><dd>{value}</dd></div>)}</dl>
          {customer.addresses.map(address => <p key={address.id}>{address.raw_address}</p>)}
          {customer.risk && <p>{t('crm.risk')}: {t(crmKey({ LOW: 'LOW_RISK', MEDIUM: 'MEDIUM_RISK', HIGH: 'HIGH_RISK' }[customer.risk.state] || 'INSUFFICIENT_DATA'))}</p>}
          {customer.risk && <ul>{customer.risk.reasons.map(reason => <li key={reason}>{t(crmKey(reason))}</li>)}</ul>}
        </Card>
        <Card title={t('crm.value')}>
          {customer.value ? <>
            {([['totalValue', customer.value.total_order_value_paisa], ['revenue', customer.value.delivered_revenue_paisa], ['profit', customer.value.measured_profit_paisa], ['average', customer.value.average_delivered_order_paisa]] as const).map(([label, value]) => <p key={label}>{t(crmKey(label))}: <strong>{money(value)}</strong></p>)}
            <p className="card__hint">{t('crm.coverage', { measured: customer.value.measured_parcels, completed: customer.value.completed_parcels })}</p>
          </> : <p>{t('crm.locked')}</p>}
        </Card>
      </div>
      <Card title={t('crm.segment')}><p>{customer.segments.map(s => t(crmKey(s))).join(' · ') || t('crm.empty')}</p><CrmDefinitions definitions={customer.definitions} /></Card>
      <Card title={t('crm.tags')}>
        <div className="toolbar">{customer.tags.map(row => <span className="chip" key={row.id}>{row.name}{customer.can_write && <button className="btn" disabled={busy} aria-label={`${t('crm.removeTag')}: ${row.name}`} onClick={() => void mutate(() => api.post('/customers/crm/bulk-tags', { customer_ids: [id], tag_id: row.id, remove: true }))}>×</button>}</span>)}</div>
        {customer.can_write && <div className="toolbar">
          <CrmSelect value={tag} onChange={setTag} refresh={refreshTags} />
          <button className="btn" disabled={!tag || busy} onClick={() => void mutate(() => api.post('/customers/crm/bulk-tags', { customer_ids: [id], tag_id: tag }))}>{t('crm.addTag')}</button>
          <input className="input" aria-label={t('crm.tagName')} placeholder={t('crm.tagName')} maxLength={60} value={tagName} onChange={e => setTagName(e.target.value)} />
          <button className="btn" disabled={!tagName.trim() || busy} onClick={() => void mutate(async () => { const created = await api.post<Tag>('/customers/crm/tags', { name: tagName }); await api.post('/customers/crm/bulk-tags', { customer_ids: [id], tag_id: created.id }); setTagName(''); setRefreshTags(n => n + 1); })}>{t('crm.createTag')}</button>
        </div>}
      </Card>
    </>}
    {tab === 'notes' && <CrmActivity id={id} notes canWrite={customer.can_write} legacy={customer.notes} onChanged={reload} />}
    {tab === 'timeline' && <CrmActivity key={version} id={id} />}
    {tab === 'followups' && <CrmFollowUps id={id} canWrite={customer.can_write} onChanged={reload} />}
    {tab === 'orders' && <CrmOrders id={id} />}
  </>;
}

function Pager({ paging, data }: { paging: ReturnType<typeof useCursorStack>; data: Page<unknown> | null }) {
  const { t } = useSession();
  return <div className="toolbar"><button className="btn" disabled={!paging.canGoBack} onClick={paging.back}>{t('crm.previous')}</button><button className="btn" disabled={!data?.has_more} onClick={() => paging.forward(data?.next_cursor ?? null)}>{t('crm.next')}</button></div>;
}

function CrmActivity({ id, notes = false, canWrite = false, legacy, onChanged }: { id: string; notes?: boolean; canWrite?: boolean; legacy?: string | null; onChanged?: () => void }) {
  const { t, locale } = useSession(); const paging = useCursorStack();
  const { data, error, reload } = useApi<Page<Activity>>(`/customers/${id}/timeline`, { limit: 20, cursor: paging.cursor, notes_only: notes });
  const [text, setText] = useState(''); const [busy, setBusy] = useState(false); const [failure, setFailure] = useState(false);
  return <Card title={t(notes ? 'crm.notes' : 'crm.timeline')}>
    {notes && <p>{t('crm.noteHint')}</p>}{notes && legacy && <p>{t('crm.legacy')}: {legacy}</p>}
    {notes && canWrite && <form onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure(false); try { await api.post(`/customers/${id}/notes`, { text }); setText(''); paging.reset(); reload(); onChanged?.(); } catch { setFailure(true); } finally { setBusy(false); } }}>
      <textarea className="input" aria-label={t('crm.notes')} value={text} maxLength={2000} required onChange={e => setText(e.target.value)} />
      <button className="btn" disabled={busy || !text.trim()}>{t('crm.addNote')}</button>
    </form>}
    {failure && <p role="alert">{t('crm.error')}</p>}{Boolean(error) && <ErrorState error={error} onRetry={reload} />}
    {data?.items.map(row => <article key={`${row.kind}:${row.id}`} style={{ padding: '16px 0', borderBottom: '1px solid var(--stroke)' }}><strong>{t(crmKey(row.kind))}</strong><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.text}</p><small>{new Date(row.created_at).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-BD')}{row.actor_id ? ` · ${row.actor_name || `${t('crm.member')} ${row.actor_id.slice(0, 8)}`}` : ''}</small></article>)}
    {data?.items.length === 0 && <p>{t('crm.empty')}</p>}<Pager paging={paging} data={data} />
  </Card>;
}

function CrmFollowUps({ id, canWrite, onChanged }: { id: string; canWrite: boolean; onChanged: () => void }) {
  const { t, locale } = useSession(); const paging = useCursorStack(); const [all, setAll] = useState(false);
  const { data, error, reload } = useApi<Page<FollowUp>>(`/customers/${id}/follow-ups`, { limit: 20, cursor: paging.cursor, completed: all ? undefined : false });
  const [text, setText] = useState(''); const [due, setDue] = useState(''); const [assignee, setAssignee] = useState('');
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState(false);
  async function run(work: () => Promise<unknown>) { setBusy(true); setFailure(false); try { await work(); reload(); onChanged(); } catch { setFailure(true); } finally { setBusy(false); } }
  return <Card title={t('crm.followups')}>
    {canWrite && <form style={{ display: 'grid', gap: 12 }} onSubmit={e => { e.preventDefault(); void run(async () => { await api.post(`/customers/${id}/follow-ups`, { text, due_at: new Date(due).toISOString(), assignee_id: assignee || null }); setText(''); setDue(''); paging.reset(); }); }}>
      <label>{t('crm.task')}<textarea className="input" value={text} maxLength={2000} required onChange={e => setText(e.target.value)} /></label>
      <label>{t('crm.due')}<input className="input" type="datetime-local" value={due} required onChange={e => setDue(e.target.value)} /></label>
      <CrmSelect kind="members" value={assignee} onChange={setAssignee} /><button className="btn" disabled={busy || !text.trim() || !due}>{t('crm.addFollowup')}</button>
    </form>}
    {failure && <p role="alert">{t('crm.error')}</p>}{Boolean(error) && <ErrorState error={error} onRetry={reload} />}
    <label><input type="checkbox" checked={all} onChange={e => { setAll(e.target.checked); paging.reset(); }} /> {t('crm.allTasks')}</label>
    {data?.items.map(task => <article key={task.id} style={{ padding: '16px 0', borderBottom: '1px solid var(--stroke)' }}><strong>{task.text}</strong><p>{t(crmKey(task.state))} · {new Date(task.due_at).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-BD')}</p><p>{task.assignee_name || (task.assignee_id ? `${t('crm.member')} ${task.assignee_id.slice(0, 8)}` : t('crm.unassigned'))} · {task.author_name || `${t('crm.member')} ${task.created_by.slice(0, 8)}`}</p>{canWrite && <button className="btn" disabled={busy} onClick={() => void run(() => api.patch(`/customers/${id}/follow-ups/${task.id}`, { completed: !task.completed_at }))}>{t(task.completed_at ? 'crm.reopen' : 'crm.complete')}</button>}</article>)}
    {data?.items.length === 0 && <p>{t('crm.empty')}</p>}<Pager paging={paging} data={data} />
  </Card>;
}

function CrmOrders({ id }: { id: string }) {
  const { t, locale } = useSession(); const paging = useCursorStack(); const [selected, setSelected] = useState<string | null>(null);
  const { data, error, reload } = useApi<Page<{ id: string; order_number: string; created_at: string }>>('/orders', { customer_id: id, limit: 20, cursor: paging.cursor });
  const detail = useApi<{ items: { id: string; product_name: string; quantity: number }[] }>(selected ? `/orders/${selected}` : null);
  return <Card title={t('crm.orders')}>
    {Boolean(error) && <ErrorState error={error} onRetry={reload} />}
    {data?.items.map(row => <p key={row.id}><button className="btn" onClick={() => setSelected(row.id)}>{row.order_number}</button> · {new Date(row.created_at).toLocaleDateString(locale === 'bn' ? 'bn-BD' : 'en-BD')}</p>)}
    {detail.loading && <p>{t('crm.loading')}</p>}{Boolean(detail.error) && <ErrorState error={detail.error} onRetry={detail.reload} />}
    {!detail.loading && !detail.refreshing && detail.data?.items.map(item => <p key={item.id}>{item.product_name} × {item.quantity}</p>)}
    {data?.items.length === 0 && <p>{t('crm.empty')}</p>}<Pager paging={paging} data={data} />
  </Card>;
}
