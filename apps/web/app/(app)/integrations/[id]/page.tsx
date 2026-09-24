'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, use, useEffect, useState } from 'react';

import {
  CopyValue,
  EventsTable,
  HealthChip,
  problemText,
  ShowOnce,
  useIntegrationLabels,
} from '@/components/Integrations';
import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState, Row } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import {
  MAX_DAYS,
  SNIPPET_LANGS,
  snippet,
  statusTone,
  type CustomSetup,
  type Detail,
  type Recipe,
  type SnippetLang,
  type SnippetPart,
  type SyncRun,
  type TestResult,
} from '@/lib/integrations';
import { useApi } from '@/lib/useApi';

export default function IntegrationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense>
      <IntegrationDetail key={id} id={id} />
    </Suspense>
  );
}

type Act = (work: () => Promise<unknown>) => Promise<void>;

function IntegrationDetail({ id }: { id: string }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const result = useSearchParams().get('result');
  const detail = useApi<Detail>(`/integrations/${id}`);
  const recipes = useApi<{ items: Recipe[] }>('/integrations/recipes');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);

  const data = detail.data;
  const conn = data?.connection;
  const manage = !!data?.can_manage;
  const reload = detail.reload;
  const working =
    !!data?.runs.some((r) => r.status === 'QUEUED' || r.status === 'RUNNING') ||
    !!data?.events.some((e) => e.status === 'QUEUED');

  // Imports and queued retries finish within seconds; keep the page current.
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [working, reload]);

  const act: Act = async (work) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      detail.reload();
      recipes.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };

  const runTest = () =>
    act(async () => {
      setTest(await api.post<TestResult>(`/integrations/${id}/test`));
    });

  const problem = problemText(error, labels.code);
  const orderProvider = conn?.provider === 'SHOPIFY' || conn?.provider === 'WOOCOMMERCE';
  const needsConnect =
    !!conn &&
    conn.provider !== 'CUSTOM_WEBSITE' &&
    ['PENDING', 'AUTH_EXPIRED', 'DISCONNECTED'].includes(conn.state);

  return (
    <>
      <PageHeader
        title={conn?.name ?? t('int.title')}
        subtitle={
          conn
            ? `${labels.provider(conn.provider)}${conn.account_name ? ` · ${conn.account_name}` : ''}`
            : undefined
        }
        actions={
          manage && conn ? (
            <span style={{ display: 'flex', gap: 8 }}>
              {!['PENDING', 'DISCONNECTED'].includes(conn.state) ? (
                <button type="button" className="btn" disabled={busy} onClick={() => void runTest()}>
                  {busy ? t('int.testing') : t('int.test')}
                </button>
              ) : null}
              {conn.state === 'CONNECTED' ? (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void act(() => api.patch(`/integrations/${id}`, { enabled: false }))}
                >
                  {t('int.disable')}
                </button>
              ) : null}
              {conn.state === 'DISABLED' ? (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void act(() => api.patch(`/integrations/${id}`, { enabled: true }))}
                >
                  {t('int.enable')}
                </button>
              ) : null}
              {conn.state !== 'DISCONNECTED' ? (
                <button
                  type="button"
                  className="btn btn--ghost"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(t('int.disconnectConfirm'))) {
                      void act(() => api.post(`/integrations/${id}/disconnect`));
                    }
                  }}
                >
                  {t('int.disconnect')}
                </button>
              ) : null}
            </span>
          ) : null
        }
      />
      <div className="content">
        <Link href="/integrations">← {t('int.back')}</Link>
        {result ? (
          <Card
            title={labels.code(result)}
            actions={
              result === 'woocommerce' && conn?.state === 'PENDING' && manage ? (
                <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void runTest()}>
                  {t('int.test')}
                </button>
              ) : null
            }
          />
        ) : null}
        {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
        {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
        {secret ? <ShowOnce title={secret.title} value={secret.value} onDone={() => setSecret(null)} /> : null}

        {data && conn ? (
          <>
            {manage ? <Stepper detail={data} recipes={recipes.data?.items ?? []} /> : null}
            <div className="grid2">
              <HealthCard detail={data} test={test} />
              {manage && needsConnect ? <ConnectCard detail={data} busy={busy} act={act} /> : null}
            </div>
            {conn.provider === 'CUSTOM_WEBSITE' && data.custom ? (
              <DeveloperSetup detail={data} setup={data.custom} busy={busy} act={act} onSecret={setSecret} />
            ) : null}
            {orderProvider && manage && conn.state === 'CONNECTED' ? (
              <SyncCard detail={data} busy={busy} act={act} />
            ) : null}
            {orderProvider ? <RunsCard runs={data.runs} /> : null}
            <Card title={t('int.events.title')} padded={false}>
              <EventsTable
                events={data.events}
                canRetry={data.can_retry}
                onChanged={detail.reload}
                emptyTitle={t('int.issues.empty')}
              />
            </Card>
            {manage && conn.provider !== 'MESSENGER' ? (
              <RecipesCard recipes={recipes.data?.items ?? []} busy={busy} act={act} />
            ) : null}
          </>
        ) : null}
      </div>
    </>
  );
}

