'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { useSession } from '@/lib/session';

const TABS = [
  { href: '/forecasting', key: 'fc.tab.reorder' },
  { href: '/forecasting/accuracy', key: 'fc.tab.accuracy' },
  { href: '/forecasting/cash', key: 'fc.tab.cash' },
] as const;

export function ForecastTabs() {
  const { t } = useSession();
  const pathname = usePathname();
  return (
    <div role="tablist" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {TABS.map((tab) => {
        const active = tab.href === '/forecasting' ? pathname === '/forecasting' : pathname.startsWith(tab.href);
        return (
          <Link key={tab.href} href={tab.href} role="tab" aria-selected={active} className={`btn btn--sm${active ? ' btn--primary' : ''}`}>
            {t(tab.key)}
          </Link>
        );
      })}
    </div>
  );
}
