'use client';

import { useState, type FormEvent } from 'react';

import { Card, ErrorState } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { Audience, Campaign, Catalog, Channel, Estimate, Flow } from '@/lib/campaigns';
import { strings, type StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

const FLOWS: Flow[] = ['WIN_BACK', 'REPEAT_NUDGE', 'INACTIVE'];

function known(key: string): key is StringKey {
  return key in strings.en;
}

function numberOrNull(value: string): number | null {
  return value === '' ? null : Number(value);
}

export function useCampaignLabels() {
  const { t, locale } = useSession();
  const pick = (key: string, fallback: string) => (known(key) ? t(key) : fallback);
  return {
    t,
    locale,
    status: (value: string) => pick(`cmp.status.${value}`, value),
    skip: (value: string) => pick(`cmp.skip.${value}`, value),
    message: (value: string) => pick(`cmp.m.${value}`, value),
    blocker: (value: string | null | undefined) =>
      value ? pick(`cmp.blocker.${value}`, pick(`int.blocker.${value}`, value)) : '',
    problem: (error: unknown) => {
      if (error instanceof ApiError) {
        const code = error.details?.blocker ?? error.details?.code;
        if (typeof code === 'string' && known(`cmp.blocker.${code}`)) return t(`cmp.blocker.${code}` as StringKey);
        return error.message;
      }
      return null;
    },
  };
}

export function CampaignBuilder({
  initial,
  kind,
  onSaved,
}: {
  initial?: Campaign;
  kind: 'ONE_OFF' | 'FLOW';
  onSaved: (campaign: Campaign) => void;
}) {
  const labels = useCampaignLabels();
  const { t, locale } = labels;
  const catalog = useApi<Catalog>('/campaigns/catalog');
  const [name, setName] = useState(initial?.name ?? '');
  const [flow, setFlow] = useState<Flow>(initial?.flow ?? 'WIN_BACK');
  const [flowDays, setFlowDays] = useState<number | null>(initial?.flow_days ?? null);
  const [channel, setChannel] = useState<Channel>(initial?.channel ?? 'EMAIL');
  const [template, setTemplate] = useState(initial?.template_key ?? '');
  const [language, setLanguage] = useState<'bn' | 'en'>(initial?.locale ?? (locale === 'en' ? 'en' : 'bn'));
  // `money_allowed` is derived by the server from the segment; never sent back.
  const [audience, setAudience] = useState<Audience>(() => {
    const { money_allowed: _derived, ...rest } = (initial?.audience ?? {}) as Audience & { money_allowed?: boolean };
    void _derived;
    return rest;
  });
  const [rate, setRate] = useState(initial?.rate_per_minute ?? 30);
  const [cap, setCap] = useState(initial?.frequency_cap_hours ?? 72);
  const [attribution, setAttribution] = useState(initial?.attribution_days ?? 7);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [preview, setPreview] = useState<{ subject: string; body: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const data = catalog.data;
  const templates = (data?.templates ?? []).filter((item) => item.channel === channel && !item.builtin);
  const flowLimits = data?.flows[flow];
  const days = flowDays ?? flowLimits?.default_days ?? 60;
  const audienceBody = () => {
    const body: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(audience)) {
      if (key === 'money_allowed') continue;
      if (value !== null && value !== undefined && value !== '') body[key] = value;
    }
    return body;
  };

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const checkAudience = () =>
    run(async () => {
      setEstimate(
        await api.post<Estimate>('/campaigns/estimate', {
          channel,
          audience: audienceBody(),
          ...(kind === 'FLOW' ? { flow, flow_days: days } : {}),
        }),
      );
    });

  const showPreview = () =>
    run(async () => {
      if (!template) return;
      setPreview(await api.post('/campaigns/preview', { template_key: template, locale: language, channel }));
    });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const body = {
        name,
        kind,
        channel,
        template_key: template,
        locale: language,
        audience: audienceBody(),
        rate_per_minute: rate,
        frequency_cap_hours: cap,
        attribution_days: attribution,
        ...(kind === 'FLOW' ? { flow, flow_days: days } : {}),
      };
      const saved = initial
        ? await api.put<Campaign>(`/campaigns/${initial.id}`, body)
        : await api.post<Campaign>('/campaigns', body);
      onSaved(saved);
    });
  };

  const setAudienceField = (key: keyof Audience, value: string | number | null) =>
    setAudience({ ...audience, [key]: value });
  const problem = labels.problem(error);

  return (
    <form onSubmit={submit} style={{ display: 'grid', gap: 16 }}>
      {catalog.error ? <ErrorState error={catalog.error} onRetry={catalog.reload} /> : null}
      <Card title={initial ? t('cmp.edit') : t(kind === 'FLOW' ? 'cmp.newFlow' : 'cmp.new')}>
        <div className="grid2">
          <label className="field">
            <span className="field__label">{t('cmp.b.name')}</span>
            <input className="input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('cmp.b.channel')}</span>
            <select
              className="select"
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value as Channel);
                setTemplate('');
                setEstimate(null);
                setPreview(null);
              }}
            >
              <option value="EMAIL">{t('cmp.channel.EMAIL')}</option>
              <option value="WHATSAPP">{t('cmp.channel.WHATSAPP')}</option>
            </select>
          </label>
          {kind === 'FLOW' ? (
            <>
              <label className="field">
                <span className="field__label">{t('cmp.b.flow')}</span>
                <select
                  className="select"
                  value={flow}
                  onChange={(e) => {
                    setFlow(e.target.value as Flow);
                    setFlowDays(null);
                    setEstimate(null);
                  }}
                >
                  {FLOWS.map((value) => (
                    <option key={value} value={value}>
                      {t(`cmp.flow.${value}` as StringKey)}
                    </option>
                  ))}
                </select>
                <span className="card__hint">{t(`cmp.flowAbout.${flow}` as StringKey, { days })}</span>
              </label>
              <label className="field">
                <span className="field__label">{t('cmp.b.flowDays')}</span>
                <input
                  className="input"
                  type="number"
                  min={flowLimits?.min_days}
                  max={flowLimits?.max_days}
                  disabled={flowLimits?.min_days === flowLimits?.max_days}
                  value={days}
                  onChange={(e) => setFlowDays(Number(e.target.value))}
                />
              </label>
            </>
          ) : null}
          <label className="field">
            <span className="field__label">{t('cmp.b.template')}</span>
            <select
              className="select"
              required
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value);
                setPreview(null);
              }}
            >
              <option value="">—</option>
              {templates.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.key}
                  {item.channel === 'WHATSAPP' && item.provider_status ? ` · ${item.provider_status}` : ''}
                </option>
              ))}
            </select>
            {templates.length === 0 && data ? <span className="card__hint">{t('cmp.b.noTemplates')}</span> : null}
          </label>
          <label className="field">
            <span className="field__label">{t('cmp.b.language')}</span>
            <select className="select" value={language} onChange={(e) => setLanguage(e.target.value as 'bn' | 'en')}>
              <option value="bn">বাংলা</option>
              <option value="en">English</option>
            </select>
          </label>
        </div>
        <p style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" className="btn" disabled={busy || !template} onClick={() => void showPreview()}>
            {t('cmp.b.preview')}
          </button>
        </p>
        {preview ? (
          <article className="card" style={{ padding: 14, whiteSpace: 'pre-wrap' }}>
            {preview.subject ? <strong>{preview.subject}</strong> : null}
            <p style={{ margin: '6px 0 0' }}>{preview.body}</p>
          </article>
        ) : null}
      </Card>

      <Card title={t('cmp.b.audience')} hint={t('cmp.b.tagHint')}>
        <div className="grid2">
          <label className="field">
            <span className="field__label">{t('cmp.b.segment')}</span>
            <select
              className="select"
              value={audience.segment ?? ''}
              onChange={(e) => setAudienceField('segment', e.target.value || null)}
            >
              <option value="">{t('cmp.b.anySegment')}</option>
              {data?.segments.map((segment) => (
                <option key={segment} value={segment}>
                  {known(`crm.${segment}`) ? t(`crm.${segment}` as StringKey) : segment}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">{t('cmp.b.tag')}</span>
            <select
              className="select"
              value={audience.tag_id ?? ''}
              onChange={(e) => setAudienceField('tag_id', e.target.value || null)}
            >
              <option value="">{t('cmp.b.anyTag')}</option>
              {data?.tags.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name}
                </option>
              ))}
            </select>
          </label>
          {(
            [
              ['min_orders', 'cmp.b.minOrders'],
              ['max_orders', 'cmp.b.maxOrders'],
              ['last_order_before_days', 'cmp.b.lastBefore'],
              ['last_order_within_days', 'cmp.b.lastWithin'],
            ] as [keyof Audience, StringKey][]
          ).map(([key, label]) => (
            <label className="field" key={key}>
              <span className="field__label">{t(label)}</span>
              <input
                className="input"
                type="number"
                min={key.startsWith('last') ? 1 : 0}
                value={(audience[key] as number | null | undefined) ?? ''}
                onChange={(e) => setAudienceField(key, numberOrNull(e.target.value))}
              />
            </label>
          ))}
        </div>
        <p>
          <button type="button" className="btn" disabled={busy} onClick={() => void checkAudience()}>
            {t('cmp.b.estimate')}
          </button>
        </p>
        {estimate ? (
          <div role="status" style={{ display: 'grid', gap: 4 }}>
            <strong>
              {estimate.reachable} {t('cmp.est.reachable')} · {estimate.matched} {t('cmp.est.matched')}
            </strong>
            {Object.entries(estimate.excluded)
              .filter(([, count]) => count > 0)
              .map(([reason, count]) => (
                <span key={reason} className="card__hint">
                  {labels.skip(reason)}: {count}
                </span>
              ))}
            {estimate.over_limit ? (
              <span className="text--bad">{t('cmp.est.overLimit', { limit: estimate.limit })}</span>
            ) : null}
          </div>
        ) : null}
      </Card>

      <Card
        title={t('cmp.b.pace')}
        hint={
          data
            ? t('cmp.rules', { start: data.limits.quiet_hours[0], end: data.limits.quiet_hours[1] })
            : undefined
        }
      >
        <div className="grid2">
          <label className="field">
            <span className="field__label">{t('cmp.b.rate')}</span>
            <input className="input" type="number" min={1} max={120} value={rate} onChange={(e) => setRate(Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field__label">{t('cmp.b.cap')}</span>
            <input className="input" type="number" min={24} max={720} value={cap} onChange={(e) => setCap(Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field__label">{t('cmp.b.attribution')}</span>
            <input
              className="input"
              type="number"
              min={1}
              max={30}
              value={attribution}
              onChange={(e) => setAttribution(Number(e.target.value))}
            />
          </label>
        </div>
      </Card>

      {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
      <p>
        <button type="submit" className="btn btn--primary" disabled={busy || !data?.can_manage}>
          {t('cmp.b.save')}
        </button>
      </p>
    </form>
  );
}
