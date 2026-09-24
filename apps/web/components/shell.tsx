'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

import type { StringKey } from '@/lib/i18n';
import { useUnreadCount } from '@/lib/notifications';
import { useSession } from '@/lib/session';

/**
 * The desktop frame: a persistent left sidebar, a contextual top header, and a
 * content area that owns its own scrolling.
 *
 * Grouped navigation rather than one flat list, because ten destinations read
 * as a wall. The groups match how a shop is actually staffed — the person doing
 * orders is rarely the person doing reconciliation — which also means a role
 * that cannot see Money loses a labelled section rather than leaving a hole in
 * the middle of a list.
 */

interface NavItem {
  href: string;
  labelKey: StringKey;
  icon: string;
}

interface NavGroup {
  labelKey: StringKey;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    labelKey: 'nav.operations',
    items: [
      { href: '/dashboard', labelKey: 'nav.dashboard', icon: '▤' },
      { href: '/insights', labelKey: 'nav.insights', icon: '↗' },
      { href: '/network', labelKey: 'nav.network', icon: '◉' },
      { href: '/notifications', labelKey: 'nav.notifications', icon: '◔' },
      { href: '/orders', labelKey: 'nav.orders', icon: '▦' },
      { href: '/customers', labelKey: 'nav.customers', icon: '☺' },
      { href: '/messaging', labelKey: 'nav.messaging', icon: '✉' },
      { href: '/campaigns', labelKey: 'nav.campaigns', icon: '✦' },
      { href: '/automation', labelKey: 'nav.automation', icon: '⚡' },
      { href: '/products', labelKey: 'nav.products', icon: '⬚' },
      { href: '/procurement', labelKey: 'nav.procurement', icon: '⧈' },
      { href: '/forecasting', labelKey: 'nav.forecasting', icon: '◭' },
      { href: '/couriers', labelKey: 'nav.couriers', icon: '⇢' },
      { href: '/returns', labelKey: 'nav.returns', icon: '↺' },
    ],
  },
  {
    labelKey: 'nav.finance',
    items: [
      { href: '/money', labelKey: 'nav.money', icon: '৳' },
      { href: '/reconciliation', labelKey: 'nav.reconciliation', icon: '⇄' },
    ],
  },
  {
    labelKey: 'nav.shop',
    items: [
      { href: '/integrations', labelKey: 'nav.integrations', icon: '⧉' },
      { href: '/imports', labelKey: 'nav.imports', icon: '⇪' },
      { href: '/order-sources', labelKey: 'nav.sources', icon: '⇥' },
      { href: '/team', labelKey: 'nav.team', icon: '⚇' },
      { href: '/settings', labelKey: 'nav.settings', icon: '⚙' },
      { href: '/developers', labelKey: 'nav.developers', icon: '⌘' },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { t, locale, setLocale, signOut } = useSession();
  // Only what this member receives, as the API counts it.
  const unread = useUnreadCount(pathname);

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__name">{t('app.name')}</div>
        <div className="sidebar__tagline">{t('app.tagline')}</div>
      </div>

      <nav className="sidebar__nav" aria-label={t('app.tagline')}>
        {NAV.map((group) => (
          <div className="sidebar__group" key={group.labelKey}>
            <div className="sidebar__grouplabel">{t(group.labelKey)}</div>
            {group.items.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`navlink${active ? ' navlink--active' : ''}`}
                  aria-current={active ? 'page' : undefined}
                >
                  <span aria-hidden="true">{item.icon}</span>
                  <span>{t(item.labelKey)}</span>
                  {item.href === '/notifications' && unread > 0 ? (
                    <span
                      className="chip chip--bad"
                      style={{ marginLeft: 'auto' }}
                      aria-label={`${unread} ${t('notif.unread')}`}
                    >
                      {unread > 99 ? '99+' : unread}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar__footer">
        <label className="field" style={{ marginBottom: 8 }}>
          <span className="field__label">{t('common.language')}</span>
          <select
            className="select"
            value={locale}
            onChange={(event) => setLocale(event.target.value === 'bn' ? 'bn' : 'en')}
            style={{ width: '100%' }}
          >
            <option value="en">English</option>
            <option value="bn">বাংলা</option>
          </select>
        </label>
        <button type="button" className="btn btn--ghost btn--sm" onClick={signOut}>
          {t('auth.signOut')}
        </button>
      </div>
    </aside>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="header">
      <div>
        <div className="header__title">{title}</div>
        {subtitle ? <div className="header__subtitle">{subtitle}</div> : null}
      </div>
      {actions ? <div className="header__actions">{actions}</div> : null}
    </header>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">{children}</div>
    </div>
  );
}
