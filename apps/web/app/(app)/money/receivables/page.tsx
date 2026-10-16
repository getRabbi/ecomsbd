'use client';

import { useState } from 'react';

import { ReceivablesTable, type AgingBand, type ReceivableFilter } from '@/components/MoneyLedger';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

/** COD receivables on their own page — the same table the Money overview links into. */
export default function ReceivablesPage() {
  const { t } = useSession();
  const [filter, setFilter] = useState<ReceivableFilter>({ provider: '', band: '', unpaidOnly: false });
  const aging = useApi<AgingBand[]>('/money/aging');

  return (
    <>
      <PageHeader title={t('nav.receivables')} subtitle={t('ml.receivablesSub')} />
      <div className="content">
        <Card padded={false}>
          <ReceivablesTable filter={filter} onFilter={setFilter} bands={aging.data ?? []} />
        </Card>
      </div>
    </>
  );
}