function Stepper({ detail, recipes }: { detail: Detail; recipes: Recipe[] }) {
  const { t } = useIntegrationLabels();
  const c = detail.connection;
  const custom = detail.custom;
  const website = c.provider === 'CUSTOM_WEBSITE';
  const steps: { key: StringKey; done: boolean }[] = [
    { key: 'int.step.connect', done: website || c.state !== 'PENDING' },
    {
      key: 'int.step.verify',
      done: website ? !!custom?.last_api_call_at : c.state !== 'PENDING' && !!c.account_name,
    },
    {
      key: 'int.step.configure',
      done: website
        ? !!custom?.webhook_url
        : c.provider === 'MESSENGER'
          ? c.state === 'CONNECTED'
          : c.webhook_state === 'ACTIVE' || c.webhook_state === 'NOT_REGISTERED',
    },
    { key: 'int.step.test', done: website ? !!custom?.last_order_at : !!c.last_success_at },
  ];
  if (c.provider === 'SHOPIFY' || c.provider === 'WOOCOMMERCE') {
    steps.push({
      key: 'int.step.sync',
      done: detail.runs.some((r) => r.kind === 'INITIAL' && r.status === 'COMPLETED'),
    });
  }
  if (c.provider !== 'MESSENGER') {
    steps.push({ key: 'int.step.automate', done: recipes.some((r) => r.installed) });
  }
  steps.push({ key: 'int.step.live', done: c.state === 'CONNECTED' });
  const current = steps.findIndex((s) => !s.done);
  return (
    <ol className="stepper" aria-label={t('int.title')}>
      {steps.map((step, index) => (
        <li
          key={step.key}
          className="stepper__item"
          data-state={step.done ? 'done' : index === current ? 'current' : undefined}
        >
          <span className="stepper__index">{step.done ? '✓' : index + 1}</span>
          {t(step.key)}
        </li>
      ))}
    </ol>
  );
}

