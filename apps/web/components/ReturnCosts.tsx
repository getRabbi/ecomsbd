'use client';

import { Card, Chip, ErrorState, Row } from '@/components/ui';
import type { StringKey } from '@/lib/i18n';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface ReturnsReport {
  since: string;
  until: string;
  parcel_count: number;
  return_count: number;
  return_rate_basis_points: number | null;
  direct_loss_paisa: number;
  outward_delivery_cost_paisa: number;
  return_delivery_cost_paisa: number;
  packaging_loss_paisa: number;
  write_off_paisa: number;
  by_reason: Record<string, number>;
  unknown_reason_count: number;
}

function sinceFor(days: number): string {
  const date = new Date(Date.now() - days * 86_400_000);
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Dhaka' }).format(date);
}

/**
 * What returns cost, from the profit engine (`/analytics/returns`): direct
 * loss, the outward and return courier charges, packaging and write-offs, and
 * why parcels came back — with the parcels nobody recorded a reason for
 * counted, not hidden. Every figure is the API's.
 */
export function ReturnCosts({ days }: { days: number }) {
  const { t, locale } = useSession();
  const report = useApi<ReturnsReport>('/analytics/returns', { since: sinceFor(days) });
  const data = report.data;

  if (report.error) {
    return (
      <Card title={t('rc2.title')}>
        <ErrorState error={report.error} onRetry={report.reload} />
      </Card>
    );
  }
  if (!data) return null;

  const reasons = Object.entries(data.by_reason).sort((a, b) => b[1] - a[1]);

  return (
    <div className="grid2" style={{ marginTop: 18 }}>
      <Card title={t('rc2.title')} hint={t('rc2.hint', { days })}>
        <Row label={t('rc2.returns')} value={`${data.return_count} / ${data.parcel_count}${data.return_rate_basis_points !== null ? ` · ${(data.return_rate_basis_points / 100).toFixed(1)}%` : ''}`} />
        <Row label={<strong>{t('rc2.directLoss')}</strong>} value={<strong>{formatPaisa(data.direct_loss_paisa, { locale })}</strong>} />
        <Row label={t('rc2.outward')} value={formatPaisa(data.outward_delivery_cost_paisa, { locale })} />
        <Row label={t('rc2.returnCharge')} value={formatPaisa(data.return_delivery_cost_paisa, { locale })} />
        <Row label={t('rc2.packaging')} value={formatPaisa(data.packaging_loss_paisa, { locale })} />
        <Row label={t('rc2.writeOff')} value={formatPaisa(data.write_off_paisa, { locale })} />
        <p className="card__hint">{t('rc2.restockNote')}</p>
      </Card>
      <Card title={t('rc2.reasons')}>
        {reasons.length === 0 ? <p className="card__hint">{t('rc2.noReasons')}</p> : null}
        {reasons.map(([reason, count]) => {
          const key = `od.reason.${reason}` as StringKey;
          return <Row key={reason} label={t(key) === key ? reason : t(key)} value={count} />;
        })}
        {data.unknown_reason_count > 0 ? (
          <Row label={t('rc2.missing')} value={<Chip label={String(data.unknown_reason_count)} tone="warn" />} />
        ) : null}
      </Card>
    </div>
  );
}
