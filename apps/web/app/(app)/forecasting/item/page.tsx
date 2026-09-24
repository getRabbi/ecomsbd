'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState, Row, Tile } from '@/components/ui';
import { itemLabel, type ItemDetail } from '@/lib/forecasting';
import type { StringKey } from '@/lib/i18n';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function ForecastItemPage() {
  return (
    <Suspense>
      <ForecastItem />
    </Suspense>
  );
}

/** One bar per completed day. Days that were not counted are drawn as a
 *  hatched band, named in the legend: never colour alone. */
function DemandChart({ history, title, soldLabel, outLabel, locale }: {
  history: ItemDetail['history'];
  title: string;
  soldLabel: string;
  outLabel: string;
  locale: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 800;
  const height = 180;
  const top = 12;
  const bottom = 20;
  const max = Math.max(1, ...history.map((d) => d.units));
  const slot = width / history.length;
  const bar = Math.max(2, slot - 2); // 2px surface gap between bars
  const y = (units: number) => top + (height - top - bottom) * (1 - units / max);
  const base = height - bottom;
  const shown = hover === null ? null : history[hover];
  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title} style={{ width: '100%', maxHeight: 240 }} onMouseLeave={() => setHover(null)}>
        <defs>
          <pattern id="fc-out" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--stroke)" strokeWidth="2" />
          </pattern>
        </defs>
        {history.map((day, i) =>
          day.in_stock ? null : <rect key={`o${day.date}`} x={i * slot} y={top} width={slot} height={base - top} fill="url(#fc-out)" />,
        )}
        <line x1="0" x2={width} y1={base} y2={base} stroke="var(--stroke)" />
        <text x="2" y={top - 2} fontSize="10" fill="currentColor" opacity="0.7">{max}</text>
        {history.map((day, i) =>
          day.units > 0 ? (
            <rect key={day.date} x={i * slot + (slot - bar) / 2} y={y(day.units)} width={bar} height={base - y(day.units)} rx={Math.min(4, bar / 2)} fill="var(--brand-ink)" opacity={hover === null || hover === i ? 1 : 0.55} />
          ) : null,
        )}
        {history.map((day, i) => (
          <rect key={`h${day.date}`} x={i * slot} y={0} width={slot} height={height} fill="transparent" onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)}>
            <title>{`${formatDate(day.date, { locale })}: ${day.units}`}</title>
          </rect>
        ))}
        <text x="2" y={height - 4} fontSize="10" fill="currentColor" opacity="0.7">{formatDate(history[0]?.date, { locale })}</text>
        <text x={width - 2} y={height - 4} fontSize="10" fill="currentColor" opacity="0.7" textAnchor="end">{formatDate(history[history.length - 1]?.date, { locale })}</text>
      </svg>
      <figcaption style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13 }}>
        <span><span aria-hidden style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: 'var(--brand-ink)', marginRight: 6 }} />{soldLabel}</span>
        <span><svg aria-hidden width="12" height="12" style={{ marginRight: 6, verticalAlign: 'middle' }}><rect width="12" height="12" fill="url(#fc-out)" stroke="var(--stroke)" /></svg>{outLabel}</span>
        <span role="status" aria-live="polite">{shown ? `${formatDate(shown.date, { locale })}: ${shown.units}` : ''}</span>
      </figcaption>
    </figure>
  );
}

function ForecastItem() {
  const { t, locale } = useSession();
  const params = useSearchParams();
  const productId = params.get('product_id');
  const variantId = params.get('variant_id');
  const detail = useApi<ItemDetail>(productId ? '/forecasting/demand/item' : null, {
    product_id: productId,
    variant_id: variantId,
  });
  const item = detail.data;
  if (!item) {
    return <div className="content">{detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}</div>;
  }
  const insufficient = item.confidence === 'INSUFFICIENT';
  return (
    <>
      <PageHeader
        title={itemLabel(item)}
        subtitle={item.supplier_name ?? undefined}
        actions={<Chip label={t(`fc.conf.${item.confidence}` as StringKey)} tone={item.confidence === 'HIGH' ? 'good' : insufficient ? 'warn' : 'neutral'} />}
      />
      <div className="content">
        <Link href="/forecasting">{t('fc.item.back')}</Link>
        <div className="tiles">
          <Tile label={t('fc.col.rate')} value={item.rate_per_day == null ? '—' : String(item.rate_per_day)} />
          <Tile label={t('fc.col.onHand')} value={String(item.on_hand)} hint={item.incoming ? `${t('fc.col.incoming')}: ${item.incoming}` : undefined} />
          <Tile label={t('fc.col.runsOut')} value={item.stockout_on ? (item.days_of_cover === 0 ? t('fc.today') : formatDate(item.stockout_on, { locale })) : '—'} />
          <Tile label={t('fc.col.suggested')} value={item.suggested_quantity == null ? '—' : String(item.suggested_quantity)} />
        </div>
        <Card title={t('fc.item.chart')}>
          <DemandChart history={item.history} title={t('fc.item.chart')} soldLabel={t('fc.item.legendSold')} outLabel={t('fc.item.legendOut')} locale={locale} />
          <details>
            <summary>{t('fc.item.table')}</summary>
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr><th>{t('fc.item.date')}</th><th className="num">{t('fc.item.units')}</th><th>{t('fc.item.inStock')}</th></tr>
                </thead>
                <tbody>
                  {item.history.map((day) => (
                    <tr key={day.date}>
                      <td>{formatDate(day.date, { locale })}</td>
                      <td className="num">{day.units}</td>
                      <td>{day.in_stock ? t('fc.item.yes') : t('fc.item.no')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </Card>
        <Card title={t('fc.how.title')}>
          {insufficient ? (
            <p>{t('fc.how.insufficient', { days: item.method.min_in_stock_days, units: item.method.min_units })}</p>
          ) : (
            <>
              <Row label={t('fc.how.days', { n: item.in_stock_days, total: item.method.history_days })} value="" />
              <Row label={t('fc.how.units', { n: item.units_sold })} value="" />
              <Row label={t('fc.how.recent', { rate: item.recent_rate_per_day ?? 0 })} value="" />
              <Row label={t('fc.how.long', { rate: item.long_rate_per_day ?? 0 })} value="" />
              <Row label={t('fc.how.rate', { rate: item.rate_per_day ?? 0 })} value="" />
              <Row label={t('fc.how.safety', { n: item.safety_stock ?? 0 })} value="" />
              <Row label={t('fc.how.reorderPoint', { n: item.reorder_point ?? 0 })} value="" />
              <Row label={t('fc.how.suggested', { cover: item.cover_days, n: item.suggested_quantity ?? 0 })} value="" />
            </>
          )}
          <Row
            label={t('fc.col.lead')}
            value={`${t('fc.days', { n: item.lead_time_days })} · ${t(`fc.lead.${item.lead_time_source}` as StringKey, { n: item.lead_time_samples })}`}
          />
        </Card>
      </div>
    </>
  );
}
