'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { PurchaseOrderForm } from '@/components/PurchaseOrderForm';
import { PageHeader } from '@/components/shell';
import { useSession } from '@/lib/session';

export default function NewPurchaseOrderPage() {
  const { t } = useSession();
  const router = useRouter();
  return (
    <>
      <PageHeader title={t('pr.newPo')} subtitle={t('pr.orderHint')} />
      <div className="content">
        <Link href="/procurement">← {t('pr.tab.orders')}</Link>
        <PurchaseOrderForm onSaved={(detail) => router.push(`/procurement/${detail.purchase_order.id}`)} />
      </div>
    </>
  );
}
