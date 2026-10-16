'use client';
import { use } from 'react';
import Link from 'next/link';
import { CustomerWorkspace } from '@/components/Crm';
import { CustomerProfile } from '@/components/CustomerProfile';
import { ExternalRisk } from '@/components/ExternalRisk';
import { PageHeader } from '@/components/shell';
import { useSession } from '@/lib/session';

export default function CustomerDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params); const { t } = useSession();
  return <><PageHeader title={t('crm.title')} /><div className="content"><Link href="/customers">← {t('crm.all')}</Link><CustomerProfile key={`p-${id}`} id={id} /><CustomerWorkspace key={id} id={id} /><ExternalRisk customerId={id} /></div></>;
}
