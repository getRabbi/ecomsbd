'use client';

import Link from 'next/link';

import { useCampaignLabels } from '@/components/CampaignBuilder';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Tile } from '@/components/ui';
import { statusTone, type Campaign, type Catalog } from '@/lib/campaigns';
import type { StringKey } from '@/lib/i18n';
import { formatDate } from '@/lib/money';
import { useApi } from '@/lib/useApi';

interface Overview {
  items: { purpose: string; channel: string; status: string; count: number }[];
  marketing_opt_outs: number;
  marketing_contacts: number;
}

const SENT = new Set(['SENT', 'DELIVERED', 'READ']);

export default function CampaignsPage() {
  const labels = useCampaignLabels();
  const { t, locale } = labels;
  const catalog = useApi<Catalog>('/campaigns/catalog');
  const list = useApi<{ items: Campaign[] }>('/campaigns');
  const overview = useApi<Overview>('/messaging/overview', { days: 30 });
  const items = list.data?.items ?? [];
  const sentOf = (purpose: string) =>
    (overview.data?.items ?? [])
      .filter((row) => row.purpose === purpose && SENT.has(row.status))
      .reduce((sum, row) => sum + row.count, 0);
  const manage = !!catalog.data?.can_manage;

  const table = (rows: Campaign[]) => (
    <div className="tablewrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t('cmp.col.name')}</th>
            <th>{t('cmp.col.channel')}</th>
            <th>{t('cmp.col.status')}</th>
            <th className="num">{t('cmp.col.recipients')}</th>
            <th className="num">{t('cmp.col.sent')}</th>
            <th>{t('cmp.col.when')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <Link href={`/campaigns/${row.id}`}>{row.name}</Link>
                {row.flow ? <div className="card__hint">{t(`cmp.flow.${row.flow}` as StringKey)}</div> : null}
              </td>
              <td>{t(`cmp.channel.${row.channel}` as StringKey)}</td>
              <td>
                <Chip label={labels.status(row.status)} tone={statusTone(row.status)} />
                {row.last_error ? <div className="card__hint">{labels.blocker(row.last_error)}</div> : null}
              </td>
              <td className="num">{row.total_recipients}</td>
              <td className="num">
                {Object.entries(row.messages ?? {})
                  .filter(([status]) => SENT.has(status))
                  .reduce((sum, [, count]) => sum + count, 0)}
              </td>
              <td>
                {formatDate(row.scheduled_at ?? row.started_at ?? row.created_at, { locale, withTime: true })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const oneOffs = items.filter((row) => row.kind === 'ONE_OFF');
  const flows = items.filter((row) => row.kind === 'FLOW');
  return (
    <>
      <PageHeader
        title={t('cmp.title')}
        subtitle={t('cmp.subtitle')}
        actions={
          manage ? (
            <span style={{ display: 'flex', gap: 8 }}>
              <Link className="btn" href="/campaigns/new?kind=FLOW">
                {t('cmp.newFlow')}
              </Link>
              <Link className="btn btn--primary" href="/campaigns/new">
                {t('cmp.new')}
              </Link>
            </span>
          ) : null
        }
      />
      <div className="content">
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
        {catalog.data && !manage ? <p className="card__hint">{t('cmp.readOnly')}</p> : null}
        <div className="tiles">
          <Tile label={t('cmp.monitor.contacts')} value={String(overview.data?.marketing_contacts ?? '—')} />
          <Tile label={t('cmp.monitor.marketing')} value={String(sentOf('MARKETING'))} hint={t('cmp.monitor.title')} />
          <Tile label={t('cmp.monitor.transactional')} value={String(sentOf('TRANSACTIONAL'))} hint={t('cmp.monitor.title')} />
          <Tile label={t('cmp.monitor.optOuts')} value={String(overview.data?.marketing_opt_outs ?? '—')} hint={t('cmp.monitor.title')} />
        </div>
        <Card title={t('cmp.oneOffs')} padded={false}>
          {oneOffs.length === 0 && !list.loading ? (
            <EmptyState title={t('cmp.empty')} hint={t('cmp.emptyHint')} />
          ) : (
            table(oneOffs)
          )}
        </Card>
        <Card title={t('cmp.flows')} hint={t('cmp.flowsHint')} padded={false}>
          {flows.length === 0 && !list.loading ? <EmptyState title={t('cmp.empty')} /> : table(flows)}
        </Card>
      </div>
    </>
  );
}
