'use client';

import Link from 'next/link';
import { useState } from 'react';

import { ProcurementTabs } from '@/components/ProcurementTabs';
import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState } from '@/components/ui';
import { formatDate, formatPaisa } from '@/lib/money';
import { STATUSES, statusTone, type PurchaseOrder } from '@/lib/procurement';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function PurchaseOrdersPage() {
  const { t, locale } = useSession();
  const [status, setStatus] = useState('');
  const list = useApi<{ items: PurchaseOrder[]; can_manage: boolean }>('/procurement/purchase-orders', { status: status || undefined });
  const items = list.data?.items ?? [];
  return (
    <>
      <PageHeader
        title={t('pr.title')}
        subtitle={t('pr.subtitle')}
        actions={list.data?.can_manage ? <Link className="btn btn--primary" href="/procurement/new">{t('pr.newPo')}</Link> : null}
      />
      <div className="content">
        <ProcurementTabs />
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
        {list.data && !list.data.can_manage ? <p className="card__hint">{t('pr.readOnly')}</p> : null}
        <Card
          title={t('pr.tab.orders')}
          padded={false}
          actions={
            <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t('pr.col.status')}>
              <option value="">{t('pr.all')}</option>
              <option value="OPEN">{t('pr.status.OPEN')}</option>
              {STATUSES.map((s) => <option key={s} value={s}>{t(`pr.status.${s}` as StringKey)}</option>)}
            </select>
          }
        >
          {items.length === 0 && !list.loading ? (
            <EmptyState title={t('pr.empty')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('pr.col.number')}</th>
                    <th>{t('pr.col.supplier')}</th>
                    <th>{t('pr.col.status')}</th>
                    <th>{t('pr.col.expected')}</th>
                    <th className="num">{t('pr.col.total')}</th>
                    <th className="num">{t('pr.col.received')}</th>
                    <th>{t('pr.col.payable')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((po) => (
                    <tr key={po.id}>
                      <td><Link href={`/procurement/${po.id}`}>{po.number}</Link></td>
                      <td>{po.supplier_name}</td>
                      <td><Chip label={t(`pr.status.${po.status}` as StringKey)} tone={statusTone(po.status)} /></td>
                      <td>{formatDate(po.expected_at, { locale })}</td>
                      <td className="num">{formatPaisa(po.total_paisa, { locale })}</td>
                      <td className="num">{formatPaisa(po.received_value_paisa, { locale })}</td>
                      <td>
                        {po.payable ? (
                          <Chip
                            label={po.payable.overdue ? t('pr.pay.overdue') : t(`pr.pay.${po.payable.status}` as StringKey)}
                            tone={po.payable.overdue ? 'bad' : po.payable.status === 'PAID' ? 'good' : 'neutral'}
                          />
                        ) : '—'}
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
