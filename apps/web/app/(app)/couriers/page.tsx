'use client';

import { useState, type FormEvent } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, ErrorState, Row, type Tone } from '@/components/ui';
import { api } from '@/lib/api';
import {
  verifiedCapabilities,
  type BookableCourier,
  type ConnectForm,
  type CourierAccount,
  type ProviderInfo,
} from '@/lib/couriers';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import { errorMessage } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

interface WebhookSetup {
  provider: string;
  supported: boolean;
  callback_url: string | null;
  secret_configured: boolean;
  help_en: string | null;
  help_bn: string | null;
}

type Panel = { kind: 'connect' | 'store' | 'webhook'; provider: string } | null;

/**
 * Couriers — connect, manage and compare Steadfast, Pathao and RedX.
 *
 * Couriers are a live launch feature and are never touched by the
 * integrations Coming Soon gate.
 *
 * The connect form is the provider's own declaration from
 * `GET /couriers/providers`: field names, labels and which are secret come
 * from the backend, so this page has no per-courier knowledge to drift. Keys
 * are sent once over HTTPS to the backend vault, which encrypts them; no
 * endpoint returns them, the form clears after sending, and nothing is kept in
 * the browser. The masked identifier is the only credential-derived value any
 * client ever sees.
 */
export default function CouriersPage() {
  const { t, locale } = useSession();
  const { can } = useShop();
  const providers = useApi<ProviderInfo[]>('/couriers/providers');
  const accounts = useApi<CourierAccount[]>('/couriers/accounts');
  const bookable = useApi<BookableCourier[]>(can('order.book') ? '/couriers/bookable' : null);
  const [panel, setPanel] = useState<Panel>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ provider: string; text: string; tone: Tone } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const manage = can('courier.credential_manage');
  const byProvider = new Map((accounts.data ?? []).map((account) => [account.provider, account]));
  const bookableBy = new Map((bookable.data ?? []).map((row) => [row.provider, row]));
  // Manual mode and any courier with no verified contract are not connectable,
  // so they are not shown as couriers that failed to connect.
  const rows = (providers.data ?? []).filter((provider) => !provider.fully_unverified);

  function refresh() {
    accounts.reload();
    bookable.reload();
  }

  async function act(provider: string, work: () => Promise<{ result?: string; message?: string } | unknown>, done: string) {
    setBusy(provider);
    setNotice(null);
    try {
      const outcome = (await work()) as { result?: string; message?: string } | undefined;
      const failed = outcome?.result && outcome.result !== 'OK' && outcome.result !== 'VALID' && outcome.result !== 'SUCCESS';
      setNotice({ provider, text: outcome?.message || done, tone: failed ? 'warn' : 'good' });
      refresh();
    } catch (caught) {
      setNotice({ provider, text: errorMessage(caught), tone: 'bad' });
    } finally {
      setBusy(null);
    }
  }

  const capabilityKeys = Array.from(new Set(rows.flatMap((row) => Object.keys(row.capabilities)))).sort();

  return (
    <>
      <PageHeader title={t('cour.title')} subtitle={t('cour.subtitleManage')} />

      <div className="content">
        {providers.error ? (
          <Card>
            <ErrorState error={providers.error} onRetry={providers.reload} />
          </Card>
        ) : null}

        <div className="tiles" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
          {rows.map((provider) => {
            const account = byProvider.get(provider.provider);
            const state = describeAccount(account, provider);
            const form = provider.connect_form;
            const storeName = typeof account?.config?.store_name === 'string' ? account.config.store_name : null;
            const working = busy === provider.provider;

            return (
              <Card key={provider.provider} title={provider.display_name} actions={<Chip label={t(state.labelKey)} tone={state.tone} />}>
                {account?.masked_identifier ? <Row label={t('cour.identifier')} value={account.masked_identifier} /> : null}
                {form?.supports_store ? (
                  <Row
                    label={t('cour.store')}
                    value={storeName ?? (form.requires_store ? <Chip label={t('cour.storeNeeded')} tone="warn" /> : '—')}
                  />
                ) : null}
                {account?.config?.sandbox === true ? <Row label={t('cour.mode')} value={<Chip label={t('cour.sandbox')} tone="warn" />} /> : null}
                {account?.last_verified_at ? <Row label={t('cour.verified')} value={formatDate(account.last_verified_at, { locale, withTime: true })} /> : null}
                {account?.reported_balance_paisa !== null && account?.reported_balance_paisa !== undefined ? (
                  <Row label={t('cour.balance')} value={formatPaisa(account.reported_balance_paisa, { locale })} />
                ) : null}
                <Row label={t('cour.capabilities')} value={verifiedCapabilities(provider.capabilities).map((key) => key.replaceAll('_', ' ')).join(', ') || '—'} />
                {bookableBy.get(provider.provider) ? (
                  <Row
                    label={t('cour.booking')}
                    value={
                      bookableBy.get(provider.provider)?.bookable ? (
                        <Chip label={t('od.canBook')} tone="good" />
                      ) : (
                        <span className="table__sub">{bookableBy.get(provider.provider)?.reason ?? '—'}</span>
                      )
                    }
                  />
                ) : null}

                {/* The provider's own words when the last check failed — never hidden, never rewritten. */}
                {account?.last_validation_message && (account.needs_reconnect || account.last_validation_result !== 'VALID') ? (
                  <p className="card__hint" style={{ marginTop: 8 }}>{account.last_validation_message}</p>
                ) : null}
                {notice?.provider === provider.provider ? (
                  <div className={`notice notice--${notice.tone === 'good' ? 'good' : 'warn'}`} style={{ marginTop: 8 }}>{notice.text}</div>
                ) : null}

                {manage && provider.enabled && form ? (
                  <div className="toolbar" style={{ padding: '10px 0 0', flexWrap: 'wrap' }}>
                    <button type="button" className={`btn btn--sm${!account?.connected || account.needs_reconnect ? ' btn--primary' : ''}`} onClick={() => setPanel({ kind: 'connect', provider: provider.provider })}>
                      {account ? t('cour.reconnect') : t('cour.connect')}
                    </button>
                    {account ? (
                      <>
                        <button type="button" className="btn btn--sm" disabled={working} onClick={() => void act(provider.provider, () => api.post(`/couriers/accounts/${provider.provider}/test`), t('cour.testOk'))}>
                          {t('cour.test')}
                        </button>
                        {form.supports_store ? (
                          <button type="button" className="btn btn--sm" onClick={() => setPanel({ kind: 'store', provider: provider.provider })}>
                            {t('cour.chooseStore')}
                          </button>
                        ) : null}
                        {form.uses_webhook ? (
                          <button type="button" className="btn btn--sm" onClick={() => setPanel({ kind: 'webhook', provider: provider.provider })}>
                            {t('cour.webhook')}
                          </button>
                        ) : null}
                        {confirming === provider.provider ? (
                          <>
                            <button
                              type="button"
                              className="btn btn--sm"
                              disabled={working}
                              onClick={() => {
                                setConfirming(null);
                                void act(provider.provider, () => api.delete(`/couriers/accounts/${provider.provider}`), t('cour.disconnected'));
                              }}
                            >
                              {t('cour.confirmDisconnect')}
                            </button>
                            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirming(null)}>
                              {t('common.cancel')}
                            </button>
                          </>
                        ) : (
                          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirming(provider.provider)}>
                            {t('cour.disconnect')}
                          </button>
                        )}
                      </>
                    ) : null}
                  </div>
                ) : null}
                {!manage ? <p className="card__hint" style={{ marginTop: 8 }}>{t('cour.ownerOnly')}</p> : null}
              </Card>
            );
          })}
        </div>

        {rows.length > 0 ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('cour.compare')} hint={t('cour.compareHint')} padded={false}>
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('cour.capability')}</th>
                      {rows.map((row) => (
                        <th key={row.provider}>{row.display_name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className="table__primary">{t('cour.state')}</td>
                      {rows.map((row) => {
                        const state = describeAccount(byProvider.get(row.provider), row);
                        return (
                          <td key={row.provider}>
                            <Chip label={t(state.labelKey)} tone={state.tone} />
                          </td>
                        );
                      })}
                    </tr>
                    {capabilityKeys.map((key) => (
                      <tr key={key}>
                        <td>{key.replaceAll('_', ' ')}</td>
                        {rows.map((row) => {
                          const value = row.capabilities[key];
                          return (
                            <td key={row.provider}>
                              {value === 'true' ? (
                                <Chip label={t('cour.yes')} tone="good" />
                              ) : value === 'false' ? (
                                <span className="table__sub">{t('cour.no')}</span>
                              ) : (
                                <span className="table__sub">{t('cour.unknown')}</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="card__hint" style={{ padding: '10px 16px' }}>{t('cour.compareNote')}</p>
            </Card>
          </div>
        ) : null}

        <div style={{ marginTop: 18 }}>
          <Card>
            {/* Said plainly, because a seller looking for their API key here
                should learn why it is absent rather than assume it is missing. */}
            <p className="card__hint">{t('cour.secretsNote')}</p>
          </Card>
        </div>
      </div>

      {panel?.kind === 'connect' ? (
        <ConnectDrawer
          form={rows.find((row) => row.provider === panel.provider)?.connect_form ?? null}
          account={byProvider.get(panel.provider) ?? null}
          onClose={() => setPanel(null)}
          onConnected={(text, tone) => {
            setNotice({ provider: panel.provider, text, tone });
            const form = rows.find((row) => row.provider === panel.provider)?.connect_form;
            setPanel(form?.supports_store ? { kind: 'store', provider: panel.provider } : null);
            refresh();
          }}
        />
      ) : null}
      {panel?.kind === 'store' ? (
        <StoreDrawer
          provider={panel.provider}
          onClose={() => setPanel(null)}
          onSaved={() => {
            setPanel(null);
            setNotice({ provider: panel.provider, text: t('cour.storeSaved'), tone: 'good' });
            refresh();
          }}
        />
      ) : null}
      {panel?.kind === 'webhook' ? <WebhookDrawer provider={panel.provider} onClose={() => setPanel(null)} /> : null}
    </>
  );
}

function ConnectDrawer({
  form,
  account,
  onClose,
  onConnected,
}: {
  form: ConnectForm | null;
  account: CourierAccount | null;
  onClose: () => void;
  onConnected: (message: string, tone: Tone) => void;
}) {
  const { t, locale } = useSession();
  const [values, setValues] = useState<Record<string, string>>({});
  const [sandbox, setSandbox] = useState(account?.config?.sandbox === true);
  const [label, setLabel] = useState(account?.label ?? '');
  const [webhookSecret, setWebhookSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!form) return null;
  const bn = locale === 'bn';

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ result: string; message: string }>(`/couriers/accounts/${form.provider}/connect`, {
        credentials: values,
        config: form.supports_sandbox ? { sandbox } : {},
        label: label.trim() || undefined,
        webhook_secret: webhookSecret.trim() || undefined,
      });
      // Cleared at once: the keys now live only in the backend vault.
      setValues({});
      setWebhookSecret('');
      const ok = result.result === 'OK' || result.result === 'VALID' || result.result === 'SUCCESS';
      onConnected(result.message || t('cour.connectedOk'), ok ? 'good' : 'warn');
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={`${account ? t('cour.reconnect') : t('cour.connect')} — ${form.display_name}`} onClose={onClose}>
      <form onSubmit={submit} autoComplete="off">
        <p className="card__hint">{t('cour.connectHint')}</p>
        {form.fields.map((field) => (
          <label key={field.name} className="field">
            <span className="field__label">
              {bn ? field.label_bn : field.label_en}
              {field.required ? ' *' : ''}
            </span>
            <input
              className="input"
              type={field.input_type === 'text' ? 'text' : 'password'}
              autoComplete="off"
              spellCheck={false}
              required={field.required}
              maxLength={300}
              value={values[field.name] ?? ''}
              onChange={(event) => setValues((current) => ({ ...current, [field.name]: event.target.value }))}
            />
            {(bn ? field.help_bn : field.help_en) ? <span className="field__hint">{bn ? field.help_bn : field.help_en}</span> : null}
          </label>
        ))}
        {form.uses_webhook && !form.webhook_secret_generated ? (
          <label className="field">
            <span className="field__label">{t('cour.webhookSecret')}</span>
            <input className="input" type="password" autoComplete="off" maxLength={300} value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} />
          </label>
        ) : null}
        <label className="field">
          <span className="field__label">{t('cour.label')}</span>
          <input className="input" maxLength={80} value={label} onChange={(event) => setLabel(event.target.value)} />
        </label>
        {form.supports_sandbox ? (
          <label className="card__hint" style={{ display: 'flex', gap: 6 }}>
            <input type="checkbox" checked={sandbox} onChange={(event) => setSandbox(event.target.checked)} />
            {t('cour.useSandbox')}
          </label>
        ) : null}
        {form.requires_store ? <p className="card__hint">{t('cour.storeNext')}</p> : null}
        {error ? <p className="formerror" role="alert">{error}</p> : null}
        <div className="formactions">
          <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" className="btn btn--primary" disabled={busy}>{busy ? t('cour.checking') : t('cour.connectAndTest')}</button>
        </div>
      </form>
    </Drawer>
  );
}

function StoreDrawer({ provider, onClose, onSaved }: { provider: string; onClose: () => void; onSaved: () => void }) {
  const { t } = useSession();
  const stores = useApi<{ provider_store_id: string; name: string; address: string | null }[]>(`/couriers/accounts/${provider}/stores`);
  const [chosen, setChosen] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    const store = stores.data?.find((row) => row.provider_store_id === chosen);
    if (!store) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/couriers/accounts/${provider}/store`, { provider_store_id: store.provider_store_id, name: store.name });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('cour.chooseStore')} onClose={onClose}>
      <p className="card__hint">{t('cour.storeHint')}</p>
      {stores.error ? <ErrorState error={stores.error} onRetry={stores.reload} /> : null}
      {stores.loading ? <p className="card__hint">{t('common.loading')}</p> : null}
      <form onSubmit={save}>
        {(stores.data ?? []).map((store) => (
          <label key={store.provider_store_id} className="choice" style={{ display: 'flex', gap: 8, padding: '8px 0' }}>
            <input type="radio" name="store" checked={chosen === store.provider_store_id} onChange={() => setChosen(store.provider_store_id)} />
            <span>
              <strong>{store.name}</strong>
              {store.address ? <span className="table__sub" style={{ display: 'block' }}>{store.address}</span> : null}
            </span>
          </label>
        ))}
        {stores.data && stores.data.length === 0 ? <p className="card__hint">{t('cour.noStores')}</p> : null}
        {error ? <p className="formerror">{error}</p> : null}
        <div className="formactions">
          <button type="submit" className="btn btn--primary" disabled={busy || !chosen}>{t('set.save')}</button>
        </div>
      </form>
    </Drawer>
  );
}

function WebhookDrawer({ provider, onClose }: { provider: string; onClose: () => void }) {
  const { t, locale } = useSession();
  const setup = useApi<WebhookSetup>(`/couriers/accounts/${provider}/webhook`);
  const data = setup.data;
  return (
    <Drawer title={t('cour.webhook')} onClose={onClose}>
      {setup.error ? <ErrorState error={setup.error} onRetry={setup.reload} /> : null}
      {data ? (
        <>
          <Row label={t('cour.webhookSupported')} value={data.supported ? t('cour.yes') : t('cour.no')} />
          {data.callback_url ? (
            <Row
              label={t('cour.callbackUrl')}
              value={
                <>
                  <code style={{ wordBreak: 'break-all' }}>{data.callback_url}</code>{' '}
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => void navigator.clipboard?.writeText(data.callback_url ?? '')}>
                    {t('od.copy')}
                  </button>
                </>
              }
            />
          ) : null}
          <Row label={t('cour.webhookSecret')} value={data.secret_configured ? <Chip label={t('cour.configured')} tone="good" /> : <Chip label={t('cour.notConfigured')} tone="warn" />} />
          {(locale === 'bn' ? data.help_bn : data.help_en) ? <p className="card__hint" style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>{locale === 'bn' ? data.help_bn : data.help_en}</p> : null}
        </>
      ) : null}
    </Drawer>
  );
}

function describeAccount(
  account: CourierAccount | undefined,
  provider: ProviderInfo,
): { labelKey: StringKey; tone: Tone } {
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
