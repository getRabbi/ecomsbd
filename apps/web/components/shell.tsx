'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';

import { CommandPalette } from '@/components/CommandPalette';
import { Icon } from '@/components/icons';
import { initialOf, roleLabel } from '@/components/ShopSetup';
import { BrandMark, LocaleToggle } from '@/components/ui';
import { activeHref, NAV, sectionOf, visibleNav } from '@/lib/nav';
import { useUnreadCount } from '@/lib/notifications';
import { useMediaQuery, usePersistedFlag } from '@/lib/prefs';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';

/**
 * The desktop frame: a persistent navy sidebar, a global top bar, and a content
 * area that owns its own scrolling.
 *
 * The sidebar is grouped the way a shop is staffed — the daily desk, the money
 * desk, growth, running the shop — so a role that cannot see Money loses a
 * labelled section rather than leaving a hole in a list. Below 1200px it folds
 * to an icon rail; below 900px it becomes a drawer behind a menu button.
 *
 * The top bar carries what applies on every screen: which shop this is, search
 * and quick actions, notifications, language and the account.
 */

const COLLAPSED_KEY = 'ecomsbd.sidebar.collapsed';

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target.isContentEditable ||
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT'
  );
}

/** Close a popover on an outside click or Escape. */
function useDismiss(open: boolean, ref: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, ref, onClose]);
}

