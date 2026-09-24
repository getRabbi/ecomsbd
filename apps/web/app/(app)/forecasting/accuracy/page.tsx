'use client';

import { ForecastTabs } from '@/components/ForecastTabs';
import { PageHeader } from '@/components/shell';
import { Card, ErrorState, Row } from '@/components/ui';
import { percent, type Accuracy } from '@/lib/forecasting';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function ForecastAccuracyPage() {
  const { t, locale } = useSession();
  const accuracy = useApi<Accuracy>('/forecasting/accuracy');
  const data = accuracy.data;
  return (
    <>
      <PageHeader title={t('fc.title')} subtitle={t('fc.subtitle')} />
      <div className="content">
        <ForecastTabs />
        {accuracy.error ? <ErrorState error={accuracy.error} onRetry={accuracy.reload} /> : null}
        {data ? (
          <Card title={t('fc.acc.title')} hint={t('fc.acc.hint')}>
            {data.status === 'NOT_ENOUGH_HISTORY' ? <p>{t('fc.acc.notEnough')}</p> : null}
            {data.status === 'NO_SALES' ? <p>{t('fc.acc.noSales')}</p> : null}
            {data.status !== 'NOT_ENOUGH_HISTORY' ? (
              <>
                <Row label={t('fc.acc.asOf')} value={formatDate(data.as_of, { locale })} />
                <Row label={t('fc.acc.items')} value={String(data.items)} />
                <Row label={t('fc.acc.skipped')} value={String(data.skipped ?? 0)} />
                <Row label={t('fc.acc.predicted')} value={String(data.predicted_units ?? 0)} />
                <Row label={t('fc.acc.actual')} value={String(data.actual_units ?? 0)} />
                {data.error_bps != null ? <Row label={t('fc.acc.error')} value={percent(data.error_bps)} /> : null}
                {data.bias_units ? (
                  <p className="card__hint">
                    {data.bias_units > 0 ? t('fc.acc.over', { n: data.bias_units }) : t('fc.acc.under', { n: -data.bias_units })}
                  </p>
                ) : null}
              </>
            ) : null}
          </Card>
        ) : null}
      </div>
    </>
  );
}
