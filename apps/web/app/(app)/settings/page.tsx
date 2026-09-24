'use client';

import Link from 'next/link';

import { PageHeader } from '@/components/shell';
import { Card, ErrorState, Row } from '@/components/ui';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface Tenant {
  id: string;
  name: string;
  business_category: string;
  status: string;
  timezone: string;
  currency: string;
  order_number_prefix: string;
  pickup_contact_name: string | null;
  pickup_address_raw: string | null;
  pickup_district: string | null;
  pickup_area: string | null;
  onboarding_step: string;
}

/**
 * Settings.
 *
 * Read-only here. Shop details are edited during onboarding and on the phone,
 * and every field on this screen is one a seller changes roughly never —
 * duplicating the edit flow would mean two places for the same validation to
 * drift, for no capability the desktop adds.
 *
 * It is worth showing, though: an operator looking at the wrong shop notices it
 * here, and support asking "what is your order prefix?" gets an answer the
 * seller can read off the screen they are already on.
 */
export default function SettingsPage() {
  const { t } = useSession();
  const { data, loading, error, reload } = useApi<Tenant>('/tenant');

  return (
    <>
      <PageHeader title={t('set.title')} subtitle={t('set.subtitle')} />

      <div className="content">
        <Card>
          {loading ? (
            <p className="card__hint">{t('common.loading')}</p>
          ) : error ? (
            <ErrorState error={error} onRetry={reload} />
          ) : data ? (
            <>
              <Row label={t('set.shopName')} value={data.name} />
              <Row
                label={t('set.category')}
                value={data.business_category.replaceAll('_', ' ').toLowerCase()}
              />
              <Row label={t('set.orderPrefix')} value={data.order_number_prefix} />
              <Row label={t('set.timezone')} value={data.timezone} />
              <Row label={t('set.currency')} value={data.currency} />
              <Row
                label={t('set.pickup')}
                value={
                  data.pickup_address_raw ?? (
                    <span className="table__sub">{t('common.none')}</span>
                  )
                }
              />
            </>
          ) : null}
        </Card>

        <div style={{ marginTop: 18 }}>
          <Card>
            <p className="card__hint">{t('set.manageOnPhone')}</p>
          </Card>
        </div>

        <div style={{ marginTop: 18 }}>
          <Card title={t('rp.title')} hint={t('rp.subtitle')}>
            <Link className="btn" href="/settings/risk-provider">{t('nav.riskProvider')}</Link>
          </Card>
        </div>
      </div>
    </>
  );
}
