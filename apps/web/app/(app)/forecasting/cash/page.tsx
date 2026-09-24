'use client';

import { ForecastTabs } from '@/components/ForecastTabs';
import { PageHeader } from '@/components/shell';
import { Card, ErrorState, Tile } from '@/components/ui';
import { ApiError } from '@/lib/api';
import type { CashOutlook } from '@/lib/forecasting';
import type { StringKey } from '@/lib/i18n';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

const WINDOWS: { key: StringKey; inflow: string | null; outflow: keyof CashOutlook['outflow'] }[] = [
  { key: 'fc.cash.overdue', inflow: null, outflow: 'overdue' },
  { key: 'fc.cash.next7', inflow: 'next_7_days', outflow: 'next_7_days' },
  { key: 'fc.cash.next14', inflow: 'days_8_to_14', outflow: 'days_8_to_14' },
  { key: 'fc.cash.later', inflow: 'later', outflow: 'later' },
  { key: 'fc.cash.noDate', inflow: null, outflow: 'no_due_date' },
];

export default function CashOutlookPage() {
  const { t, locale } = useSession();
  const cash = useApi<CashOutlook>('/forecasting/cash');
  const data = cash.data;
  const forbidden = cash.error instanceof ApiError && cash.error.status === 403;
  const money = (paisa: number) => formatPaisa(paisa, { locale });
  return (
    <>
      <PageHeader title={t('fc.title')} subtitle={t('fc.subtitle')} />
      <div className="content">
        <ForecastTabs />
        {forbidden ? <p className="card__hint">{t('fc.cash.noAccess')}</p> : cash.error ? <ErrorState error={cash.error} onRetry={cash.reload} /> : null}
        {data ? (
          <>
            <div className="tiles">
              <Tile label={t('fc.cash.net7')} value={money(data.net_7_days_paisa)} />
              <Tile label={t('fc.cash.net14')} value={money(data.net_14_days_paisa)} />
              <Tile label={t('fc.cash.committed')} value={money(data.committed_on_open_orders_paisa)} />
              <Tile label={t('fc.cash.unplaced')} value={money(data.unplaced_inflow_paisa)} />
            </div>
            <Card title={t('fc.cash.title')} hint={t('fc.cash.hint')} padded={false}>
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('fc.cash.window')}</th>
                      <th className="num">{t('fc.cash.in')}</th>
                      <th className="num">{t('fc.cash.out')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {WINDOWS.map((w) => (
                      <tr key={w.key}>
                        <td>{t(w.key)}</td>
                        <td className="num">{w.inflow ? money(data.inflow[w.inflow] ?? 0) : '—'}</td>
                        <td className="num">{money(data.outflow[w.outflow])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </>
  );
}
