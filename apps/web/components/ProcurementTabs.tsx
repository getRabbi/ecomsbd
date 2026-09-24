'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { useSession } from '@/lib/session';

const TABS = [
  { href: '/procurement', key: 'pr.tab.orders' },
  { href: '/procurement/suppliers', key: 'pr.tab.suppliers' },
  { href: '/procurement/payables', key: 'pr.tab.payables' },
  { href: '/procurement/stock', key: 'pr.tab.stock' },
] as const;

export function ProcurementTabs() {
  const { t } = useSession();
  const pathname = usePathname();
  return (
    <div role="tablist" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {TABS.map((tab) => {
        const active = tab.href === '/procurement' ? pathname === '/procurement' : pathname.startsWith(tab.href);
        return (
          <Link key={tab.href} href={tab.href} role="tab" aria-selected={active} className={`btn btn--sm${active ? ' btn--primary' : ''}`}>
            {t(tab.key)}
          </Link>
        );
      })}
    </div>
  );
}
