'use client';

import Link from 'next/link';
import { useState } from 'react';

import { ProcurementTabs } from '@/components/ProcurementTabs';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, Tile } from '@/components/ui';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import type { PurchaseOrder } from '@/lib/procurement';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

const STATES = ['OPEN', 'OVERDUE', 'PAID', 'ALL'] as const;

export default function PayablesPage() {
  const { t, locale } = useSession();
  const [state, setState] = useState<(typeof STATES)[number]>('OPEN');
  const list = useApi<{
    items: PurchaseOrder[];
    totals: { owed_paisa: number; paid_paisa: number; outstanding_paisa: number; overdue_paisa: number };
  }>('/procurement/payables', { state });
  const totals = list.data?.totals;
  return (
    <>
      <PageHeader title={t('pr.tab.payables')} subtitle={t('pr.p.separate')} />
      <div className="content">
        <ProcurementTabs />
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
        {totals ? (
          <div className="tiles">
            <Tile label={t('pr.totals.owed')} value={formatPaisa(totals.owed_paisa, { locale })} />
            <Tile label={t('pr.totals.paid')} value={formatPaisa(totals.paid_paisa, { locale })} />
            <Tile label={t('pr.totals.outstanding')} value={formatPaisa(totals.outstanding_paisa, { locale })} />
            <Tile label={t('pr.totals.overdue')} value={formatPaisa(totals.overdue_paisa, { locale })} />
          </div>
        ) : null}
        <Card
          title={t('pr.tab.payables')}
          padded={false}
          actions={
            <div role="tablist" style={{ display: 'flex', gap: 6 }}>
              {STATES.map((s) => (
                <button key={s} type="button" className={`btn btn--sm${state === s ? ' btn--primary' : ''}`} onClick={() => setState(s)}>{t(`pr.state.${s}` as StringKey)}</button>
              ))}
            </div>
          }
        >
          {(list.data?.items.length ?? 0) === 0 && !list.loading ? (
            <EmptyState title={t('pr.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('pr.col.number')}</th>
                    <th>{t('pr.col.supplier')}</th>
                    <th className="num">{t('pr.col.received')}</th>
                    <th className="num">{t('pr.totals.paid')}</th>
                    <th className="num">{t('pr.totals.outstanding')}</th>
                    <th>{t('pr.col.due')}</th>
                    <th>{t('pr.col.payable')}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data?.items.map((po) => (
                    <tr key={po.id}>
                      <td><Link href={`/procurement/${po.id}`}>{po.number}</Link></td>
                      <td>{po.supplier_name}</td>
                      <td className="num">{formatPaisa(po.received_value_paisa, { locale })}</td>
                      <td className="num">{formatPaisa(po.payable?.paid_paisa ?? 0, { locale })}</td>
                      <td className="num">{formatPaisa(po.payable?.balance_paisa ?? 0, { locale })}</td>
                      <td>{formatDate(po.payable?.due_at ?? null, { locale })}</td>
                      <td>
                        {po.payable ? (
                          <Chip
                            label={po.payable.overdue ? t('pr.pay.overdue') : t(`pr.pay.${po.payable.status}` as StringKey)}
                            tone={po.payable.overdue ? 'bad' : po.payable.status === 'PAID' ? 'good' : 'warn'}
                          />
                        ) : null}
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
