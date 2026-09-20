'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Row, Tile } from '@/components/ui';
import { strings, type StringKey } from '@/lib/i18n';
import type { Cash, Comparison, Counts, Couriers, Customers, Explanation, Inventory, Overview, ProductPage, Reconciliation, Trend } from '@/lib/insights';
import { formatDate, formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, type AsyncState } from '@/lib/useApi';

const BASE = '/analytics/insights';
const PAGE = 20;
type Range = { days: number; since?: never; until?: never } | { days?: never; since: string; until: string };
type Translate = ReturnType<typeof useSession>['t'];
const courier = (name: string) => ({ steadfast: 'Steadfast', pathao: 'Pathao', redx: 'RedX' })[name] ?? name;
const pct = (bps: number | null | undefined) => bps == null ? '—' : `${(bps / 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
const change = (t: Translate, value: Comparison) => value.change_basis_points === null
  ? t('ins.noCompare') : t('ins.vsPrev', { change: `${value.change_basis_points > 0 ? '+' : ''}${pct(value.change_basis_points)}` });
const rate = (t: Translate, counts: Counts) => !counts.completed ? t('ins.notEnough')
  : counts.sufficient ? pct(counts.rto_rate_basis_points) : t('ins.limited');

function Result<T>({ state, children }: { state: AsyncState<T>; children: (data: T) => ReactNode }) {
  const { t } = useSession();
  // A new period never borrows figures from the previous request on screen.
  if (state.loading || state.refreshing) return <p role="status">{t('common.loading')}</p>;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  return state.data ? children(state.data) : null;
}

function Locked({ reason }: { reason: string | null }) {
  const { t } = useSession();
  return reason ? <p className="card__hint">{t(reason === 'PERMISSION' ? 'ins.moneyLockedRole' : 'ins.planLocked')}</p> : null;
}

function Facts({ items }: { items: Explanation[] }) {
  const { t, locale } = useSession();
  const lines = items.flatMap(({ code, params: p }) => {
    const directional = ['PROFIT_CHANGED', 'PROFIT_CHANGED_WITH_RTO', 'REVENUE_CHANGED', 'ORDERS_CHANGED', 'RECEIVED_CHANGED'].includes(code);
    const key = `ins.why.${code}${directional ? (Number(p.change_bps) < 0 ? '.down' : '.up') : ''}`;
    if (!(key in strings.en)) return [];
    const vars: Record<string, string | number> = {};
    for (const [name, value] of Object.entries(p)) vars[name] = value ?? '—';
    Object.assign(vars, {
      change: pct(typeof p.change_bps === 'number' ? Math.abs(p.change_bps) : null),
      from: pct(p.rto_from_bps as number | null), to: pct(p.rto_to_bps as number | null),
      share: pct(p.share_bps as number | null),
      courier: typeof p.provider === 'string' ? courier(p.provider) : '',
      amount: formatPaisa(p.amount_paisa as number | null, { locale }),
      courierAmount: formatPaisa(p.courier_paisa as number | null, { locale }),
      bucket: p.max_days == null ? t('ins.daysOpen', { min: p.min_days ?? '' })
        : t('ins.daysRange', { min: p.min_days ?? '', max: p.max_days }),
    });
    return [t(key as StringKey, vars)];
  });
  return lines.length ? <ul>{lines.map((line, index) => <li key={index}>{line}</li>)}</ul>
    : <p className="card__hint">{t('ins.nothingChanged')}</p>;
}

function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  const { t } = useSession();
  if (!rows.length) return <EmptyState title={t('common.nothingHere')} />;
  return <div style={{ overflowX: 'auto' }}><table className="table">
    <thead><tr>{headers.map((header, i) => <th key={i}>{header}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
  </table></div>;
}

function Pages({ offset, more, setOffset }: { offset: number; more: boolean; setOffset: (value: number) => void }) {
  const { t } = useSession();
  return <div className="toolbar">
    <button className="btn" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('common.previous')}</button>
    <button className="btn" disabled={!more} onClick={() => setOffset(offset + PAGE)}>{t('common.next')}</button>
  </div>;
}

function ProfitTrend({ data }: { data: Trend }) {
  const { t, locale } = useSession();
  const money = !data.money_locked;
  const values = data.buckets.map((b) => money ? b.profit_paisa ?? 0 : b.orders);
  const high = Math.max(1, ...values), low = Math.min(0, ...values);
  const y = (value: number) => 150 - ((value - low) / (high - low)) * 140;
  const x = (i: number) => 10 + (i / Math.max(1, values.length - 1)) * 780;
  const title = t(money ? 'ins.trendProfit' : 'ins.trendOrders');
  return <>
    <Locked reason={data.money_locked} />
    {!data.buckets.some((b) => b.orders || b.parcels) ? <p>{t('ins.trendEmpty')}</p> : <svg viewBox="0 0 800 165" role="img" aria-label={title} style={{ width: '100%', maxHeight: 220 }}>
      <title>{title}</title>
      <line x1="10" x2="790" y1={y(0)} y2={y(0)} stroke="var(--stroke)" />
      <polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(' ')} fill="none" stroke="var(--brand-ink)" strokeWidth="2" />
      {values.length === 1 ? <circle cx={x(0)} cy={y(values[0]!)} r="3" fill="var(--brand-ink)" /> : null}
    </svg>}
    <details><summary>{title} · {t('ins.open')}</summary>
      <Table headers={[t('ins.date'), t('ins.orders'), t('ins.fact.completed'), t('ins.revenue'), t('ins.profit')]}
        rows={data.buckets.map((b) => [formatDate(b.start, { locale }), b.orders, b.parcels, formatPaisa(b.revenue_paisa, { locale }), formatPaisa(b.profit_paisa, { locale })])} />
    </details>
  </>;
}

export default function InsightsPage() {
  const { t, locale } = useSession();
  const [range, setRange] = useState<Range>({ days: 30 });
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [category, setCategory] = useState('all');
  const [productOffset, setProductOffset] = useState(0);
  const [filter, setFilter] = useState('all');
  const [inventoryOffset, setInventoryOffset] = useState(0);
  const overview = useApi<Overview>(`${BASE}/overview`, range);
  const trend = useApi<Trend>(`${BASE}/trend`, range);
  const products = useApi<ProductPage>(`${BASE}/products`, { ...range, category, offset: productOffset, limit: PAGE });
  const couriers = useApi<Couriers>(`${BASE}/couriers`, range);
  const cash = useApi<Cash>(`${BASE}/cash`, range);
  const reconciliation = useApi<Reconciliation>(`${BASE}/reconciliation`, range);
  const customers = useApi<Customers>(`${BASE}/customers`, range);
  const inventory = useApi<Inventory>(`${BASE}/inventory`, { ...range, filter, offset: inventoryOffset, limit: PAGE });
  const amount = (paisa: number | null) => formatPaisa(paisa, { locale });
  const chooseRange = (value: Range) => { setRange(value); setProductOffset(0); setInventoryOffset(0); setCategory('all'); };
  const link = (href: string) => <Link className="btn btn--sm" href={href}>{t('ins.open')}</Link>;
  const stockStatus = (status: string) => status === 'OUT' || status === 'LOW'
    ? <Chip label={t(status === 'OUT' ? 'ins.status.OUT' : 'ins.status.LOW')} tone={status === 'OUT' ? 'bad' : 'warn'} /> : null;

  return <>
    <PageHeader title={t('nav.insights')} subtitle={t('ins.desktopSub')} />
    <div className="content">
      <Card>
        <div className="toolbar">{[7, 30, 90].map((days) => <button key={days} className="btn" aria-pressed={range.days === days} onClick={() => chooseRange({ days })}>{t('ins.range', { days })}</button>)}</div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); if (since && until) chooseRange({ since, until }); }}>
          <span>{t('ins.custom')}</span>
          <label className="field">{t('ins.from')}<input className="input" type="date" required value={since} max={until || undefined} onChange={(e) => setSince(e.target.value)} /></label>
          <label className="field">{t('ins.to')}<input className="input" type="date" required value={until} min={since || undefined} onChange={(e) => setUntil(e.target.value)} /></label>
          <button className="btn" type="submit">{t('ins.apply')}</button>
        </form>
      </Card>
      <Result state={overview}>{(o) => <>
        <p className="card__hint">{formatDate(o.window.since, { locale })} – {formatDate(o.window.until, { locale })} · {t('ins.previous', { from: formatDate(o.previous.since, { locale }), to: formatDate(o.previous.until, { locale }) })}</p>
        <Locked reason={o.money_locked} />
        <div className="tiles">
          {o.money && <>
            <Tile label={t('ins.revenue')} value={amount(o.money.revenue.current)} hint={change(t, o.money.revenue)} />
            <Tile label={t('ins.profit')} value={amount(o.money.profit.current)} hint={o.money.profit_quality.MISSING ? t('ins.missingCost', { count: o.money.profit_quality.MISSING }) : o.money.profit_quality.ESTIMATED ? t('ins.estimated') : change(t, o.money.profit)} />
            <Tile label={t('ins.receivable')} value={amount(o.money.receivable_paisa)} />
            <Tile label={t('ins.overdue')} value={amount(o.money.overdue_paisa)} />
            <Tile label={t('ins.discrepancies')} value={String(o.money.discrepancy_count)} hint={amount(o.money.discrepancy_paisa)} />
          </>}
          <Tile label={t('ins.orders')} value={String(o.orders.current)} hint={change(t, o.orders)} />
          <Tile label={t('ins.delivered')} value={String(o.delivered.current)} hint={change(t, o.delivered)} />
          <Tile label={t('ins.rtoRate')} value={rate(t, o.rto)} hint={t('ins.rtoOf', { rto: o.rto.rto, completed: o.rto.completed })} />
          {o.stock && <Tile label={t('ins.stock')} value={t('ins.stockValue', { low: o.stock.low_stock_items, out: o.stock.out_of_stock_items })} hint={t('ins.slowCaption', { count: o.stock.slow_moving_items, days: o.stock.slow_moving_days })} />}
        </div>
        <Card title={t('ins.whatChanged')} hint={t('ins.whatChangedSub')}><Facts items={o.explanations} /></Card>
      </>}</Result>
      <Card title={t(trend.data?.money_locked ? 'ins.trendOrders' : 'ins.trendProfit')}><Result state={trend}>{(data) => <ProfitTrend data={data} />}</Result></Card>
      <Card title={t('ins.productsTile')} hint={t('ins.productsDescription')} actions={link('/products')}>
        <Result state={products}>{(data) => <>
          <Locked reason={data.money_locked} />
          <label className="field">{t('common.filter')}<select className="select" value={category} onChange={(e) => { setCategory(e.target.value); setProductOffset(0); }}>
            {Object.entries(data.counts).filter(([key]) => `ins.cat.${key}` in strings.en).map(([key, count]) => <option key={key} value={key}>{t(`ins.cat.${key}` as StringKey)} · {count}</option>)}
          </select></label>
          <Table headers={[t('ins.productsTile'), t('ins.sku'), t('ins.fact.completed'), t('ins.delivered'), t('ins.revenue'), t('ins.profit'), t('ins.margin'), 'RTO', t('ins.units')]}
            rows={data.items.map((p) => [p.name, p.sku ?? '—', p.parcels, p.units_delivered, amount(p.revenue_paisa), p.profit_paisa === null ? t('ins.noProfit') : <>{amount(p.profit_paisa)}{p.profit_quality === 'ESTIMATED' && <p className="card__hint">{t('ins.estimated')}</p>}</>, pct(p.margin_basis_points), `${rate(t, p.rto)} · ${p.rto.rto}/${p.rto.completed}`, <>{p.stock_on_hand ?? '—'} {stockStatus(p.stock_status)}{p.slow_moving && <p className="card__hint">{t('ins.slowRow', { days: data.slow_moving_days, count: p.stock_on_hand ?? 0 })}</p>}</>])} />
          <Pages offset={productOffset} more={data.has_more} setOffset={setProductOffset} />
        </>}</Result>
      </Card>
      <Card title={t('ins.couriersTitle')} hint={t('ins.couriersDescription')} actions={link('/couriers')}>
        <Result state={couriers}>{(data) => <>
          <Locked reason={data.money_locked} />
          <Table headers={[t('ins.couriersTile'), t('ins.fact.completed'), t('ins.delivered'), 'RTO', t('ins.onRoad'), t('ins.fact.stuck'), t('ins.receivable'), t('ins.overdue'), t('ins.fact.delay'), t('ins.discrepancies')]}
            rows={data.items.map((c) => [courier(c.provider), c.counts.completed, `${c.counts.delivered} + ${c.counts.partial}`, `${rate(t, c.counts)} · ${c.counts.rto}/${c.counts.completed}`, c.in_transit_now, c.stuck_now, amount(c.outstanding_paisa), amount(c.overdue_paisa), c.payout_delay?.reliable ? t('ins.fact.delayValue', { days: c.payout_delay.median_days }) : t(c.payout_delay ? 'ins.limited' : 'ins.notEnough'), c.discrepancy_count === null ? '—' : `${c.discrepancy_count} · ${amount(c.discrepancy_paisa)}`])} />
          {data.excluded_providers.length > 0 && <p className="card__hint">{t('ins.excluded', { providers: data.excluded_providers.map(courier).join(', ') })}</p>}
        </>}</Result>
      </Card>
      <div className="grid2">
        <Card title={t('ins.cashTile')} hint={t('ins.cashDescription')} actions={link('/money')}>
          <Result state={cash}>{(data) => <>
            <Row label={t('ins.received')} value={amount(data.received.current)} />
            <p className="card__hint">{change(t, data.received)}</p>
            <Row label={t('ins.receivable')} value={amount(data.receivable_paisa)} />
            <Row label={t('ins.overdue')} value={amount(data.overdue_paisa)} />
            <Row label={t('ins.onRoad')} value={amount(data.in_transit_paisa)} />
            <Table headers={[t('ins.aging'), t('ins.fact.completed'), t('ins.receivable')]} rows={data.aging.map((a) => [a.max_days === null ? t('ins.daysOpen', { min: a.min_days }) : t('ins.daysRange', { min: a.min_days, max: a.max_days }), a.count, amount(a.amount_paisa)])} />
            <Facts items={data.explanations} />
          </>}</Result>
        </Card>
        <Card title={t('nav.reconciliation')} actions={link('/reconciliation')}>
          <Result state={reconciliation}>{(data) => <>
            <Row label={t('ins.matched')} value={data.matched} />
            <Row label={t('ins.discrepancies')} value={`${data.discrepancy_count} · ${amount(data.difference_paisa)}`} />
            <Row label={t('ins.missingCod')} value={`${data.missing_cod_count} · ${amount(data.missing_cod_paisa)}`} />
            <Row label={t('ins.chargeMismatch')} value={`${data.charge_mismatch_count} · ${amount(data.charge_mismatch_paisa)}`} />
            <Row label={t('ins.unmatched')} value={`${data.unmatched_count} · ${amount(data.unmatched_paisa)}`} />
            <Row label={t('ins.openCases')} value={`${data.open_case_count} · ${amount(data.open_case_paisa)}`} />
            <Facts items={data.explanations} />
          </>}</Result>
        </Card>
      </div>
      <Card title={t('ins.customersTile')} hint={t('ins.customersDescription')} actions={<>{link('/customers')} {link('/returns')}</>}>
        <Result state={customers}>{(data) => <>
          <div className="tiles">
            <Tile label={t('ins.activeCustomers')} value={String(data.active.current)} hint={change(t, data.active)} />
            <Tile label={t('ins.newCustomers')} value={String(data.new.current)} hint={change(t, data.new)} />
            <Tile label={t('ins.returningCustomers')} value={String(data.returning)} />
            <Tile label={t('ins.repeatShare')} value={!data.orders_with_customer ? t('ins.notEnough') : data.sufficient ? pct(data.repeat_order_rate_basis_points) : t('ins.limited')} hint={t('ins.repeatShareCaption', { repeat: data.repeat_orders, orders: data.orders_with_customer })} />
          </div>
          <h3>{t('ins.lifetime')}</h3>
          <Row label={t('ins.repeatCustomers')} value={data.repeat_customers} />
          <Row label={t('ins.multiDelivered')} value={data.multi_delivery_customers} />
          <Row label={t('ins.repeatRto')} value={data.repeat_rto_customers} />
          <Facts items={data.explanations} />
        </>}</Result>
      </Card>
      <Card title={t('ins.inventoryTile')} hint={t('ins.inventoryDescription')} actions={link('/products')}>
        <Result state={inventory}>{(data) => <>
          <div className="tiles">
            <Tile label={t('ins.units')} value={String(data.total_units)} />
            <Tile label={t('ins.lowItems')} value={String(data.low_stock_items)} />
            <Tile label={t('ins.outItems')} value={String(data.out_of_stock_items)} />
            <Tile label={t('ins.slowItems')} value={String(data.counts.slow_moving)} />
          </div>
          <Facts items={data.explanations} />
          <label className="field">{t('common.filter')}<select className="select" value={filter} onChange={(e) => { setFilter(e.target.value); setInventoryOffset(0); }}>
            {['all', 'low_stock', 'out_of_stock', 'slow_moving', 'fast_moving'].map((f) => <option key={f} value={f}>{t(`ins.cat.${f}` as StringKey)}</option>)}
          </select></label>
          <Table headers={[t('ins.productsTile'), t('ins.sku'), t('ins.units'), t('ins.booked')]} rows={data.items.map((item) => [<>{item.name}{item.variant_name ? ` · ${item.variant_name}` : ''}{item.slow_moving && <p className="card__hint">{t('ins.slowRow', { days: data.slow_moving_days, count: item.stock_on_hand })}</p>}</>, item.sku ?? '—', <>{item.stock_on_hand} {stockStatus(item.status)}</>, item.units_booked])} />
          <Pages offset={inventoryOffset} more={data.has_more} setOffset={setInventoryOffset} />
        </>}</Result>
      </Card>
    </div>
  </>;
}
