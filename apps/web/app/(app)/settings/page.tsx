'use client';

import Link from 'next/link';

import { AccountSecurity, PrivacyData } from '@/components/AccountSettings';
import { CurrentAccess } from '@/components/current-access';
import { Icon, type IconName } from '@/components/icons';
import { PageHeader } from '@/components/shell';
import { ShopForm } from '@/components/ShopSetup';
import { Card, ErrorState, Row } from '@/components/ui';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import type { ShopDetails } from '@/lib/shop-form';
import { useApi } from '@/lib/useApi';

interface WorkspaceLink {
  href: string;
  labelKey: StringKey;
  icon: IconName;
  permission?: string;
}

const WORKSPACE_LINKS: WorkspaceLink[] = [
  { href: '/couriers', labelKey: 'set.courierAccounts', icon: 'couriers' },
  { href: '/integrations', labelKey: 'nav.connections', icon: 'connections', permission: 'order.view' },
  { href: '/team', labelKey: 'nav.team', icon: 'team' },
  { href: '/notifications', labelKey: 'set.notificationSettings', icon: 'bell' },
  { href: '/settings/risk-provider', labelKey: 'nav.riskProvider', icon: 'network' },
  { href: '/developers', labelKey: 'nav.developers', icon: 'developers', permission: 'settings.manage' },
];

/**
 * Settings.
 *
 * Shop details are edited here, against the same `PATCH /v1/tenant` the
 * mobile app uses — the API validates and audits every change, and only a
 * member with `settings.manage` may make one. Everyone else sees the same
 * fields read-only, with the reason.
 *
 * The order-number prefix, timezone and currency are shown but not offered for
 * editing: the API does not accept changes to them, and a box that looks
 * editable but is not would be worse than a plain value.
 */
export default function SettingsPage() {
  const { t, locale } = useSession();
  const shop = useShop();
  const tenant = useApi<ShopDetails>('/tenant');
  const canEdit = shop.can('settings.manage');
  const bn = locale === 'bn';

  return (
    <>
      <PageHeader title={t('set.title')} subtitle={t('set.subtitle')} />

      <div className="content">
        <div className="settings">
          <div className="stack">
            <Card title={t('set.shopSection')} hint={canEdit ? t('set.shopHint') : t('set.readOnly')}>
              {tenant.error ? (
                <ErrorState error={tenant.error} onRetry={tenant.reload} />
              ) : tenant.data ? (
                <ShopForm
                  key={tenant.data.id}
                  mode="edit"
                  shop={tenant.data}
                  readOnly={!canEdit}
                  onDone={() => {
                    tenant.reload();
                    shop.reload();
                  }}
                />
              ) : (
                <p className="card__hint">{t('common.loading')}</p>
              )}
            </Card>
            <AccountSecurity />
            <PrivacyData />
          </div>

          <div className="stack">
            <Card title={t('set.fixedTitle')}>
              {tenant.data ? (
                <>
                  <Row label={t('set.orderPrefix')} value={tenant.data.order_number_prefix} />
                  <Row label={t('set.timezone')} value={tenant.data.timezone} />
                  <Row label={t('set.currency')} value={tenant.data.currency} />
                  <Row label={t('set.status')} value={tenant.data.status.replaceAll('_', ' ').toLowerCase()} />
                </>
              ) : (
                <p className="card__hint">{tenant.error ? '—' : t('common.loading')}</p>
              )}
            </Card>

            <CurrentAccess />

            <Card title={t('set.workspace')} hint={t('set.workspaceHint')} padded={false}>
              <ul className="linklist">
                {WORKSPACE_LINKS.filter((link) => !link.permission || shop.can(link.permission)).map((link) => (
                  <li key={link.href}>
                    <Link href={link.href} className="linklist__item">
                      <Icon name={link.icon} size={16} />
                      <span>{t(link.labelKey)}</span>
                      <Icon name="arrowRight" size={14} />
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>

            <Card title={t('legal.title')} padded={false}>
              <ul className="linklist">
                <li>
                  <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'} className="linklist__item">
                    <Icon name="document" size={16} />
                    <span>{t('legal.privacy')}</span>
                    <Icon name="arrowRight" size={14} />
                  </Link>
                </li>
                <li>
                  <Link href={bn ? '/account-deletion/bn' : '/account-deletion'} className="linklist__item">
                    <Icon name="document" size={16} />
                    <span>{t('legal.deletion')}</span>
                    <Icon name="arrowRight" size={14} />
                  </Link>
                </li>
              </ul>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
