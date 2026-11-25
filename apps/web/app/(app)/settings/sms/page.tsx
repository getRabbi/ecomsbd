'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import {
  canToggle,
  orderUpdateSwitch,
  type ChannelRow,
  type SmsSettings,
  type SmsSettingsKey,
  type SmsSwitch,
} from '@/lib/sms-settings';
import { useApi } from '@/lib/useApi';

/**
 * Settings → SMS (launch run 14). Two switches, both off until the seller
 * turns them on: the SMS confirm link (`PATCH /v1/shop/sms-settings`) and
 * order-update SMS (the messaging channel `SMS`). Needs `settings.manage`.
 */
export default function SmsSettingsPage() {
  const { t } = useSession();
  const shop = useShop();
  const canEdit = shop.can('settings.manage');
  const settings = useApi<SmsSettings>(canEdit ? '/shop/sms-settings' : null);
  const channels = useApi<{ items: ChannelRow[] }>(canEdit ? '/messaging/channels' : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await action();
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      settings.reload();
      channels.reload();
    }
  }

  const loadError = settings.error || channels.error;

  return (
    <>
      <PageHeader title={t('nav.sms')} subtitle={t('ss.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18, maxWidth: 720 }}>
        {!canEdit ? (
          <Card>
            <p>{t('ss.readOnly')}</p>
          </Card>
        ) : loadError ? (
          <ErrorState error={loadError} onRetry={() => (settings.reload(), channels.reload())} />
        ) : settings.data && channels.data ? (
          <>
            {error ? <ErrorState error={error} /> : null}
            {saved ? <p className="card__hint">{t('ss.saved')}</p> : null}
            <SwitchCard
              title="ss.linkTitle"
              hint="ss.linkHint"
              label="ss.linkSwitch"
              state={settings.data.confirmation_link}
              busy={busy}
              testId="sms-confirm-link"
              onChange={(on) => void run(() => api.patch('/shop/sms-settings', { confirmation_link_enabled: on }))}
            />
            <SwitchCard
              title="ss.updatesTitle"
              hint="ss.updatesHint"
              label="ss.updatesSwitch"
              state={orderUpdateSwitch(channels.data.items)}
              busy={busy}
              testId="sms-order-updates"
              onChange={(on) => void run(() => api.post('/messaging/channels/SMS', { enabled: on }))}
            />
          </>
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </div>
    </>
  );
}

function SwitchCard({
  title,
  hint,
  label,
  state,
  busy,
  testId,
  onChange,
}: {
  title: SmsSettingsKey;
  hint: SmsSettingsKey;
  label: SmsSettingsKey;
  state: SmsSwitch;
  busy: boolean;
  testId: string;
  onChange: (on: boolean) => void;
}) {
  const { t } = useSession();
  return (
    <Card title={t(title)} hint={t(hint)}>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          type="checkbox"
          data-testid={testId}
          checked={state.enabled}
          disabled={busy || !canToggle(state)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{t(label)}</span>
      </label>
      <p className="card__hint" style={{ marginTop: 8 }}>
        {state.available || state.enabled ? t('ss.cost') : t('ss.unavailable')}
      </p>
    </Card>
  );
}
