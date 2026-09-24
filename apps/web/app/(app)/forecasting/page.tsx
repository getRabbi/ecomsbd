'use client';

import Link from 'next/link';
import { useState } from 'react';

import { ForecastTabs } from '@/components/ForecastTabs';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Tile } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { itemHref, itemLabel, percent, type Accuracy, type Confidence, type DemandList, type ItemForecast } from '@/lib/forecasting';
import { strings, type StringKey } from '@/lib/i18n';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

const FILTERS = ['all', 'at_risk', 'insufficient'] as const;

function tone(confidence: Confidence): 'good' | 'neutral' | 'warn' {
  return confidence === 'HIGH' ? 'good' : confidence === 'MEDIUM' ? 'neutral' : 'warn';
}

export default function ForecastsPage() {
  const { t, locale } = useSession();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('at_risk');
  const [cover, setCover] = useState(14);
  const list = useApi<DemandList>('/forecasting/demand', { filter, cover_days: cover });
  const accuracy = useApi<Accuracy>('/forecasting/accuracy');
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; href?: string; bad?: boolean } | null>(null);

  const data = list.data;
  const items = data?.items ?? [];

  async function draft(item: ItemForecast) {
    const key = `${item.product_id}:${item.variant_id ?? '-'}`;
    setBusy(key);
    setMessage(null);
    try {
      const made = await api.post<{ purchase_order_id: string; number: string; quantity: number }>(
        '/forecasting/draft-purchase-order',
        { product_id: item.product_id, variant_id: item.variant_id },
      );
      setMessage({
        text: t('fc.drafted', { number: made.number, quantity: made.quantity }),
        href: `/procurement/${made.purchase_order_id}`,
      });
      list.reload();
    } catch (failure) {
      const code = failure instanceof ApiError ? failure.details?.code : null;
      const known = typeof code === 'string' && `fc.err.${code}` in strings.en;
      setMessage({
        text: known ? t(`fc.err.${code}` as StringKey) : failure instanceof Error ? failure.message : String(failure),
        bad: true,
      });
    } finally {
      setBusy(null);
    }
  }

  const acc = accuracy.data;
  return (
    <>
      <PageHeader title={t('fc.title')} subtitle={t('fc.subtitle')} />
      <div className="content">
        <ForecastTabs />
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
        {data ? (
          <div className="tiles">
            <Tile label={t('fc.tile.atRisk')} value={String(data.counts.at_risk)} hint={t('fc.tile.atRiskHint')} />
            <Tile label={t('fc.tile.insufficient')} value={String(data.counts.insufficient)} hint={t('fc.tile.insufficientHint')} />
            <Tile
              label={t('fc.tile.accuracy')}
              value={acc?.status === 'SCORED' && acc.error_bps != null ? percent(acc.error_bps) : '—'}
              hint={acc?.status === 'SCORED' ? undefined : t('fc.tile.accuracyNone')}
            />
          </div>
        ) : null}
        {data && !data.can_draft ? <p className="card__hint">{t('fc.readOnly')}</p> : null}
        {message ? (
          <p role="status" className={message.bad ? 'formerror' : 'text--good'}>
            {message.text} {message.href ? <Link href={message.href}>{t('fc.openDraft')}</Link> : null}
          </p>
        ) : null}
        <Card
          title={t('fc.tab.reorder')}
          padded={false}
          actions={
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <select className="select" value={filter} onChange={(e) => setFilter(e.target.value as (typeof FILTERS)[number])} aria-label={t('fc.col.item')}>
                {FILTERS.map((f) => (
                  <option key={f} value={f}>
                    {t(`fc.filter.${f}` as StringKey)}
                    {data ? ` (${data.counts[f]})` : ''}
                  </option>
                ))}
              </select>
              <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span className="card__hint">{t('fc.coverDays')}</span>
                <select className="select" value={cover} onChange={(e) => setCover(Number(e.target.value))}>
                  {[7, 14, 30, 45, 60].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
            </div>
          }
        >
          {items.length === 0 && !list.loading ? (
            <EmptyState title={t('fc.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('fc.col.item')}</th>
                    <th>{t('fc.col.confidence')}</th>
                    <th className="num">{t('fc.col.rate')}</th>
                    <th className="num">{t('fc.col.onHand')}</th>
                    <th className="num">{t('fc.col.incoming')}</th>
                    <th>{t('fc.col.lead')}</th>
                    <th>{t('fc.col.runsOut')}</th>
                    <th className="num">{t('fc.col.suggested')}</th>
                    {data?.can_draft ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const key = `${item.product_id}:${item.variant_id ?? '-'}`;
                    return (
                      <tr key={key}>
                        <td>
                          <Link href={itemHref(item)}>{itemLabel(item)}</Link>
                          {item.at_risk ? <> <Chip label={t('fc.filter.at_risk')} tone="bad" /></> : null}
                        </td>
                        <td><Chip label={t(`fc.conf.${item.confidence}` as StringKey)} tone={tone(item.confidence)} /></td>
                        <td className="num">{item.rate_per_day ?? '—'}</td>
                        <td className="num">{item.on_hand}</td>
                        <td className="num">{item.incoming}</td>
                        <td>
                          {t('fc.days', { n: item.lead_time_days })}
                          <div className="card__hint">{t(`fc.lead.${item.lead_time_source}` as StringKey, { n: item.lead_time_samples })}</div>
                        </td>
                        <td>{item.stockout_on ? (item.days_of_cover === 0 ? t('fc.today') : formatDate(item.stockout_on, { locale })) : '—'}</td>
                        <td className="num">{item.suggested_quantity ?? '—'}</td>
                        {data?.can_draft ? (
                          <td>
                            {item.suggested_quantity ? (
                              <button type="button" className="btn btn--sm" disabled={busy !== null} onClick={() => void draft(item)}>
                                {busy === key ? '…' : t('fc.draft')}
                              </button>
                            ) : null}
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
