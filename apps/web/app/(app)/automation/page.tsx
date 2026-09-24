'use client';

import Link from 'next/link';
import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, EmptyState, ErrorState, Row, Tile } from '@/components/ui';
import { useAutomationLabels } from '@/components/WorkflowBuilder';
import { api, ApiError } from '@/lib/api';
import { statusTone, type Catalog, type Metrics, type Recipe, type Run, type RunDetail, type Workflow } from '@/lib/automation';
import { formatDate } from '@/lib/money';
import { useApi } from '@/lib/useApi';

type Tab = 'workflows' | 'recipes' | 'runs' | 'failures' | 'tasks';
type Task = { id: string; text_en: string; text_bn: string; due_at: string; completed_at: string | null };

export default function AutomationPage() {
  const labels = useAutomationLabels();
  const { t, locale } = labels;
  const [tab, setTab] = useState<Tab>('workflows');
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const catalog = useApi<Catalog>('/automation/catalog');
  const metrics = useApi<Metrics>('/automation/metrics', { days: 7 });
  const workflows = useApi<{ items: Workflow[] }>('/automation/workflows');
  const manage = !!catalog.data?.can_manage;
  const operate = !!catalog.data?.can_operate;

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await work();
      workflows.reload();
      metrics.reload();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const m = metrics.data;
  const top = m?.top_failures[0];
  return (
    <>
      <PageHeader
        title={t('auto.title')}
        subtitle={t('auto.subtitle')}
        actions={
          manage ? (
            <Link className="btn btn--primary" href="/automation/new">
              {t('auto.new')}
            </Link>
          ) : null
        }
      />
      <div className="content">
        {catalog.error ? <ErrorState error={catalog.error} onRetry={catalog.reload} /> : null}
        {error ? <ProblemNote error={error} /> : null}
        {notice ? <p className="card__hint">{notice}</p> : null}
        {catalog.data && !manage ? <p className="card__hint">{t('auto.readOnly')}</p> : null}
        <div className="tiles">
          <Tile label={t('auto.metrics.runs')} value={String(m?.executions ?? '—')} hint={t('auto.metrics.period', { days: 7 })} />
          <Tile label={t('auto.metrics.succeeded')} value={String(m?.succeeded ?? '—')} />
          <Tile label={t('auto.metrics.failed')} value={String(m?.failed ?? '—')} />
          <Tile label={t('auto.metrics.waiting')} value={String(m?.waiting ?? '—')} />
          <Tile
            label={t('auto.metrics.duration')}
            value={m?.average_duration_seconds == null ? '—' : m.average_duration_seconds < 120 ? t('auto.seconds', { n: m.average_duration_seconds }) : t('auto.minutes', { n: Math.round(m.average_duration_seconds / 60) })}
            hint={top?.reason ? `${t('auto.metrics.topFailure')}: ${labels.reason(top.reason)}` : undefined}
          />
        </div>

        <div className="toolbar" role="tablist" style={{ padding: 0, marginBottom: 12 }}>
          {(['workflows', 'recipes', 'runs', 'failures', 'tasks'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`btn btn--sm${tab === value ? ' btn--primary' : ''}`}
              onClick={() => setTab(value)}
            >
              {t(`auto.tab.${value}`)}
            </button>
          ))}
        </div>

        {tab === 'workflows' ? (
          <Card title={t('auto.tab.workflows')} padded={false}>
            {workflows.error ? <ErrorState error={workflows.error} onRetry={workflows.reload} /> : null}
            {workflows.data && workflows.data.items.length === 0 ? (
              <EmptyState title={t('auto.empty')} hint={t('auto.emptyHint')} />
            ) : (
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('auto.col.workflow')}</th>
                      <th>{t('auto.col.trigger')}</th>
                      <th>{t('auto.col.status')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {workflows.data?.items.map((row) => {
                      const r = row.runs_7d ?? {};
                      return (
                        <tr key={row.id}>
                          <td>
                            <Link href={`/automation/${row.id}`}>{row.name}</Link>
                            <div className="card__hint">
                              {t('auto.runs7d', { ok: r.SUCCEEDED ?? 0, failed: r.FAILED ?? 0, waiting: r.WAITING ?? 0 })}
                            </div>
                          </td>
                          <td>{labels.trigger(row.trigger)}</td>
                          <td>
                            <Chip label={row.enabled ? t('auto.enabled') : t('auto.disabled')} tone={row.enabled ? 'good' : 'neutral'} />
                            <div className="card__hint">
                              {row.published_version ? t('auto.version', { n: row.published_version }) : t('auto.notPublished')}
                              {row.published_version && row.has_unpublished_changes ? ` · ${t('auto.unpublished')}` : ''}
                            </div>
                          </td>
                          <td>
                            {manage && row.published_version ? (
                              <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void act(() => api.patch(`/automation/workflows/${row.id}`, { enabled: !row.enabled }))}>
                                {row.enabled ? t('auto.disable') : t('auto.enable')}
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        ) : null}

        {tab === 'recipes' ? (
          <Recipes
            catalog={catalog.data}
            manage={manage}
            busy={busy}
            onInstall={(key, body) =>
              act(async () => {
                const result = await api.post<Workflow & { blocker: string | null }>(`/automation/recipes/${key}/install`, body);
                setNotice(result.blocker ? t('auto.recipe.draft', { reason: labels.reason(result.blocker) }) : t('auto.recipe.done'));
              })
            }
            locale={locale === 'en' ? 'en' : 'bn'}
          />
        ) : null}

        {tab === 'runs' || tab === 'failures' ? (
          <Runs key={tab} status={tab === 'failures' ? 'FAILED' : ''} onOpen={setOpen} />
        ) : null}

        {tab === 'tasks' ? <Tasks operate={operate} /> : null}
      </div>
      {open ? <RunDrawer id={open} operate={operate} onClose={() => setOpen(null)} onChanged={() => { workflows.reload(); metrics.reload(); }} /> : null}
    </>
  );
}

function ProblemNote({ error }: { error: unknown }) {
  const labels = useAutomationLabels();
  if (error instanceof ApiError) {
    const code = error.details?.blocker ?? (Array.isArray(error.details?.problems) ? error.details?.problems[0] : null);
    return <ErrorState error={new ApiError(error.status, error.code, typeof code === 'string' ? labels.reason(code) : error.message)} />;
  }
  return <ErrorState error={error} />;
}

function Recipes({
  catalog,
  manage,
  busy,
  onInstall,
  locale,
}: {
  catalog: Catalog | null;
  manage: boolean;
  busy: boolean;
  onInstall: (key: string, body: Record<string, unknown>) => Promise<void>;
  locale: 'en' | 'bn';
}) {
  const labels = useAutomationLabels();
  const { t } = labels;
  const recipes = useApi<{ items: Recipe[] }>('/automation/recipes');
  const [provider, setProvider] = useState('');
  const [channel, setChannel] = useState('EMAIL');
  const couriers = (catalog?.couriers ?? []).filter((c) => c !== 'manual');
  return (
    <Card title={t('auto.tab.recipes')}>
      {recipes.error ? <ErrorState error={recipes.error} onRetry={recipes.reload} /> : null}
      {recipes.data?.items.map((recipe) => (
        <article key={recipe.key} style={{ borderBottom: '1px solid var(--stroke)', padding: '12px 0' }}>
          <h3>{recipe.name[locale]}</h3>
          <p className="card__hint">{labels.trigger(recipe.trigger)}</p>
          {recipe.installed ? (
            <Chip label={t('auto.recipe.installed')} tone="good" />
          ) : manage ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {recipe.needs.includes('provider') ? (
                <select className="select" aria-label={t('auto.recipe.provider')} value={provider} onChange={(e) => setProvider(e.target.value)}>
                  <option value="">{t('auto.recipe.provider')}</option>
                  {(couriers.length ? couriers : ['steadfast', 'pathao', 'redx']).map((c) => (
                    <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                  ))}
                </select>
              ) : null}
              {recipe.needs.includes('channel') ? (
                <select className="select" aria-label={t('auto.recipe.channel')} value={channel} onChange={(e) => setChannel(e.target.value)}>
                  <option value="EMAIL">Email</option>
                  <option value="WHATSAPP">WhatsApp</option>
                </select>
              ) : null}
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={busy || (recipe.needs.includes('provider') && !provider)}
                onClick={() =>
                  void onInstall(recipe.key, {
                    locale,
                    channel,
                    ...(recipe.needs.includes('provider') ? { provider } : {}),
                  }).then(recipes.reload)
                }
              >
                {t('auto.recipe.install')}
              </button>
            </div>
          ) : null}
        </article>
      ))}
    </Card>
  );
}

function Runs({ status, onOpen }: { status: string; onOpen: (id: string) => void }) {
  const labels = useAutomationLabels();
  const { t, locale } = labels;
  const [offset, setOffset] = useState(0);
  const page = useApi<{ items: Run[]; next_offset: number | null }>('/automation/executions', { status: status || undefined, offset });
  const rows = page.data?.items ?? [];
  return (
    <Card title={t(status ? 'auto.tab.failures' : 'auto.tab.runs')} padded={false}>
      {page.error ? <ErrorState error={page.error} onRetry={page.reload} /> : null}
      {page.data && rows.length === 0 ? (
        <EmptyState title={t(status ? 'auto.run.noFailures' : 'auto.run.none')} />
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('auto.col.workflow')}</th>
                <th>{t('auto.col.status')}</th>
                <th>{t('auto.col.step')}</th>
                <th>{t('auto.col.reason')}</th>
                <th>{t('auto.col.when')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="table__row--clickable" onClick={() => onOpen(row.id)}>
                  <td className="table__primary">
                    {row.workflow_name ?? '—'}
                    {row.source === 'TEST' ? <div className="card__hint">{t('auto.run.test')}</div> : null}
                  </td>
                  <td>
                    <Chip label={labels.status(row.status)} tone={statusTone(row.status)} />
                    {row.waiting_for ? <div className="card__hint">{t('auto.run.waitingFor', { event: labels.event(row.waiting_for) })}</div> : null}
                  </td>
                  <td>{row.current_step ?? '—'}</td>
                  <td>{labels.reason(row.last_error)}</td>
                  <td>{formatDate(row.created_at, { locale, withTime: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="card__body" style={{ display: 'flex', gap: 8 }}>
        {offset > 0 ? (
          <button type="button" className="btn btn--sm" onClick={() => setOffset(Math.max(0, offset - 50))}>←</button>
        ) : null}
        {page.data?.next_offset ? (
          <button type="button" className="btn btn--sm" onClick={() => setOffset(page.data?.next_offset ?? 0)}>
            {t('auto.run.more')}
          </button>
        ) : null}
        <button type="button" className="btn btn--sm" onClick={page.reload}>↻</button>
      </div>
    </Card>
  );
}

function RunDrawer({
  id,
  operate,
  onClose,
  onChanged,
}: {
  id: string;
  operate: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const labels = useAutomationLabels();
  const { t, locale } = labels;
  const run = useApi<RunDetail>(`/automation/executions/${id}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const data = run.data;
  async function act(path: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post(path);
      run.reload();
      onChanged();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Drawer title={t('auto.run.detail')} onClose={onClose}>
      {run.error ? <ErrorState error={run.error} onRetry={run.reload} /> : null}
      {error ? <ProblemNote error={error} /> : null}
      {data ? (
        <>
          <Row label={t('auto.col.workflow')} value={data.workflow_name ?? '—'} />
          <Row label={t('auto.col.trigger')} value={data.trigger ? labels.trigger(data.trigger) : '—'} />
          <Row label={t('auto.col.status')} value={<Chip label={labels.status(data.status)} tone={statusTone(data.status)} />} />
          <Row label={t('auto.run.version')} value={String(data.version)} />
          {data.order_id ? <Row label={t('auto.run.order')} value={<Link href={`/orders/${data.order_id}`}>{data.order_id.slice(0, 8)}</Link>} /> : null}
          {data.depth ? <Row label={t('auto.run.depth')} value={String(data.depth)} /> : null}
          {data.last_error ? <Row label={t('auto.col.reason')} value={labels.reason(data.last_error)} /> : null}
          {data.status === 'FAILED' ? <Row label={t('auto.col.status')} value={data.retryable ? t('auto.run.retryable') : t('auto.run.notRetryable')} /> : null}
          {data.next_attempt_at ? <Row label={t('auto.col.when')} value={t('auto.run.nextAt', { when: formatDate(data.next_attempt_at, { locale, withTime: true }) })} /> : null}
          <div style={{ display: 'flex', gap: 8, margin: '12px 0' }}>
            {operate && data.status === 'FAILED' && data.retryable ? (
              <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void act(`/automation/executions/${id}/retry`)}>
                {t('auto.run.retry')}
              </button>
            ) : null}
            {operate && (data.status === 'QUEUED' || data.status === 'WAITING') ? (
              <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void act(`/automation/executions/${id}/cancel`)}>
                {t('auto.run.cancel')}
              </button>
            ) : null}
          </div>
          <h3>{t('auto.run.steps')}</h3>
          <ol>
            {data.steps.map((step) => (
              <li key={step.step_id}>
                <strong>{step.action ? labels.action(step.action) : step.event ? labels.event(step.event) : t(`auto.step.${step.kind}` as never)}</strong>{' '}
                <Chip label={labels.status(step.status)} tone={statusTone(step.status)} />{' '}
                {step.outcome && !['THEN', 'ELSE', 'EVENT', 'TIMEOUT'].includes(step.outcome) && !/^\d{4}-/.test(step.outcome) ? labels.reason(step.outcome) : step.outcome === 'THEN' || step.outcome === 'ELSE' ? t(`auto.builder.${step.outcome === 'THEN' ? 'then' : 'else'}`) : ''}
              </li>
            ))}
          </ol>
          <h3>{t('auto.run.history')}</h3>
          <ul>
            {data.history.map((h) => (
              <li key={h.id}>
                {formatDate(h.created_at, { locale, withTime: true })} · {h.step_id ?? '—'} · {labels.status(h.status)} {h.error ? `· ${labels.reason(h.error)}` : ''}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Drawer>
  );
}

function Tasks({ operate }: { operate: boolean }) {
  const { t, locale } = useAutomationLabels();
  const tasks = useApi<{ items: Task[] }>('/automation/tasks');
  const [busy, setBusy] = useState(false);
  return (
    <Card title={t('auto.tab.tasks')}>
      {tasks.error ? <ErrorState error={tasks.error} onRetry={tasks.reload} /> : null}
      {tasks.data?.items.length === 0 ? <EmptyState title={t('auto.run.none')} /> : null}
      {tasks.data?.items.map((task) => (
        <article key={task.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '6px 0' }}>
          <span>
            {locale === 'bn' ? task.text_bn : task.text_en} · {formatDate(task.due_at, { locale, withTime: true })}
          </span>
          {operate ? (
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void api.patch(`/automation/tasks/${task.id}`, { completed: !task.completed_at }).finally(() => {
                  setBusy(false);
                  tasks.reload();
                });
              }}
            >
              {task.completed_at ? '↺' : '✓'}
            </button>
          ) : null}
        </article>
      ))}
    </Card>
  );
}
