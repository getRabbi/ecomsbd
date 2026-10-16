import type { IconName } from '../components/icons';
import type { StringKey } from './i18n';

/**
 * The workstation's navigation, grouped by how a shop works rather than by
 * backend domain: the daily desk, the money desk, growing the shop, and running
 * it. A role that cannot see Money loses a labelled section, not a hole in a
 * list.
 *
 * `permission` names the permission the destination's own list endpoint
 * requires, so a destination is hidden only when the API would refuse it
 * anyway. That is display policy — the API still authorizes every request,
 * and a hidden route reached by URL renders its own permission error.
 */

export interface NavItem {
  href: string;
  labelKey: StringKey;
  icon: IconName;
  permission?: string;
}

export interface NavGroup {
  labelKey: StringKey;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    labelKey: 'nav.daily',
    items: [
      { href: '/dashboard', labelKey: 'nav.dashboard', icon: 'dashboard' },
      { href: '/orders', labelKey: 'nav.orders', icon: 'orders', permission: 'order.view' },
      { href: '/inbox', labelKey: 'nav.inbox', icon: 'inbox', permission: 'order.write' },
      { href: '/customers', labelKey: 'nav.customers', icon: 'customers', permission: 'customer.view' },
      { href: '/products', labelKey: 'nav.products', icon: 'products', permission: 'product.view' },
      { href: '/couriers', labelKey: 'nav.couriers', icon: 'couriers' },
    ],
  },
  {
    labelKey: 'nav.moneyGroup',
    items: [
      { href: '/money', labelKey: 'nav.moneyOverview', icon: 'money', permission: 'money.view' },
      { href: '/money/receivables', labelKey: 'nav.receivables', icon: 'document', permission: 'money.view' },
      { href: '/money/payouts', labelKey: 'nav.payouts', icon: 'money', permission: 'money.view' },
      { href: '/reconciliation', labelKey: 'nav.reconciliation', icon: 'reconcile', permission: 'money.view' },
      { href: '/expenses', labelKey: 'nav.expenses', icon: 'document', permission: 'money.view' },
    ],
  },
  {
    labelKey: 'nav.grow',
    items: [
      { href: '/insights', labelKey: 'nav.insights', icon: 'insights', permission: 'order.view' },
      { href: '/returns', labelKey: 'nav.returns', icon: 'returns', permission: 'order.view' },
      { href: '/forecasting', labelKey: 'nav.forecasting', icon: 'forecasting', permission: 'product.view' },
      { href: '/procurement', labelKey: 'nav.procurement', icon: 'procurement', permission: 'product.view' },
      { href: '/campaigns', labelKey: 'nav.campaigns', icon: 'campaigns', permission: 'customer.view' },
      { href: '/messaging', labelKey: 'nav.messaging', icon: 'messaging', permission: 'customer.view' },
      { href: '/automation', labelKey: 'nav.automation', icon: 'automation', permission: 'order.view' },
      { href: '/network', labelKey: 'nav.network', icon: 'network', permission: 'order.view' },
    ],
  },
  {
    labelKey: 'nav.manage',
    items: [
      { href: '/notifications', labelKey: 'nav.notifications', icon: 'bell' },
      { href: '/imports', labelKey: 'nav.imports', icon: 'imports', permission: 'order.view' },
      { href: '/integrations', labelKey: 'nav.connections', icon: 'connections', permission: 'order.view' },
      { href: '/order-sources', labelKey: 'nav.sources', icon: 'sources', permission: 'order.view' },
      { href: '/team', labelKey: 'nav.team', icon: 'team' },
      { href: '/settings', labelKey: 'nav.settings', icon: 'settings' },
      { href: '/developers', labelKey: 'nav.developers', icon: 'developers', permission: 'settings.manage' },
    ],
  },
];

/**
 * Shortcuts offered by the command palette. Each lands on the screen where the
 * action already lives — the palette is a faster way in, not a second form.
 * Each entry opens a destination that exists — the composer, the paste
 * detector, the product and expense forms, the risk check.
 */
export interface QuickAction {
  id: string;
  href: string;
  labelKey: StringKey;
  icon: IconName;
  permission?: string;
}

export const QUICK_ACTIONS: QuickAction[] = [
  { id: 'new-order', href: '/orders?new=1', labelKey: 'act.newOrder', icon: 'plus', permission: 'order.write' },
  { id: 'paste-order', href: '/orders?paste=1', labelKey: 'act.pasteOrder', icon: 'inbox', permission: 'order.write' },
  { id: 'book', href: '/orders?status=CONFIRMED', labelKey: 'act.bookCourier', icon: 'couriers', permission: 'order.book' },
  { id: 'add-product', href: '/products?new=1', labelKey: 'act.addProduct', icon: 'products', permission: 'product.write' },
  { id: 'stock', href: '/products', labelKey: 'act.stock', icon: 'products', permission: 'inventory.adjust' },
  { id: 'expense', href: '/expenses?new=1', labelKey: 'act.addExpense', icon: 'money', permission: 'money.reconcile' },
  { id: 'risk', href: '/network?check=1', labelKey: 'act.riskCheck', icon: 'network', permission: 'customer.risk_view' },
  { id: 'import', href: '/imports', labelKey: 'act.import', icon: 'imports', permission: 'order.view' },
  { id: 'reconcile', href: '/reconciliation', labelKey: 'act.reconcile', icon: 'reconcile', permission: 'money.reconcile' },
  { id: 'notifications', href: '/notifications', labelKey: 'act.notifications', icon: 'bell' },
  { id: 'shop', href: '/settings', labelKey: 'act.shopSettings', icon: 'store', permission: 'settings.manage' },
];

/** Items this member may use. `null` permissions — not known yet — allow all. */
export function allowed<T extends { permission?: string }>(
  items: T[],
  permissions: ReadonlySet<string> | null,
): T[] {
  if (permissions === null) {
    return items;
  }
  return items.filter((item) => item.permission === undefined || permissions.has(item.permission));
}

/**
 * The groups this member can use, with empty groups dropped.
 *
 * `null` permissions — still loading, or `/me` failed — show everything rather
 * than nothing: a sidebar that empties while a request is in flight is worse
 * than one link that answers with a permission error.
 */
export function visibleNav(
  groups: NavGroup[],
  permissions: ReadonlySet<string> | null,
): NavGroup[] {
  return groups
    .map((group) => ({ ...group, items: allowed(group.items, permissions) }))
    .filter((group) => group.items.length > 0);
}

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The one destination to highlight: the longest href that contains the path,
 * so `/money/payouts` lights Payouts and not Money overview as well.
 */
export function activeHref(pathname: string, hrefs: string[]): string | null {
  let best: string | null = null;
  for (const href of hrefs) {
    if (isActive(pathname, href) && (best === null || href.length > best.length)) {
      best = href;
    }
  }
  return best;
}

/**
 * A post-sign-in destination, only if it is a path on this site.
 *
 * `next` arrives in the address bar, so anyone can write it: `//evil.example`
 * and `/\evil.example` are both read by browsers as another host. Only a plain
 * absolute path is accepted, and never the sign-in page itself.
 */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) {
    return null;
  }
  if (/[\u0000-\u001f]/.test(value) || value.includes('://')) {
    return null;
  }
  if (value === '/sign-in' || value.startsWith('/sign-in?') || value.startsWith('/sign-in/')) {
    return null;
  }
  return value;
}

/** The section a path belongs to — where a shop switch should land. */
export function sectionOf(pathname: string): string {
  const first = pathname.split('/').filter(Boolean)[0];
  return first ? `/${first}` : '/dashboard';
}
