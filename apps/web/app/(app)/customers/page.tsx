'use client';

import Link from 'next/link';
import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { CrmDefinitions, CrmSelect } from '@/components/Crm';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui';
import { api } from '@/lib/api';
import { crmKey, segments, type Customer, type CustomerPage } from '@/lib/crm';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

export default function CustomersPage() { return <Suspense><Customers /></Suspense>; }

function Customers() {
  const { t, locale } = useSession(); const params = useSearchParams();
  const [search, setSearch] = useState(''); const debounced = useDebounced(search);
  const [segment, setSegment] = useState(params.get('segment') || ''); const [tag, setTag] = useState('');
  const [min, setMin] = useState(''); const [max, setMax] = useState(''); const [from, setFrom] = useState(''); const [until, setUntil] = useState('');
  const [selected, setSelected] = useState(new Set<string>()); const [bulkTag, setBulkTag] = useState('');
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState(false); const [tagVersion, setTagVersion] = useState(0);
  const paging = useCursorStack();
  const reset = () => { paging.reset(); setSelected(new Set()); };
  const { data, loading, refreshing, error, reload } = useApi<CustomerPage>('/customers/crm', {
    search: debounced || undefined, limit: 30, cursor: paging.cursor, segment: segment || undefined, tag_id: tag || undefined,
    min_orders: min ? Number(min) : undefined, max_orders: max ? Number(max) : undefined,
    last_from: from ? new Date(`${from}T00:00:00+06:00`).toISOString() : undefined,
    last_until: until ? new Date(`${until}T23:59:59.999+06:00`).toISOString() : undefined,
  });
  const rows = data?.items ?? [];
  async function mutate(remove: boolean) {
    setBusy(true); setFailure(false);
    try { await api.post('/customers/crm/bulk-tags', { customer_ids: [...selected], tag_id: bulkTag, remove }); setSelected(new Set()); reload(); }
    catch { setFailure(true); } finally { setBusy(false); }
  }
  const columns: Column<Customer>[] = [
    { key: 'name', header: t('crm.customer'), render: row => <><Link className="table__primary" href={`/customers/${row.id}`}>{row.name || row.phone_masked}</Link><div className="table__sub">{row.phone_masked}</div><div className="table__sub">{row.tags.map(tag => tag.name).join(' · ')}</div></> },
    { key: 'orders', header: t('crm.orderCount'), numeric: true, render: row => row.order_count },
    { key: 'delivered', header: t('crm.delivered'), numeric: true, render: row => row.delivered_count },
    { key: 'rto', header: t('crm.returned'), numeric: true, render: row => row.returned_count },
    { key: 'active', header: t('crm.active'), numeric: true, render: row => row.active_orders },
    { key: 'segments', header: t('crm.segment'), render: row => row.segments.map(s => t(crmKey(s))).join(' · ') || '—' },
    { key: 'followup', header: t('crm.followups'), render: row => row.next_follow_up_at ? new Date(row.next_follow_up_at).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-BD') : '—' },
    ...(!data?.money_locked ? [{ key: 'value', header: t('crm.revenue'), numeric: true, render: (row: Customer) => row.value?.delivered_revenue_paisa == null ? '—' : formatPaisa(row.value.delivered_revenue_paisa, { locale }) }] : []),
  ];
  return <><PageHeader title={t('crm.title')} subtitle={t('cust.subtitle')} /><div className="content">
    <Card><div className="toolbar" style={{ flexWrap: 'wrap' }}>
      <input className="input" type="search" aria-label={t('crm.search')} placeholder={t('crm.search')} value={search} onChange={e => { setSearch(e.target.value); reset(); }} />
      <select className="input" aria-label={t('crm.segment')} value={segment} onChange={e => { setSegment(e.target.value); reset(); }}><option value="">{t('crm.all')}</option>{segments.filter(s => s !== 'HIGH_VALUE' || !data?.money_locked).map(s => <option value={s} key={s}>{t(crmKey(s))}</option>)}</select>
      <CrmSelect value={tag} onChange={value => { setTag(value); reset(); }} refresh={tagVersion} />
      {([[min, setMin, 'minOrders'], [max, setMax, 'maxOrders']] as const).map(([value, setter, label]) => <label key={label}>{t(crmKey(label))}<input className="input" type="number" min={0} value={value} onChange={e => { setter(e.target.value); reset(); }} /></label>)}
      {([[from, setFrom, 'lastFrom'], [until, setUntil, 'lastUntil']] as const).map(([value, setter, label]) => <label key={label}>{t(crmKey(label))}<input className="input" type="date" value={value} onChange={e => { setter(e.target.value); reset(); }} /></label>)}
      <button className="btn" onClick={() => { setSearch(''); setSegment(''); setTag(''); setMin(''); setMax(''); setFrom(''); setUntil(''); reset(); }}>{t('crm.reset')}</button>
    </div>{data && <CrmDefinitions definitions={data.definitions} />}</Card>
    {data?.can_write && <Card><div className="toolbar"><span>{t('crm.bulk', { count: selected.size })}</span><CrmSelect value={bulkTag} onChange={setBulkTag} refresh={tagVersion} />
      <button className="btn" disabled={!selected.size || !bulkTag || busy || refreshing} onClick={() => void mutate(false)}>{t('crm.addTag')}</button><button className="btn" disabled={!selected.size || !bulkTag || busy || refreshing} onClick={() => void mutate(true)}>{t('crm.removeTag')}</button>
      <button className="btn" disabled={!bulkTag || busy} onClick={async () => { setBusy(true); setFailure(false); try { await api.delete(`/customers/crm/tags/${bulkTag}`); setBulkTag(''); setTag(''); reset(); setTagVersion(n => n + 1); reload(); } catch { setFailure(true); } finally { setBusy(false); } }}>{t('crm.archiveTag')}</button>
    </div>{failure && <p role="alert">{t('crm.error')}</p>}</Card>}
    <Card padded={false}><DataTable columns={columns} rows={rows} loading={loading} error={error} onRetry={reload} emptyTitle={t('crm.empty')}
      selection={data?.can_write && !refreshing ? { selected, onToggle: id => setSelected(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; }), onToggleAll: checked => setSelected(new Set(checked ? rows.map(r => r.id) : [])) } : undefined}
      pagination={{ canGoBack: paging.canGoBack, canGoForward: Boolean(data?.has_more), onBack: () => { setSelected(new Set()); paging.back(); }, onForward: () => { setSelected(new Set()); paging.forward(data?.next_cursor ?? null); } }} />
    </Card>
  </div></>;
}
