'use client';

import Link from 'next/link';
import { launchCopy, showPlanUi } from '@/lib/access';
import { useAccess } from '@/lib/access-context';
import { useSession } from '@/lib/session';
import { Card, ErrorState } from './ui';

export function CurrentAccess() {
  const state = useAccess();
  const { locale, t } = useSession();
  const copy = launchCopy[locale];
  const access = state?.data ?? null;
  return <Card title={copy.currentAccess}>
    {state?.error ? <ErrorState error={state.error} onRetry={state.reload} /> :
      !access ? <p>{t('common.loading')}</p> : access.free_launch_mode ? <>
        <p>{copy.fullAccess}</p><p className="card__hint">{copy.message}</p>
      </> : <>
        <p>{access.plan}</p>
        {showPlanUi(access) ? <Link href="/plans" className="btn">{copy.plans}</Link> : null}
      </>}
  </Card>;
}
