'use client';

import { PageHeader } from '@/components/shell';
import { Card, Chip } from '@/components/ui';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

type Source = { id: string; name: string; provider: string; enabled: boolean; available: boolean };

/**
 * Order sources — orders pushed in from the seller's own website or system.
 *
 * This is the Custom Website path, which is Coming Soon for the launch: the
 * page offers no create, enable or submit control. Sources saved before the
 * gate are listed read-only and left exactly as they are, so the feature can
 * be switched back on without anyone re-entering anything. Couriers are not
 * affected by this gate.
 */
export default function OrderSourcesPage() {
  const { locale, t } = useSession();
  const tx = (en: string, bn: string) => (locale === 'bn' ? bn : en);
  const sources = useApi<{ items: Source[] }>('/order-sources');
  const saved = sources.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={tx('Order sources', 'অর্ডারের উৎস')}
        subtitle={tx('Orders from your own website or system.', 'নিজের ওয়েবসাইট বা সিস্টেম থেকে অর্ডার।')}
      />
      <div className="content">
        <div className="notice">
          <span>
            <strong>{tx('Custom website orders', 'নিজের ওয়েবসাইটের অর্ডার')}</strong> <Chip label={t('int.comingSoon')} />
            <br />
            <span className="card__hint">
              {tx(
                'Sending orders from your own website into ecomsbd is coming soon. Until then, create orders here, paste them from chats, or import a file.',
                'নিজের ওয়েবসাইট থেকে ecomsbd-এ অর্ডার পাঠানো শীঘ্রই আসছে। ততদিন এখানে অর্ডার তৈরি করুন, চ্যাট থেকে পেস্ট করুন, অথবা ফাইল ইমপোর্ট করুন।',
              )}
            </span>
          </span>
        </div>
        {/* No error is shown: a gated feature has nothing for the seller to fix. */}
        {saved.length > 0 ? (
          <Card title={tx('Saved sources', 'সংরক্ষিত উৎস')} hint={tx('Kept as they are for when this launches.', 'চালু হলে ব্যবহারের জন্য যেমন আছে তেমন রাখা আছে।')}>
            {saved.map((source) => (
              <p key={source.id} className="card__hint">
                {source.name} · {source.provider}
              </p>
            ))}
          </Card>
        ) : null}
      </div>
    </>
  );
}
