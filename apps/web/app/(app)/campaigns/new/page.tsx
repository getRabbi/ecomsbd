'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { CampaignBuilder, useCampaignLabels } from '@/components/CampaignBuilder';
import { PageHeader } from '@/components/shell';

export default function NewCampaignPage() {
  return (
    <Suspense>
      <NewCampaign />
    </Suspense>
  );
}

function NewCampaign() {
  const { t } = useCampaignLabels();
  const router = useRouter();
  const kind = useSearchParams().get('kind') === 'FLOW' ? 'FLOW' : 'ONE_OFF';
  return (
    <>
      <PageHeader title={t(kind === 'FLOW' ? 'cmp.newFlow' : 'cmp.new')} subtitle={t('cmp.subtitle')} />
      <div className="content">
        <Link href="/campaigns">← {t('cmp.back')}</Link>
        <CampaignBuilder kind={kind} onSaved={(saved) => router.push(`/campaigns/${saved.id}`)} />
      </div>
    </>
  );
}