function HealthCard({ detail, test }: { detail: Detail; test: TestResult | null }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const c = detail.connection;
  const webhookKey = `int.webhook.${c.webhook_state}`;
  return (
    <Card title={t('int.col.status')}>
      <Row label={t('int.col.status')} value={<HealthChip connection={c} />} />
      {c.account_name ? <Row label={t('int.col.connection')} value={c.account_name} /> : null}
      <Row label={t('int.lastSuccess')} value={labels.when(c.last_success_at)} />
      {c.webhook_state ? (
        <Row
          label={t('int.webhookState')}
          value={
            ['ACTIVE', 'FAILING', 'NOT_REGISTERED'].includes(c.webhook_state)
              ? t(webhookKey as StringKey)
              : c.webhook_state
          }
        />
      ) : null}
      <Row label={t('int.lastWebhook')} value={labels.when(c.last_webhook_at)} />
      {c.provider === 'SHOPIFY' || c.provider === 'WOOCOMMERCE' ? (
        <Row label={t('int.lastSync')} value={labels.when(c.last_sync_at)} />
      ) : null}
      <Row
        label={t('int.lastError')}
        value={c.last_error_code ? `${labels.code(c.last_error_code)} · ${labels.when(c.last_error_at)}` : t('int.none')}
      />
      {c.import_from && c.state === 'CONNECTED' ? (
        <p className="card__hint">
          {t('int.importFrom', { date: labels.when(c.import_from) })}
        </p>
      ) : null}
      {c.provider === 'MESSENGER' ? <p className="card__hint">{t('int.messengerNote')}</p> : null}
      {c.state === 'DISCONNECTED' ? <p className="card__hint">{t('int.disconnectedHint')}</p> : null}
      {test ? (
        <div role="status" style={{ marginTop: 12 }}>
          <strong>{test.ok ? t('int.testOk') : t('int.testFailed')}</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {test.checks.map((check) => (
              <li key={check.key} className={check.ok ? 'text--good' : 'text--bad'}>
                {check.ok ? '✓' : '✗'} {t(`int.check.${check.key}` as StringKey)}
                {check.code ? ` — ${labels.code(check.code)}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

function ConnectCard({ detail, busy, act }: { detail: Detail; busy: boolean; act: Act }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const c = detail.connection;
  const [value, setValue] = useState(c.account_id ?? '');
  const [manual, setManual] = useState(!detail.availability.one_click && c.provider === 'WOOCOMMERCE');
  const [keys, setKeys] = useState({ consumer_key: '', consumer_secret: '' });

  if (!detail.availability.available) {
    return (
      <Card title={t('int.setup.title')}>
        <p>{labels.blocker(detail.availability.blocker)}</p>
      </Card>
    );
  }

  const go = (body: Record<string, string>) =>
    act(async () => {
      const result = await api.post<{ authorize_url: string | null; manual?: boolean }>(
        `/integrations/${c.id}/connect`,
        body,
      );
      if (result.authorize_url) {
        // Provider sign-in happens on the provider's own site.
        window.location.assign(result.authorize_url);
      } else if (result.manual) {
        setManual(true);
      }
    });

  if (c.provider === 'MESSENGER') {
    return (
      <Card title={t('int.step.connect')} hint={t('int.about.MESSENGER')}>
        {c.pages && c.pages.length > 0 ? (
          <div style={{ display: 'grid', gap: 8 }}>
            <strong>{t('int.pages.title')}</strong>
            {c.pages.map((page) => (
              <span key={page.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                {page.name}
                <button
                  type="button"
                  className="btn btn--sm btn--primary"
                  disabled={busy}
                  onClick={() => void act(() => api.post(`/integrations/${c.id}/messenger/page`, { page_id: page.id }))}
                >
                  {t('int.pages.use')}
                </button>
              </span>
            ))}
          </div>
        ) : (
          <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void go({})}>
            {busy ? t('int.redirecting') : t('int.connectMeta')}
          </button>
        )}
      </Card>
    );
  }

  const shopify = c.provider === 'SHOPIFY';
  return (
    <Card title={t('int.step.connect')} hint={t(shopify ? 'int.about.SHOPIFY' : 'int.about.WOOCOMMERCE')}>
      <form
        className="wizard__body"
        onSubmit={(event) => {
          event.preventDefault();
          void go(shopify ? { shop: value } : { store_url: value });
        }}
      >
        <label className="field">
          <span className="field__label">{t(shopify ? 'int.shopDomain' : 'int.storeUrl')}</span>
          <input
            className="input"
            required
            maxLength={255}
            value={value}
            placeholder={shopify ? 'mystore.myshopify.com' : 'https://mystore.com'}
            onChange={(event) => setValue(event.target.value)}
          />
          <span className="card__hint">{t(shopify ? 'int.shopHint' : 'int.storeHint')}</span>
        </label>
        {!manual ? (
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? t('int.redirecting') : t(shopify ? 'int.connectShopify' : 'int.connectWoo')}
          </button>
        ) : null}
      </form>
      {!shopify ? (
        <details open={manual} style={{ marginTop: 14 }}>
          <summary>{t('int.manualKeys')}</summary>
          <form
            className="wizard__body"
            style={{ marginTop: 10 }}
            onSubmit={(event) => {
              event.preventDefault();
              void act(async () => {
                await api.post(`/integrations/${c.id}/connect`, { store_url: value });
                await api.post(`/integrations/${c.id}/woocommerce/keys`, keys);
              });
            }}
          >
            <p className="card__hint">{t('int.manualHint')}</p>
            <label className="field">
              <span className="field__label">{t('int.consumerKey')}</span>
              <input
                className="input"
                required
                autoComplete="off"
                pattern="ck_[A-Za-z0-9]{8,96}"
                value={keys.consumer_key}
                onChange={(event) => setKeys({ ...keys, consumer_key: event.target.value.trim() })}
              />
            </label>
            <label className="field">
              <span className="field__label">{t('int.consumerSecret')}</span>
              <input
                className="input"
                required
                type="password"
                autoComplete="off"
                pattern="cs_[A-Za-z0-9]{8,96}"
                value={keys.consumer_secret}
                onChange={(event) => setKeys({ ...keys, consumer_secret: event.target.value.trim() })}
              />
            </label>
            <button type="submit" className="btn btn--primary" disabled={busy || !value}>
              {t('int.saveKeys')}
            </button>
          </form>
        </details>
      ) : null}
    </Card>
  );
}

function SyncCard({ detail, busy, act }: { detail: Detail; busy: boolean; act: Act }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const c = detail.connection;
  const max = MAX_DAYS[c.provider] ?? 30;
  const options = [7, 30, 60, 90, 180, 365].filter((days) => days <= max);
  const [days, setDays] = useState(options[1] ?? options[0]);
  const running = detail.runs.find(
    (run) => run.kind === 'INITIAL' && (run.status === 'QUEUED' || run.status === 'RUNNING'),
  );
  return (
    <Card title={t('int.sync.title')} hint={`${t('int.sync.hint')} ${t('int.sync.maxDays', { days: max })}`}>
      {running ? (
        <div style={{ display: 'grid', gap: 10 }}>
          <span style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Chip label={t(`int.sync.status.${running.status}` as StringKey)} tone={statusTone(running.status)} />
            <span className="table__sub">{progressText(running, t)}</span>
          </span>
          <div className="progress" role="progressbar" aria-busy="true" aria-label={t('int.sync.title')}>
            <div
              className="progress__bar"
              style={
                running.total
                  ? {
                      width: `${Math.min(100, Math.round((100 * processed(running)) / running.total))}%`,
                      animation: 'none',
                    }
                  : undefined
              }
            />
          </div>
          <span>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy}
              onClick={() => void act(() => api.post(`/integrations/${c.id}/sync/${running.id}/cancel`))}
            >
              {t('int.sync.cancel')}
            </button>
          </span>
        </div>
      ) : (
        <form
          style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}
          onSubmit={(event) => {
            event.preventDefault();
            void act(() => api.post(`/integrations/${c.id}/sync`, { days }));
          }}
        >
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="field__label">{t('int.col.when')}</span>
            <select className="select" value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {options.map((option) => (
                <option key={option} value={option}>
                  {t('int.sync.days', { days: option })}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {t('int.sync.start')}
          </button>
        </form>
      )}
    </Card>
  );
}

function processed(run: SyncRun): number {
  return run.imported + run.duplicates + run.skipped + run.failed;
}

function progressText(run: SyncRun, t: ReturnType<typeof useIntegrationLabels>['t']): string {
  const counts = t('int.sync.counts', {
    imported: run.imported,
    duplicates: run.duplicates,
    skipped: run.skipped,
    failed: run.failed,
  });
  const extent = run.total
    ? t('int.sync.of', { done: processed(run), total: run.total })
    : t('int.sync.pages', { pages: run.pages });
  return `${counts} · ${extent}`;
}

function RunsCard({ runs }: { runs: SyncRun[] }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  return (
    <Card title={t('int.sync.history')} padded={false}>
      {runs.length === 0 ? (
        <p className="card__body card__hint">{t('int.sync.none')}</p>
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('int.col.when')}</th>
                <th>{t('int.col.kind')}</th>
                <th>{t('int.col.state')}</th>
                <th>{t('int.col.result')}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <span className="table__primary">{labels.when(run.started_at ?? run.created_at)}</span>
                    <span className="table__sub">
                      {labels.when(run.since)} – {labels.when(run.until)}
                    </span>
                  </td>
                  <td>{t(`int.sync.kind.${run.kind}` as StringKey)}</td>
                  <td>
                    <Chip label={t(`int.sync.status.${run.status}` as StringKey)} tone={statusTone(run.status)} />
                    {run.last_error_code ? (
                      <span className="table__sub">{labels.code(run.last_error_code)}</span>
                    ) : null}
                  </td>
                  <td style={{ whiteSpace: 'normal' }}>{progressText(run, t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function RecipesCard({ recipes, busy, act }: { recipes: Recipe[]; busy: boolean; act: Act }) {
  const labels = useIntegrationLabels();
  const { t, locale } = labels;
  return (
    <Card title={t('int.recipes.title')} hint={t('int.recipes.hint')}>
      <div style={{ display: 'grid', gap: 10 }}>
        {recipes.map((recipe) => (
          <span key={recipe.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
            <span>
              <span className="table__primary">{recipe.name[locale]}</span>
              {!recipe.available ? <span className="table__sub">{labels.blocker(recipe.blocker)}</span> : null}
            </span>
            {recipe.installed ? (
              <Chip label={t('int.recipes.on')} tone="good" />
            ) : (
              <button
                type="button"
                className="btn btn--sm"
                disabled={busy || !recipe.available}
                onClick={() => void act(() => api.post(`/integrations/recipes/${recipe.key}`, { locale }))}
              >
                {t('int.recipes.use')}
              </button>
            )}
          </span>
        ))}
      </div>
    </Card>
  );
}

function DeveloperSetup({
  detail,
  setup,
  busy,
  act,
  onSecret,
}: {
  detail: Detail;
  setup: CustomSetup;
  busy: boolean;
  act: Act;
  onSecret: (secret: { title: string; value: string }) => void;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const c = detail.connection;
  const [url, setUrl] = useState(setup.webhook_url ?? '');
  const [lang, setLang] = useState<SnippetLang>('php');
  const [tested, setTested] = useState(false);
  const open = c.state !== 'DISCONNECTED';
  const parts: { part: SnippetPart; label: StringKey }[] = [
    { part: 'create', label: 'int.dev.part.create' },
    { part: 'verify', label: 'int.dev.part.verify' },
    { part: 'status', label: 'int.dev.part.status' },
  ];
  return (
    <>
      <div className="grid2">
        <Card
          title={t('int.dev.title')}
          hint={t('int.dev.hint')}
          actions={
            open && c.state !== 'CONNECTED' ? (
              <button
                type="button"
                className="btn btn--primary"
                disabled={busy || !setup.key_active}
                onClick={() => void act(() => api.post(`/integrations/${c.id}/go-live`))}
              >
                {t('int.goLive')}
              </button>
            ) : c.state === 'CONNECTED' ? (
              <Chip label={t('int.live')} tone="good" />
            ) : null
          }
        >
          <Row label={t('int.dev.apiBase')} value={<CopyValue value={setup.api_base_url} />} />
          <Row label={t('int.dev.ordersEndpoint')} value={<CopyValue value={setup.orders_endpoint} />} />
          <Row label={t('int.dev.sourceId')} value={<CopyValue value={setup.source_id} />} />
          <Row label={t('int.dev.scopes')} value={setup.scopes.join(', ')} />
          <Row
            label={t('int.dev.key')}
            value={
              setup.key_active
                ? t('int.dev.keyActive', { date: labels.when(setup.key_created_at) })
                : t('int.dev.keyRevoked')
            }
          />
          <Row
            label={t('int.dev.lastCall')}
            value={setup.last_api_call_at ? labels.when(setup.last_api_call_at) : t('int.dev.waitingRequest')}
          />
          <Row
            label={t('int.dev.lastOrder')}
            value={setup.last_order_at ? labels.when(setup.last_order_at) : t('int.dev.waitingOrder')}
          />
          {open ? (
            <p style={{ marginTop: 12 }}>
              <button
                type="button"
                className="btn btn--sm"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm(t('int.dev.rotateConfirm'))) return;
                  void act(async () => {
                    const result = await api.post<{ api_key: string }>(`/integrations/${c.id}/api-key`);
                    onSecret({ title: t('int.dev.keyOnce'), value: result.api_key });
                  });
                }}
              >
                {t('int.dev.rotate')}
              </button>
            </p>
          ) : null}
        </Card>
        <Card title={t('int.webhookState')}>
          {setup.webhook_url ? (
            <>
              <Row label={t('int.dev.webhookUrl')} value={<code style={{ overflowWrap: 'anywhere' }}>{setup.webhook_url}</code>} />
              <Row
                label={t('int.dev.lastDelivery')}
                value={
                  setup.deliveries.last_status
                    ? `${setup.deliveries.last_status} · ${labels.when(setup.deliveries.last_at)}`
                    : t('int.never')
                }
              />
              {setup.deliveries.failed_24h ? (
                <p className="text--bad">{t('int.dev.deliveries', { failed: setup.deliveries.failed_24h })}</p>
              ) : null}
            </>
          ) : null}
          {open ? (
            <form
              className="wizard__body"
              style={{ marginTop: 10 }}
              onSubmit={(event) => {
                event.preventDefault();
                void act(async () => {
                  const result = await api.post<{ signing_secret: string }>(`/integrations/${c.id}/webhook`, { url });
                  onSecret({ title: t('int.dev.secretOnce'), value: result.signing_secret });
                });
              }}
            >
              <label className="field">
                <span className="field__label">{t('int.dev.webhookUrl')}</span>
                <input
                  className="input"
                  type="url"
                  required
                  maxLength={1000}
                  placeholder="https://mystore.com/ecomsbd/webhook"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                />
              </label>
              <span style={{ display: 'flex', gap: 8 }}>
                <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
                  {setup.webhook_url ? t('int.dev.replaceWebhook') : t('int.dev.saveWebhook')}
                </button>
                {setup.webhook_enabled ? (
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        await api.post(`/integrations/${c.id}/webhook/test`);
                        setTested(true);
                      })
                    }
                  >
                    {t('int.dev.sendTest')}
                  </button>
                ) : null}
              </span>
              {tested ? <p className="card__hint">{t('int.dev.testQueued')}</p> : null}
            </form>
          ) : null}
        </Card>
      </div>
      <Card title={t('int.dev.quickstart')} hint={t('int.dev.quickHint')}>
        <div role="tablist" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {SNIPPET_LANGS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={lang === option.id}
              className={`btn btn--sm${lang === option.id ? ' btn--primary' : ''}`}
              onClick={() => setLang(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {parts.map(({ part, label }) => (
          <div key={part} style={{ marginBottom: 14 }}>
            <strong>{t(label)}</strong>
            <pre
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--stroke)',
                borderRadius: 'var(--radius)',
                padding: 12,
                overflowX: 'auto',
                fontSize: 12,
                margin: '6px 0 0',
              }}
            >
              <code>{snippet(lang, part, setup)}</code>
            </pre>
          </div>
        ))}
      </Card>
    </>
  );
}
