'use client';

import Link from 'next/link';
import { use } from 'react';

import { PageHeader } from '@/components/shell';
import { ErrorState } from '@/components/ui';
import { useAutomationLabels } from '@/components/WorkflowBuilder';
import { WorkflowEditor } from '@/components/WorkflowEditor';
import type { WorkflowDetail } from '@/lib/automation';
import { useApi } from '@/lib/useApi';

export default function WorkflowPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Workflow key={id} id={id} />;
}

function Workflow({ id }: { id: string }) {
  const { t } = useAutomationLabels();
  const detail = useApi<WorkflowDetail>(`/automation/workflows/${id}`);
  const data = detail.data;
  return (
    <>
      <PageHeader title={data?.name ?? t('auto.title')} subtitle={t('auto.subtitle')} />
      <div className="content">
        <Link href="/automation">← {t('auto.back')}</Link>
        {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
        {data ? <WorkflowEditor initial={data} onSaved={detail.reload} /> : null}
      </div>
    </>
  );
}