function Sidebar({
  collapsed,
  canCollapse,
  onToggleCollapsed,
  onNavigate,
  unread,
}: {
  collapsed: boolean;
  canCollapse: boolean;
  onToggleCollapsed: () => void;
  onNavigate: () => void;
  unread: number;
}) {
  const pathname = usePathname();
  const { t } = useSession();
  const { permissions } = useShop();
  const groups = visibleNav(NAV, permissions);

  return (
    <aside className="sidebar">
      <Link href="/dashboard" className="sidebar__brand" onClick={onNavigate}>
        <BrandMark size={30} />
        <span className="sidebar__wordmark">
          <span className="sidebar__name">{t('app.name')}</span>
          <span className="sidebar__tagline">{t('app.tagline')}</span>
        </span>
      </Link>

      <nav className="sidebar__nav" aria-label={t('nav.main')}>
        {groups.map((group, _index, all) => (
          <div className="sidebar__group" key={group.labelKey}>
            <div className="sidebar__grouplabel">{t(group.labelKey)}</div>
            {group.items.map((item) => {
              const active = activeHref(pathname, all.flatMap((entry) => entry.items.map((link) => link.href))) === item.href;
              const label = t(item.labelKey);
              const badge = item.href === '/notifications' && unread > 0;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`navlink${active ? ' navlink--active' : ''}`}
                  aria-current={active ? 'page' : undefined}
                  title={collapsed ? label : undefined}
                  onClick={onNavigate}
                >
                  <Icon name={item.icon} />
                  <span className="navlink__label">{label}</span>
                  {badge ? (
                    <span className="navlink__badge" aria-label={t('top.unread', { count: unread })}>
                      {unread > 99 ? '99+' : unread}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {canCollapse ? (
        <div className="sidebar__footer">
          <button
            type="button"
            className="navlink sidebar__collapse"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
            title={collapsed ? t('nav.expand') : undefined}
          >
            <Icon name={collapsed ? 'expand' : 'collapse'} />
            <span className="navlink__label">{t('nav.collapse')}</span>
          </button>
        </div>
      ) : null}
    </aside>
  );
}

function ShopSwitcher() {
  const { t } = useSession();
  const { me, current, can, switchShop } = useShop();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, ref, close);

  const shops = me?.tenants ?? [];

  async function choose(id: string) {
    if (id === me?.tenant_id) {
      setOpen(false);
      return;
    }
    setBusy(true);
    setFailed(false);
    try {
      // Land on the same section, not the same record: an order id from one
      // shop means nothing in another.
      await switchShop(id, sectionOf(pathname));
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <div className="popoverwrap" ref={ref}>
      <button
        type="button"
        className="shopswitch"
        aria-haspopup="menu"
        aria-expanded={open}
        title={t('shop.switch')}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="shopavatar" aria-hidden="true">
          {initialOf(current?.name)}
        </span>
        <span className="shopswitch__text">
          <span className="shopswitch__name">{current?.name ?? '—'}</span>
          <span className="shopswitch__role">{roleLabel(current?.role, t)}</span>
        </span>
        <Icon name="chevronDown" size={16} />
      </button>
      {open ? (
        <div className="popover popover--left" role="menu" aria-label={t('shop.yourShops')}>
          <div className="menu__label">{t('shop.yourShops')}</div>
          {shops.map((shop) => {
            const selected = shop.id === me?.tenant_id;
            return (
              <button
                key={shop.id}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                className="menuitem"
                disabled={busy}
                onClick={() => void choose(shop.id)}
              >
                <span className="shopavatar shopavatar--sm" aria-hidden="true">
                  {initialOf(shop.name)}
                </span>
                <span className="menuitem__main">
                  <span className="menuitem__title">{shop.name}</span>
                  <span className="menuitem__sub">
                    {roleLabel(shop.role, t)}
                    {shop.onboarding_complete ? '' : ` · ${t('shop.setupIncomplete')}`}
                  </span>
                </span>
                {selected ? <Icon name="check" size={16} /> : null}
              </button>
            );
          })}
          {failed ? (
            <p className="formerror menu__error" role="alert">
              {t('shop.switchFailed')}
            </p>
          ) : null}
          {can('settings.manage') ? (
            <>
              <div className="menu__sep" />
              <Link href="/settings" className="menuitem" role="menuitem" onClick={close}>
                <Icon name="store" size={16} />
                <span className="menuitem__main">{t('act.shopSettings')}</span>
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function AccountMenu() {
  const { t, locale, session, signOut } = useSession();
  const { me, current } = useShop();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, ref, close);

  const email = session?.user.email ?? null;
  const name = me?.display_name || email || t('top.account');
  const bn = locale === 'bn';

  return (
    <div className="popoverwrap" ref={ref}>
      <button
        type="button"
        className="avatarbtn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('top.account')}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="avatar" aria-hidden="true">
          {initialOf(name)}
        </span>
      </button>
      {open ? (
        <div className="popover popover--right" role="menu" aria-label={t('top.account')}>
          <div className="menu__identity">
            <span className="avatar avatar--lg" aria-hidden="true">
              {initialOf(name)}
            </span>
            <span className="menuitem__main">
              <span className="menu__caption">{t('top.signedInAs')}</span>
              <span className="menuitem__title">{name}</span>
              {email && email !== name ? <span className="menuitem__sub">{email}</span> : null}
              {current ? (
                <span className="menuitem__sub">
                  {current.name} · {roleLabel(current.role, t)}
                </span>
              ) : null}
            </span>
          </div>
          <div className="menu__sep" />
          <Link href="/settings" className="menuitem" role="menuitem" onClick={close}>
            <Icon name="settings" size={16} />
            <span className="menuitem__main">{t('nav.settings')}</span>
          </Link>
          <Link
            href={bn ? '/privacy-policy/bn' : '/privacy-policy'}
            className="menuitem"
            role="menuitem"
            onClick={close}
          >
            <Icon name="document" size={16} />
            <span className="menuitem__main">{t('legal.privacy')}</span>
          </Link>
          <Link
            href={bn ? '/account-deletion/bn' : '/account-deletion'}
            className="menuitem"
            role="menuitem"
            onClick={close}
          >
            <Icon name="document" size={16} />
            <span className="menuitem__main">{t('legal.deletion')}</span>
          </Link>
          <div className="menu__sep" />
          <button
            type="button"
            className="menuitem"
            role="menuitem"
            onClick={() => {
              close();
              void signOut();
            }}
          >
            <Icon name="signOut" size={16} />
            <span className="menuitem__main">{t('auth.signOut')}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Topbar({
  narrow,
  unread,
  onOpenNav,
  onOpenPalette,
}: {
  narrow: boolean;
  unread: number;
  onOpenNav: () => void;
  onOpenPalette: () => void;
}) {
  const { t } = useSession();
  return (
    <header className="topbar">
      {narrow ? (
        <button type="button" className="iconbtn" onClick={onOpenNav} aria-label={t('nav.open')}>
          <Icon name="menu" />
        </button>
      ) : null}
      <ShopSwitcher />
      <button
        type="button"
        className="searchtrigger"
        onClick={onOpenPalette}
        aria-label={t('pal.label')}
        aria-keyshortcuts="/ Control+K Meta+K"
      >
        <Icon name="search" size={16} />
        <span className="searchtrigger__text">{t('top.search')}</span>
        <kbd>/</kbd>
      </button>
      <div className="topbar__tools">
        <Link
          href="/notifications"
          className="iconbtn"
          aria-label={unread > 0 ? t('top.unread', { count: unread }) : t('nav.notifications')}
          title={t('nav.notifications')}
        >
          <Icon name="bell" />
          {unread > 0 ? (
            <span className="iconbtn__badge" aria-hidden="true">
              {unread > 99 ? '99+' : unread}
            </span>
          ) : null}
        </Link>
        <LocaleToggle />
        <AccountMenu />
      </div>
    </header>
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
      <div className="header__text">
        <h1 className="header__title">{title}</h1>
        {subtitle ? <p className="header__subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="header__actions">{actions}</div> : null}
    </header>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { t } = useSession();
  // Counted once here and shown in both the sidebar and the top bar.
  const unread = useUnreadCount(pathname);
  const [collapsedPref, setCollapsedPref] = usePersistedFlag(COLLAPSED_KEY);
  const medium = useMediaQuery('(max-width: 1199px)');
  const narrow = useMediaQuery('(max-width: 899px)');
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const collapsed = !narrow && (medium || collapsedPref);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.key === 'k' || event.key === 'K') && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setPaletteOpen(true);
      } else if (
        event.key === '/' &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isEditable(event.target)
      ) {
        event.preventDefault();
        setPaletteOpen(true);
      } else if (event.key === 'Escape') {
        setNavOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const closeNav = useCallback(() => setNavOpen(false), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);

  return (
    <div className="shell" data-collapsed={collapsed} data-navopen={narrow && navOpen}>
      <a className="skiplink" href="#main">
        {t('nav.skip')}
      </a>
      <Sidebar
        collapsed={collapsed}
        canCollapse={!medium}
        onToggleCollapsed={() => setCollapsedPref(!collapsedPref)}
        onNavigate={closeNav}
        unread={unread}
      />
      {narrow && navOpen ? (
        <div className="shell__scrim" role="presentation" onClick={closeNav} />
      ) : null}
      <div className="main">
        <Topbar
          narrow={narrow}
          unread={unread}
          onOpenNav={() => setNavOpen(true)}
          onOpenPalette={() => setPaletteOpen(true)}
        />
        <main id="main" className="workspace" tabIndex={-1}>
          {children}
        </main>
      </div>
      {paletteOpen ? <CommandPalette onClose={closePalette} /> : null}
    </div>
  );
}
