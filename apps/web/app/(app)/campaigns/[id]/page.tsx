'use client';

import Link from 'next/link';
import { use, useEffect, useState } from 'react';

import { CampaignBuilder, useCampaignLabels } from '@/components/CampaignBuilder';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Row, Tile } from '@/components/ui';
import { api } from '@/lib/api';
import { statusTone, type Catalog, type Detail, type Estimate, type Recipient } from '@/lib/campaigns';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { useApi } from '@/lib/useApi';

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <CampaignDetail key={id} id={id} />;
}

function CampaignDetail({ id }: { id: string }) {
  const labels = useCampaignLabels();
  const { t, locale } = labels;
  const detail = useApi<Detail>(`/campaigns/${id}`);
  const catalog = useApi<Catalog>('/campaigns/catalog');
  const [filter, setFilter] = useState<'' | 'PENDING' | 'QUEUED' | 'SKIPPED'>('');
  const recipients = useApi<{ items: Recipient[] }>(`/campaigns/${id}/recipients`, { status: filter || undefined });
  const [editing, setEditing] = useState(false);
  const [when, setWhen] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const data = detail.data;
  const campaign = data?.campaign;
  const analytics = data?.analytics;
  const manage = !!catalog.data?.can_manage;
  const canPause = !!catalog.data?.can_pause;
  const live = campaign?.status === 'SENDING' || campaign?.status === 'ACTIVE';
  const reload = detail.reload;
  const reloadRecipients = recipients.reload;

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => {
      reload();
      reloadRecipients();
    }, 10_000);
    return () => clearInterval(timer);
  }, [live, reload, reloadRecipients]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      detail.reload();
      recipients.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  if (!campaign || !analytics) {
    return (
      <div className="content">
        {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      </div>
    );
  }

  const editable =
    manage &&
    (campaign.status === 'DRAFT' ||
      campaign.status === 'SCHEDULED' ||
      (campaign.kind === 'FLOW' && campaign.status === 'PAUSED'));
  const launch = (scheduledAt?: string) =>
    act(async () => {
      if (!scheduledAt && campaign.kind === 'ONE_OFF') {
        // Confirm against who can actually receive it right now, not a guess.
        const { money_allowed: _unused, ...audience } = campaign.audience as Record<string, unknown>;
        void _unused;
        const estimate = await api.post<Estimate>('/campaigns/estimate', { channel: campaign.channel, audience });
        if (!window.confirm(t('cmp.launchConfirm', { count: estimate.reachable }))) return;
      }
      await api.post(`/campaigns/${id}/launch`, {
        version: campaign.version,
        ...(scheduledAt ? { scheduled_at: new Date(scheduledAt).toISOString() } : {}),
      });
    });
  const problem = labels.problem(error);
  const notReported = t('cmp.a.notReported');

  return (
    <>
      <PageHeader
        title={campaign.name}
        subtitle={`${t(`cmp.kind.${campaign.kind}` as StringKey)} · ${t(`cmp.channel.${campaign.channel}` as StringKey)}${
          campaign.flow ? ` · ${t(`cmp.flow.${campaign.flow}` as StringKey)}` : ''
        }`}
        actions={
          <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Chip label={labels.status(campaign.status)} tone={statusTone(campaign.status)} />
            {canPause && ['SCHEDULED', 'SENDING', 'ACTIVE'].includes(campaign.status) ? (
              <button type="button" className="btn" disabled={busy} onClick={() => void act(() => api.post(`/campaigns/${id}/pause`))}>
                {t('cmp.pause')}
              </button>
            ) : null}
            {manage && campaign.status === 'PAUSED' ? (
              <button type="button" className="btn" disabled={busy} onClick={() => void act(() => api.post(`/campaigns/${id}/resume`))}>
                {t('cmp.resume')}
              </button>
            ) : null}
            {manage && !['COMPLETED', 'CANCELLED'].includes(campaign.status) ? (
              <button
                type="button"
                className="btn btn--ghost"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(t('cmp.cancelConfirm'))) void act(() => api.post(`/campaigns/${id}/cancel`));
                }}
              >
                {t('cmp.cancel')}
              </button>
            ) : null}
          </span>
        }
      />
      <div className="content">
        <Link href="/campaigns">← {t('cmp.back')}</Link>
        {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
        {data.blocker && !['COMPLETED', 'CANCELLED'].includes(campaign.status) ? (
          <Card title={t('cmp.blocked')} hint={labels.blocker(data.blocker)} />
        ) : null}
        {campaign.last_error ? <Card title={labels.status('PAUSED')} hint={labels.blocker(campaign.last_error)} /> : null}

        {manage && ['DRAFT', 'SCHEDULED'].includes(campaign.status) && !editing ? (
          <Card title={campaign.kind === 'FLOW' ? t('cmp.activate') : t('cmp.launch')}>
            {campaign.kind === 'FLOW' ? (
              <button type="button" className="btn btn--primary" disabled={busy || !!data.blocker} onClick={() => void launch()}>
                {t('cmp.activate')}
              </button>
            ) : (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy || !!data.blocker}
                  onClick={() => void launch()}
                >
                  {t('cmp.launch')}
                </button>
                <label className="field">
                  <span className="field__label">{t('cmp.scheduleAt')}</span>
                  <input className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
                </label>
                <button type="button" className="btn" disabled={busy || !when || !!data.blocker} onClick={() => void launch(when)}>
                  {t('cmp.schedule')}
                </button>
              </div>
            )}
            {campaign.scheduled_at ? (
              <p className="card__hint">
                {t('cmp.status.SCHEDULED')}: {formatDate(campaign.scheduled_at, { locale, withTime: true })}
              </p>
            ) : null}
          </Card>
        ) : null}

        {editable ? (
          editing ? (
            <CampaignBuilder
              initial={campaign}
              kind={campaign.kind}
              onSaved={() => {
                setEditing(false);
                detail.reload();
              }}
            />
          ) : (
            <p>
              <button type="button" className="btn" onClick={() => setEditing(true)}>
                {t('cmp.edit')}
              </button>
            </p>
          )
        ) : null}

        <div className="tiles">
          <Tile label={t('cmp.a.recipients')} value={String(analytics.recipients.total)} />
          <Tile label={t('cmp.a.sent')} value={String(analytics.sent)} />
          <Tile label={t('cmp.a.delivered')} value={analytics.delivered === null ? '—' : String(analytics.delivered)} hint={analytics.delivered === null ? notReported : undefined} />
          <Tile label={t('cmp.a.read')} value={analytics.read === null ? '—' : String(analytics.read)} hint={analytics.read === null ? notReported : undefined} />
          <Tile label={t('cmp.a.failed')} value={String(analytics.failed)} />
          <Tile label={t('cmp.a.optOuts')} value={String(analytics.opt_outs)} />
        </div>

        <div className="grid2">
          <Card title={t('cmp.a.skipped')}>
            <Row label={t('cmp.a.pending')} value={String(analytics.recipients.PENDING)} />
            {Object.entries(analytics.recipients.skipped_by).map(([reason, count]) => (
              <Row key={reason} label={labels.skip(reason)} value={String(count)} />
            ))}
            {analytics.errors.length > 0 ? <strong style={{ display: 'block', marginTop: 10 }}>{t('cmp.a.errors')}</strong> : null}
            {analytics.errors.map((row) => (
              <Row key={row.code} label={labels.blocker(row.code) || row.code} value={String(row.count)} />
            ))}
          </Card>
          <Card title={t('cmp.a.ordersAfter', { days: analytics.orders_after.window_days })} hint={t('cmp.a.ordersAfterHint')}>
            <Row label={t('cmp.a.buyers')} value={String(analytics.orders_after.customers)} />
            <Row label={t('cmp.a.orders')} value={String(analytics.orders_after.orders)} />
            {analytics.orders_after.order_value_paisa !== null ? (
              <Row label={t('cmp.a.value')} value={formatPaisa(analytics.orders_after.order_value_paisa, { locale })} />
            ) : null}
          </Card>
        </div>

        <Card
          title={t('cmp.r.title')}
          padded={false}
          actions={
            <select className="select" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
              <option value="">—</option>
              {(['PENDING', 'QUEUED', 'SKIPPED'] as const).map((value) => (
                <option key={value} value={value}>
                  {t(`cmp.r.status.${value}` as StringKey)}
                </option>
              ))}
            </select>
          }
        >
          {recipients.data?.items.length === 0 ? (
            <EmptyState title={t('cmp.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <tbody>
                  {recipients.data?.items.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <Link href={`/customers/${row.customer_id}`}>{row.customer_name || row.phone_masked}</Link>
                      </td>
                      <td>{t(`cmp.r.status.${row.status}` as StringKey)}</td>
                      <td>
                        {row.skip_reason
                          ? labels.skip(row.skip_reason)
                          : row.message_status
                            ? labels.message(row.message_status)
                            : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
