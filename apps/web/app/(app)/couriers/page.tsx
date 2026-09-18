'use client';

import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState, Row, type Tone } from '@/components/ui';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface CourierAccount {
  id: string;
  provider: string;
  label: string | null;
  status: string;
  connected: boolean;
  needs_reconnect: boolean;
  /** `****abcd`. The only credential-derived value any client ever receives. */
  masked_identifier: string | null;
  last_validation_message: string | null;
  capabilities: Record<string, string>;
  config: Record<string, unknown>;
  webhook_configured: boolean;
}

interface ProviderInfo {
  provider: string;
  display_name: string;
  capabilities: Record<string, string>;
  enabled: boolean;
  fully_unverified: boolean;
  manual_fallback: string | null;
}

/**
 * Couriers.
 *
 * Read-only on purpose, and the reason is the important part: courier
 * credentials are stored encrypted in the backend vault and **no endpoint
 * returns them to any client**, so there is nothing for this screen to display
 * and nothing for it to edit. The masked identifier is the only
 * credential-derived value that crosses the wire, and it is not reversible.
 *
 * Connecting and disconnecting stay on the phone, where the seller already
 * does it. Adding a second place to type an API key would mean a second place
 * for one to be shoulder-surfed, for no capability the phone lacks.
 *
 * What the desktop is good for is the overview: every courier at once, which is
 * connected, which needs attention, and what each can actually do.
 */
export default function CouriersPage() {
  const { t } = useSession();

  const providers = useApi<ProviderInfo[]>('/couriers/providers');
  const accounts = useApi<CourierAccount[]>('/couriers/accounts');

  const byProvider = new Map<string, CourierAccount>();
  for (const account of accounts.data ?? []) {
    byProvider.set(account.provider, account);
  }

  // Manual mode and any courier with no verified contract are not connectable,
  // so they are not shown as couriers that failed to connect.
  const rows = (providers.data ?? []).filter((provider) => !provider.fully_unverified);

  return (
    <>
      <PageHeader title={t('cour.title')} subtitle={t('cour.subtitle')} />

      <div className="content">
        {providers.error ? (
          <Card>
            <ErrorState error={providers.error} onRetry={providers.reload} />
          </Card>
        ) : (
          <div className="tiles" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
            {providers.loading
              ? null
              : rows.map((provider) => {
                  const account = byProvider.get(provider.provider);
                  const state = describeAccount(account, provider);

                  return (
                    <Card
                      key={provider.provider}
                      title={provider.display_name}
                      actions={<Chip label={t(state.labelKey)} tone={state.tone} />}
                    >
                      {account?.masked_identifier ? (
                        <Row label={t('cour.identifier')} value={account.masked_identifier} />
                      ) : null}

                      {typeof account?.config?.store_name === 'string' ? (
                        <Row label="Store" value={account.config.store_name} />
                      ) : null}

                      <Row
                        label={t('cour.capabilities')}
                        value={summariseCapabilities(provider.capabilities)}
                      />

                      {account?.last_validation_message ? (
                        <p className="card__hint" style={{ marginTop: 10 }}>
                          {account.last_validation_message}
                        </p>
                      ) : null}
                    </Card>
                  );
                })}
          </div>
        )}

        <Card>
          <p className="card__hint">{t('cour.manageOnPhone')}</p>
          {/* Said plainly, because a seller looking for their API key here
              should learn why it is absent rather than assume it is missing. */}
          <p className="card__hint" style={{ marginTop: 6 }}>
            {t('cour.secretsNote')}
          </p>
        </Card>
      </div>
    </>
  );
}

function describeAccount(
  account: CourierAccount | undefined,
  provider: ProviderInfo,
): { labelKey: 'cour.connected' | 'cour.needsReconnect' | 'cour.notConnected' | 'cour.unverified'; tone: Tone } {
  if (!provider.enabled) {
    return { labelKey: 'cour.unverified', tone: 'neutral' };
  }
  if (!account) {
    return { labelKey: 'cour.notConnected', tone: 'neutral' };
  }
  if (account.needs_reconnect) {
    return { labelKey: 'cour.needsReconnect', tone: 'warn' };
  }
  if (account.connected) {
    return { labelKey: 'cour.connected', tone: 'good' };
  }
  return { labelKey: 'cour.notConnected', tone: 'neutral' };
}

/**
 * The capabilities a courier has actually been verified to support.
 *
 * Only `"true"` counts. `"unknown"` means the provider's documentation is
 * silent, which is not the same as supported — and a dashboard that rounded it
 * up would promise a seller something that has never been tested.
 */
function summariseCapabilities(capabilities: Record<string, string>): string {
  const verified = Object.entries(capabilities)
    .filter(([, value]) => value === 'true')
    .map(([key]) => key.replaceAll('_', ' '));
  return verified.length > 0 ? verified.join(', ') : '—';
}
