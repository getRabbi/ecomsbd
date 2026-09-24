'use client';

import { useState } from 'react';

import { Card, Chip, ErrorState } from '@/components/ui';
import { useAutomationLabels, WorkflowBuilder } from '@/components/WorkflowBuilder';
import { api, ApiError } from '@/lib/api';
import { emptyGroup, type Catalog, type Definition, type Workflow, type WorkflowDetail } from '@/lib/automation';
import { formatDate } from '@/lib/money';
import { useApi } from '@/lib/useApi';

interface Preview {
  entry_conditions: { matched: boolean };
  steps: { id: string; type: string; action?: string; would: string; reason?: string | null; event?: string }[];
}

const STARTER: Definition = {
  trigger: 'order.created',
  conditions: emptyGroup(),
  steps: [],
};

/** Create (no ``initial``) or edit a workflow. */
export function WorkflowEditor({
  initial,
  onSaved,
}: {
  initial?: WorkflowDetail;
  onSaved: (workflow: Workflow) => void;
}) {
  const labels = useAutomationLabels();
  const { t, locale } = labels;
  const catalog = useApi<Catalog>('/automation/catalog');
  const [name, setName] = useState(initial?.name ?? '');
  const [definition, setDefinition] = useState<Definition>(() => {
    const draft = initial?.draft;
    return draft ? { ...draft, conditions: { groups: [], ...draft.conditions } } : STARTER;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [sampleOrder, setSampleOrder] = useState('');
  const [sampleCustomer, setSampleCustomer] = useState('');
  const manage = !!catalog.data?.can_manage;

  async function act<T>(work: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      return await work();
    } catch (failure) {
      setError(failure);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function save(): Promise<Workflow> {
    const body = { name, definition };
    const saved = initial
      ? await api.put<Workflow>(`/automation/workflows/${initial.id}`, body)
      : await api.post<Workflow>('/automation/workflows', body);
    return saved;
  }

  const problems = (() => {
    if (!(error instanceof ApiError)) return [] as string[];
    const list = error.details?.problems;
    if (Array.isArray(list)) return list.map((p) => labels.reason(String(p)));
    if (typeof error.details?.blocker === 'string') return [labels.reason(error.details.blocker)];
    return [];
  })();

  if (!catalog.data) return catalog.error ? <ErrorState error={catalog.error} onRetry={catalog.reload} /> : null;

  const sample = { order_id: sampleOrder.trim() || undefined, customer_id: sampleCustomer.trim() || undefined };
  return (
    <>
      <Card>
        <label className="field">
          {t('auto.builder.name')}
          <input className="input" value={name} maxLength={100} disabled={!manage} onChange={(e) => setName(e.target.value)} />
        </label>
        <WorkflowBuilder value={definition} onChange={setDefinition} catalog={catalog.data} disabled={!manage} />
        {error ? problems.length ? (
          <ul className="card__hint" role="alert">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        ) : (
          <ErrorState error={error} />
        ) : null}
        {notice ? <p className="card__hint" role="status">{notice}</p> : null}
        {manage ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
            <button
              type="button"
              className="btn"
              disabled={busy || !name.trim()}
              onClick={() =>
                void act(async () => {
                  const saved = await save();
                  if (saved) {
                    setNotice(t('auto.builder.saved'));
                    onSaved(saved);
                  }
                })
              }
            >
              {t('auto.builder.save')}
            </button>
            {(['publish', 'publishOff'] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                className={`btn${kind === 'publish' ? ' btn--primary' : ''}`}
                disabled={busy || !name.trim()}
                onClick={() =>
                  void act(async () => {
                    const saved = await save();
                    const published = await api.post<Workflow>(`/automation/workflows/${saved.id}/publish`, { enable: kind === 'publish' });
                    setNotice(t('auto.builder.published', { n: published.published_version ?? 1 }));
                    onSaved(published);
                  })
                }
              >
                {t(`auto.builder.${kind}`)}
              </button>
            ))}
          </div>
        ) : null}
      </Card>

      {initial && manage ? (
        <Card title={t('auto.builder.preview')} hint={t('auto.builder.previewHint')}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input className="input" placeholder={t('auto.builder.sampleOrder')} aria-label={t('auto.builder.sampleOrder')} value={sampleOrder} onChange={(e) => setSampleOrder(e.target.value)} />
            <input className="input" placeholder={t('auto.builder.sampleCustomer')} aria-label={t('auto.builder.sampleCustomer')} value={sampleCustomer} onChange={(e) => setSampleCustomer(e.target.value)} />
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await save();
                  setPreview(await api.post<Preview>(`/automation/workflows/${initial.id}/preview`, sample));
                })
              }
            >
              {t('auto.builder.preview')}
            </button>
            {initial.enabled && initial.published_version ? (
              <button
                type="button"
                className="btn"
                disabled={busy || !(sample.order_id || sample.customer_id)}
                onClick={() => {
                  if (!window.confirm(t('auto.builder.testConfirm'))) return;
                  void act(async () => {
                    await api.post(`/automation/workflows/${initial.id}/run`, { ...sample, confirm: true });
                    setNotice(t('auto.builder.testStarted'));
                  });
                }}
              >
                {t('auto.builder.testRun')}
              </button>
            ) : null}
          </div>
          {preview ? (
            <div style={{ marginTop: 12 }}>
              <h3>{t('auto.preview.title')}</h3>
              <p>{t(preview.entry_conditions.matched ? 'auto.preview.entryMatched' : 'auto.preview.entryMissed')}</p>
              <ol>
                {preview.steps.map((step) => (
                  <li key={step.id}>
                    {step.action ? labels.action(step.action) : step.event ? labels.event(step.event) : t(`auto.step.${step.type}` as never)}{' '}
                    <Chip label={t(`auto.preview.${step.would}` as never)} tone={step.would === 'SKIP' ? 'warn' : 'neutral'} />
                    {step.reason ? ` ${labels.reason(step.reason)}` : ''}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </Card>
      ) : null}

      {initial?.versions.length ? (
        <Card title={t('auto.builder.versions')}>
          <ul>
            {initial.versions.map((v) => (
              <li key={v.number}>
                {t('auto.version', { n: v.number })} · {formatDate(v.published_at, { locale, withTime: true })}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}
