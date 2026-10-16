'use client';

import { PayoutsTable } from '@/components/MoneyLedger';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui';
import { useSession } from '@/lib/session';

/** Courier payouts: statement import, manual entry, and line-by-line matching. */
export default function PayoutsPage() {
  const { t } = useSession();

  return (
    <>
      <PageHeader title={t('nav.payouts')} subtitle={t('ml.payoutsSub')} />
      <div className="content">
        <Card padded={false}>
          <PayoutsTable />
        </Card>
      </div>
    </>
  );
}
