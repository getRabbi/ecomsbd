'use client';

import { CurrentAccess } from '@/components/current-access';
import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { launchCopy, showPlanUi } from '@/lib/access';
import { useAccess } from '@/lib/access-context';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

export default function PlansPage() {
  const { locale } = useSession();
  const access = useAccess()?.data ?? null;
  const copy = launchCopy[locale];
  const plans = useApi<{ code: string; name: string }[]>(showPlanUi(access) ? '/billing/plans' : null);
  return <>
    <PageHeader title={copy.currentAccess} />
    <div className="content">
      <CurrentAccess />
      {showPlanUi(access) ? <Card title={copy.plans}>
        {plans.error ? <ErrorState error={plans.error} onRetry={plans.reload} /> :
          plans.data?.map(plan => <p key={plan.code}>{plan.name}</p>)}
        <p className="card__hint">{copy.unavailable}</p>
      </Card> : null}
    </div>
  </>;
}
