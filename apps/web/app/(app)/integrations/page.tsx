'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import {
  EventsTable,
  HealthChip,
  problemText,
  ShowOnce,
  useIntegrationLabels,
} from '@/components/Integrations';
import { ConflictsPanel } from '@/components/IntegrationSync';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState, Tile } from '@/components/ui';
import { api } from '@/lib/api';
import {
  PROVIDERS,
  type Availability,
  type Connection,
  type Hub,
  type IntegrationEvent,
  type Provider,
} from '@/lib/integrations';
import { useApi } from '@/lib/useApi';

/**
 * The one place a seller connects, checks and fixes every order source.
 *
 * Everything shown is the API's own state: a provider without its official
 * app says so instead of offering a button that cannot work.
 */
export default function IntegrationsPage() {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const hub = useApi<Hub>('/integrations');
  const issues = useApi<{ items: IntegrationEvent[] }>('/integrations/issues');
  const [adding, setAdding] = useState<Provider | null | 'choose'>(null);

  const items = hub.data?.items ?? [];
  const live = items.filter((c) => c.state !== 'DISCONNECTED');
  const reload = () => {
    hub.reload();
    issues.reload();
  };

  return (
    <>
      <PageHeader
        title={t('int.title')}
        subtitle={t('int.subtitle')}
        actions={
          hub.data?.can_manage ? (
            <button type="button" className="btn btn--primary" onClick={() => setAdding('choose')}>
              {t('int.add')}
            </button>
          ) : null
        }
      />
      <div className="content">
        {hub.error ? <ErrorState error={hub.error} onRetry={hub.reload} /> : null}

        <div className="tiles">
          <Tile
            label={t('int.tile.connected')}
            value={String(live.filter((c) => c.state === 'CONNECTED').length)}
          />
          <Tile
            label={t('int.tile.ordersToday')}
            value={String(live.reduce((sum, c) => sum + c.orders_today, 0))}
          />
          <Tile
            label={t('int.tile.issues')}
            value={String(live.reduce((sum, c) => sum + c.open_issues, 0))}
          />
          <Tile
            label={t('int.tile.failedToday')}
            value={String(live.reduce((sum, c) => sum + c.failed_today, 0))}
          />
          <Tile
            label={t('int.tile.conflicts')}
            value={String(live.reduce((sum, c) => sum + (c.open_conflicts ?? 0), 0))}
          />
        </div>

        <div className="grid2">
          {PROVIDERS.map((provider) => (
            <ProviderCard
              key={provider}
              provider={provider}
              availability={hub.data?.providers.find((p) => p.provider === provider)}
              connections={live.filter((c) => c.provider === provider)}
              canManage={!!hub.data?.can_manage}
              onAdd={() => setAdding(provider)}
            />
          ))}
        </div>

        <Card title={t('int.col.connection')} padded={false}>
          {items.length === 0 && !hub.loading ? (
            <EmptyState title={t('int.empty')} hint={t('int.emptyHint')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('int.col.connection')}</th>
                    <th>{t('int.col.status')}</th>
                    <th className="num">{t('int.col.ordersToday')}</th>
                    <th>{t('int.col.lastSync')}</th>
                    <th>{t('int.col.lastWebhook')}</th>
                    <th className="num">{t('int.col.issues')}</th>
                    <th className="num">{t('int.col.conflicts')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.id} className="table__row--clickable">
                      <td>
                        <Link href={`/integrations/${c.id}`} className="table__primary">
                          {c.name}
                        </Link>
                        <span className="table__sub">
                          {labels.provider(c.provider)}
                          {c.account_name ? ` · ${c.account_name}` : ''}
                        </span>
                      </td>
                      <td>
                        <HealthChip connection={c} />
                      </td>
                      <td className="num">{c.orders_today}</td>
                      <td>{labels.when(c.last_sync_at)}</td>
                      <td>{labels.when(c.last_webhook_at)}</td>
                      <td className="num">{c.open_issues}</td>
                      <td className="num">{c.open_conflicts ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title={t('int.issues.title')} hint={t('int.issues.hint')} padded={false}>
          {issues.error ? <ErrorState error={issues.error} onRetry={issues.reload} /> : null}
          <EventsTable
            events={issues.data?.items ?? []}
            connections={items}
            canRetry={!!hub.data?.can_retry}
            onChanged={reload}
            emptyTitle={t('int.issues.empty')}
          />
        </Card>

        <ConflictsPanel
          canManage={!!hub.data?.can_manage}
          canRetry={!!hub.data?.can_retry}
          onChanged={reload}
        />
      </div>

      {adding ? (
        <AddDrawer
          initial={adding === 'choose' ? null : adding}
          providers={hub.data?.providers ?? []}
          onClose={() => {
            setAdding(null);
            reload();
          }}
        />
      ) : null}
    </>
  );
}

function ProviderCard({
  provider,
  availability,
  connections,
  canManage,
  onAdd,
}: {
  provider: Provider;
  availability: Availability | undefined;
  connections: Connection[];
  canManage: boolean;
  onAdd: () => void;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const blocked = availability && !availability.available;
  return (
    <Card
      title={labels.provider(provider)}
      hint={t(`int.about.${provider}`)}
      actions={
        connections.length === 0 ? (
          <Chip
            label={labels.health(blocked ? 'OFFICIAL_SETUP_REQUIRED' : 'NOT_CONNECTED')}
            tone={blocked ? 'warn' : 'neutral'}
          />
        ) : null
      }
    >
      <div style={{ display: 'grid', gap: 10 }}>
        {connections.map((c) => (
          <div
            key={c.id}
            style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}
          >
            <span>
              <Link href={`/integrations/${c.id}`} className="table__primary">
                {c.account_name || c.name}
              </Link>
              <span className="table__sub">
                {t('int.col.ordersToday')}: {c.orders_today} · {t('int.lastSync')}:{' '}
                {labels.when(c.last_sync_at ?? c.last_success_at)}
              </span>
            </span>
            <HealthChip connection={c} />
          </div>
        ))}
        {blocked && availability?.blocker ? (
          <p className="card__hint">{labels.blocker(availability.blocker)}</p>
        ) : null}
        {!blocked && canManage ? (
          <span>
            <button type="button" className="btn btn--sm" onClick={onAdd}>
              {t('int.add')}
            </button>
          </span>
        ) : null}
      </div>
    </Card>
  );
}

function AddDrawer({
  initial,
  providers,
  onClose,
}: {
  initial: Provider | null;
  providers: Availability[];
  onClose: () => void;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const router = useRouter();
  const [provider, setProvider] = useState<Provider | null>(initial);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [created, setCreated] = useState<{ id: string; key: string } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!provider) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ connection: Connection; api_key?: string }>('/integrations', {
        provider,
        name: name.trim() || labels.provider(provider),
      });
      if (result.api_key) {
        // The key exists only in this response; it is shown here and dropped.
        setCreated({ id: result.connection.id, key: result.api_key });
      } else {
        router.push(`/integrations/${result.connection.id}`);
      }
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <Drawer title={t('int.addTitle')} onClose={onClose}>
        <ShowOnce
          title={t('int.dev.keyOnce')}
          value={created.key}
          onDone={() => router.push(`/integrations/${created.id}`)}
        />
      </Drawer>
    );
  }

  const problem = problemText(error, labels.code);
  return (
    <Drawer title={t('int.addTitle')} onClose={onClose}>
      <form onSubmit={submit} className="wizard__body">
        <fieldset className="wizard__choices" disabled={busy} style={{ gridTemplateColumns: '1fr' }}>
          <legend>{t('int.choose')}</legend>
          {PROVIDERS.map((option) => {
            const status = providers.find((p) => p.provider === option);
            const blocked = status ? !status.available : false;
            return (
              <label key={option} className="choice" data-checked={provider === option}>
                <input
                  type="radio"
                  name="provider"
                  value={option}
                  disabled={blocked}
                  checked={provider === option}
                  onChange={() => setProvider(option)}
                />
                <span>
                  <strong>{labels.provider(option)}</strong>
                  <span className="table__sub">
                    {blocked && status?.blocker
                      ? labels.blocker(status.blocker)
                      : t(`int.about.${option}`)}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>
        <label className="field">
          <span className="field__label">{t('int.name')}</span>
          <input
            className="input"
            value={name}
            maxLength={120}
            placeholder={provider ? labels.provider(provider) : ''}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
        <div className="wizard__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy || !provider}>
            {t('int.create')}
          </button>
        </div>
      </form>
    </Drawer>
  );
}
