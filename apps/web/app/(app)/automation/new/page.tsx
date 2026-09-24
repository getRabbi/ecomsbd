'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { PageHeader } from '@/components/shell';
import { useAutomationLabels } from '@/components/WorkflowBuilder';
import { WorkflowEditor } from '@/components/WorkflowEditor';

export default function NewWorkflowPage() {
  const { t } = useAutomationLabels();
  const router = useRouter();
  return (
    <>
      <PageHeader title={t('auto.new')} subtitle={t('auto.subtitle')} />
      <div className="content">
        <Link href="/automation">← {t('auto.back')}</Link>
        <WorkflowEditor onSaved={(saved) => router.push(`/automation/${saved.id}`)} />
      </div>
    </>
  );
}
