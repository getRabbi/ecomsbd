'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatPaisa } from '@/lib/money';
import { cellValue, when, type Cell, type CourierFacts, type Release } from '@/lib/risk';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Tab = 'overview' | 'cohorts' | 'couriers';
type Dimension = 'COURIER' | 'CATEGORY' | 'VOLUME_BAND';

interface Summary {
  opted_in: boolean;
  minimum_shops: number;
  minimum_sample: number;
  benchmarks: Release;
}

/** Network intelligence (V3.7): anonymous cohorts next to the shop's own facts. */
export default function NetworkPage() {
  const { t } = useSession();
  const [tab, setTab] = useState<Tab>('overview');
  const summary = useApi<Summary>('/network-intelligence');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await api.patch('/network-intelligence/preference', { opted_in: !summary.data?.opted_in });
      summary.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title={t('net.title')} subtitle={t('net.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        <div role="tablist" style={{ display: 'flex', gap: 6 }}>
          {(['overview', 'cohorts', 'couriers'] as const).map((key) => (
            <button key={key} role="tab" aria-selected={tab === key} className={`btn btn--sm${tab === key ? ' btn--primary' : ''}`} onClick={() => setTab(key)}>
              {t(`net.tab.${key}`)}
            </button>
          ))}
        </div>
        {error || summary.error ? <ErrorState error={error || summary.error} onRetry={summary.reload} /> : null}
        {tab === 'overview' ? (
          <>
            <Card>
              <p>{summary.data?.opted_in ? t('net.optedIn') : t('net.notOptedIn')}</p>
              <p className="card__hint">{t('net.consentNote')}</p>
              <button className="btn" disabled={busy || !summary.data} onClick={() => void toggle()}>
                {summary.data?.opted_in ? t('net.optOut') : t('net.consent')}
              </button>
            </Card>
            <ReleaseTable release={summary.data?.benchmarks ?? null} />
          </>
        ) : null}
        {tab === 'cohorts' ? <Cohorts /> : null}
        {tab === 'couriers' ? <Couriers /> : null}
        <Card title={t('net.quality.title')}>
          <p className="card__hint">
            {t('net.quality.body', {
              shops: summary.data?.minimum_shops ?? 20,
              sample: summary.data?.minimum_sample ?? 200,
              share: summary.data?.benchmarks.cohort_definition?.max_share_percent ?? 10,
            })}
          </p>
          <p className="card__hint">{t('net.quality.freshness')}</p>
        </Card>
      </div>
    </>
  );
}

function ReleaseTable({ release, title }: { release: Release | null; title?: string }) {
  const { t, locale } = useSession();
  if (!release) return null;
  if (!release.period) return <Card title={title}><p className="card__hint">{t('net.noRelease')}</p></Card>;
  return (
    <Card title={title} hint={`${t('net.period')}: ${release.period} (UTC) · ${t('net.computed', { when: when(release.computed_at, locale) })}`} padded={false}>
      <table className="table">
        <thead>
          <tr>
            <th>{t('net.metric')}</th>
            <th>{t('net.cohort')}</th>
            <th>{t('net.value')}</th>
            <th>{t('net.shops')}</th>
            <th>{t('net.sample')}</th>
          </tr>
        </thead>
        <tbody>
          {release.cells.map((cell) => (
            <tr key={`${cell.metric}-${cell.dimension}-${cell.cohort}`}>
              <td>{t(`net.metric.${cell.metric}` as StringKey)}</td>
              <td>{cohortLabel(cell, t)}</td>
              <td>{cell.status === 'PUBLISHED' ? cellValue(cell, t) : <Chip label={t('net.insufficient')} />}</td>
              <td>{cell.shops_band ?? '—'}</td>
              <td>{cell.sample_band ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function cohortLabel(cell: Cell, t: (key: StringKey) => string): string {
  if (cell.dimension === 'COURIER') return cell.cohort.charAt(0).toUpperCase() + cell.cohort.slice(1);
  if (cell.dimension === 'CATEGORY') return cell.cohort.replaceAll('_', ' ').toLowerCase();
  return t(`net.cohort.${cell.cohort}` as StringKey);
}

function Cohorts() {
  const { t } = useSession();
  const [dimension, setDimension] = useState<Dimension>('COURIER');
  const release = useApi<Release>('/network-intelligence/benchmarks', { dimension });
  return (
    <>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span>{t('net.dimension')}</span>
        <select className="select" value={dimension} onChange={(e) => setDimension(e.target.value as Dimension)}>
          {(['COURIER', 'CATEGORY', 'VOLUME_BAND'] as const).map((d) => (
            <option key={d} value={d}>{t(`net.dim.${d}`)}</option>
          ))}
        </select>
      </label>
      {release.error ? <ErrorState error={release.error} onRetry={release.reload} /> : null}
      <ReleaseTable release={release.data} title={t(`net.dim.${dimension}`)} />
    </>
  );
}

function Couriers() {
  const { t } = useSession();
  const result = useApi<{ own_shop: { window_days: number; stuck_after_days: number; overdue_after_days: number; couriers: CourierFacts[] }; network: Release }>('/network-intelligence/couriers');
  if (result.error) return <ErrorState error={result.error} onRetry={result.reload} />;
  const own = result.data?.own_shop;
  if (!own) return <p className="card__hint">{t('common.loading')}</p>;
  const pct = (bps: number | null) => (bps === null ? '—' : `${(bps / 100).toFixed(1)}%`);
  return (
    <>
      <Card title={t('net.cour.title', { days: own.window_days })} hint={t('net.cour.hint')} padded={false}>
        {own.couriers.length === 0 ? (
          <div className="card__body"><p className="card__hint">{t('net.cour.empty')}</p></div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t('net.cour.courier')}</th>
                <th>{t('net.cour.completed')}</th>
                <th>{t('net.cour.deliveryRate')}</th>
                <th>{t('net.cour.rto')}</th>
                <th>{t('net.cour.hours')}</th>
                <th>{t('net.cour.stuck', { days: own.stuck_after_days })}</th>
                <th>{t('net.cour.payout')}</th>
                <th>{t('net.cour.outstanding')}</th>
                <th>{t('net.cour.overdue', { days: own.overdue_after_days })}</th>
                <th>{t('net.cour.mismatch')}</th>
                <th>{t('net.cour.charge')}</th>
              </tr>
            </thead>
            <tbody>
              {own.couriers.map((c) => (
                <tr key={c.courier}>
                  <td>
                    {c.courier.charAt(0).toUpperCase() + c.courier.slice(1)}
                    {c.live_integration ? null : <div className="table__sub">{t('net.cour.notLive')}</div>}
                  </td>
                  <td>{c.completed_parcels}</td>
                  <td>{pct(c.delivery_rate_bps)}</td>
                  <td>{c.rto_parcels} ({pct(c.rto_rate_bps)})</td>
                  <td>{c.median_delivery_hours === null ? t('net.small') : t('net.unit.hours', { n: c.median_delivery_hours })}</td>
                  <td>{c.stuck_parcels} / {c.in_transit_parcels}</td>
                  <td>{c.median_payout_delay_days === null ? t('net.small') : t('net.unit.days', { n: c.median_payout_delay_days })}</td>
                  <td>{formatPaisa(c.outstanding_cod_paisa)}</td>
                  <td>{formatPaisa(c.overdue_cod_paisa)}</td>
                  <td>{c.reconciliation_mismatches} / {c.reconciliation_items}</td>
                  <td>{c.median_delivery_charge_paisa === null ? t('net.small') : formatPaisa(c.median_delivery_charge_paisa)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <ReleaseTable release={result.data?.network ?? null} title={t('net.cour.network')} />
    </>
  );
}
