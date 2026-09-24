'use client';

/**
 * The structured workflow builder: WHEN [trigger] IF [conditions] THEN [steps].
 *
 * Deliberately not a node canvas. A workflow is a list of steps; a branch holds
 * two nested lists (THEN / ELSE) and both rejoin the step after it, like
 * if/else. The server compiles, validates and versions it; this component only
 * edits the JSON definition.
 */

import type { ReactNode } from 'react';

import {
  defaultConfig,
  emptyGroup,
  flatten,
  newId,
  provides,
  type Catalog,
  type Condition,
  type ConditionGroup,
  type ConditionSet,
  type Definition,
  type Step,
} from '@/lib/automation';
import { strings, type StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';

function known(key: string): key is StringKey {
  return key in strings.en;
}

export function useAutomationLabels() {
  const { t, locale } = useSession();
  const pick = (key: string, fallback: string) => (known(key) ? t(key) : fallback);
  return {
    t,
    locale,
    trigger: (key: string) => pick(`auto.trigger.${key}`, key),
    action: (key: string) => pick(`auto.action.${key}`, key),
    field: (key: string) => pick(`auto.field.${key}`, key),
    op: (key: string) => pick(`auto.op.${key}`, key),
    event: (key: string) => pick(`auto.event.${key}`, key),
    status: (key: string) => pick(`auto.status.${key}`, key),
    /** A seller-safe reason code, possibly "CODE:detail" or "TRANSIENT:Error". */
    reason: (code: string | null | undefined) => {
      if (!code) return '';
      const head = code.split(':')[0] ?? code;
      return pick(`auto.reason.${head}`, pick(`cmp.blocker.${head}`, head));
    },
  };
}

type Labels = ReturnType<typeof useAutomationLabels>;

const ROW: React.CSSProperties = { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 };

// ------------------------------------------------------------- conditions --

function ConditionRow({
  value,
  onChange,
  onRemove,
  catalog,
  allowed,
  labels,
  disabled,
}: {
  value: Condition;
  onChange: (next: Condition) => void;
  onRemove: () => void;
  catalog: Catalog;
  allowed: Set<string>;
  labels: Labels;
  disabled: boolean;
}) {
  const { t } = labels;
  const fields = catalog.fields.filter((f) => f.needs === null || allowed.has(f.needs));
  const spec = catalog.fields.find((f) => f.key === value.field);
  const firstOp = (key: string) => catalog.fields.find((f) => f.key === key)?.ops[0] ?? 'eq';
  const listOp = value.op === 'in' || value.op === 'not_in';
  const noValue = value.op === 'is_true' || value.op === 'is_false';
  const options: { value: string; label: string }[] =
    spec?.kind === 'tag'
      ? catalog.tags.map((tag) => ({ value: tag.id, label: tag.name }))
      : (spec?.values ?? []).map((v) => ({ value: v, label: v }));
  return (
    <div style={ROW}>
      <select
        className="select"
        aria-label={t('auto.col.trigger')}
        value={value.field}
        disabled={disabled}
        onChange={(e) => onChange({ field: e.target.value, op: firstOp(e.target.value), value: null })}
      >
        {fields.map((f) => (
          <option key={f.key} value={f.key}>
            {labels.field(f.key)}
          </option>
        ))}
      </select>
      <select
        className="select"
        aria-label={t('auto.builder.value')}
        value={value.op}
        disabled={disabled}
        onChange={(e) => onChange({ ...value, op: e.target.value, value: null })}
      >
        {(spec?.ops ?? []).map((op) => (
          <option key={op} value={op}>
            {labels.op(op)}
          </option>
        ))}
      </select>
      {noValue ? null : spec?.kind === 'int' ? (
        <input
          className="input"
          type="number"
          min={0}
          aria-label={t('auto.builder.value')}
          value={typeof value.value === 'number' ? value.value : ''}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, value: e.target.value === '' ? null : Number(e.target.value) })}
        />
      ) : options.length && listOp ? (
        <select
          className="select"
          multiple
          aria-label={t('auto.builder.value')}
          value={Array.isArray(value.value) ? value.value : []}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, value: Array.from(e.target.selectedOptions, (o) => o.value) })}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : options.length ? (
        <select
          className="select"
          aria-label={t('auto.builder.value')}
          value={typeof value.value === 'string' ? value.value : ''}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        >
          <option value="">{t('auto.builder.select')}</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="input"
          aria-label={t('auto.builder.value')}
          maxLength={80}
          value={typeof value.value === 'string' ? value.value : ''}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        />
      )}
      {disabled ? null : (
        <button type="button" className="btn" onClick={onRemove}>
          {t('auto.builder.remove')}
        </button>
      )}
    </div>
  );
}

function ConditionList({
  value,
  onChange,
  catalog,
  allowed,
  labels,
  disabled,
}: {
  value: ConditionSet;
  onChange: (next: ConditionSet) => void;
  catalog: Catalog;
  allowed: Set<string>;
  labels: Labels;
  disabled: boolean;
}) {
  const { t } = labels;
  const first = catalog.fields.find((f) => f.needs === null || allowed.has(f.needs));
  return (
    <div>
      <select
        className="select"
        aria-label={t('auto.builder.match.all')}
        value={value.match}
        disabled={disabled}
        onChange={(e) => onChange({ ...value, match: e.target.value as 'all' | 'any' })}
        style={{ marginBottom: 8 }}
      >
        <option value="all">{t('auto.builder.match.all')}</option>
        <option value="any">{t('auto.builder.match.any')}</option>
      </select>
      {value.conditions.map((condition, index) => (
        <ConditionRow
          key={index}
          value={condition}
          catalog={catalog}
          allowed={allowed}
          labels={labels}
          disabled={disabled}
          onChange={(next) => onChange({ ...value, conditions: value.conditions.map((c, i) => (i === index ? next : c)) })}
          onRemove={() => onChange({ ...value, conditions: value.conditions.filter((_, i) => i !== index) })}
        />
      ))}
      {disabled || !first ? null : (
        <button
          type="button"
          className="btn"
          disabled={value.conditions.length >= 10}
          onClick={() =>
            onChange({ ...value, conditions: [...value.conditions, { field: first.key, op: first.ops[0] ?? 'eq', value: null }] })
          }
        >
          {t('auto.builder.addCondition')}
        </button>
      )}
    </div>
  );
}

export function ConditionEditor({
  value,
  onChange,
  catalog,
  allowed,
  disabled,
}: {
  value: ConditionGroup;
  onChange: (next: ConditionGroup) => void;
  catalog: Catalog;
  allowed: Set<string>;
  disabled: boolean;
}) {
  const labels = useAutomationLabels();
  const { t } = labels;
  const groups = value.groups ?? [];
  return (
    <div>
      <ConditionList value={value} onChange={(next) => onChange({ ...value, ...next })} catalog={catalog} allowed={allowed} labels={labels} disabled={disabled} />
      {groups.map((group, index) => (
        <fieldset key={index} style={{ marginTop: 8 }}>
          <legend>
            {t('auto.builder.group')} {index + 1}
          </legend>
          <ConditionList
            value={group}
            catalog={catalog}
            allowed={allowed}
            labels={labels}
            disabled={disabled}
            onChange={(next) => onChange({ ...value, groups: groups.map((g, i) => (i === index ? next : g)) })}
          />
          {disabled ? null : (
            <button type="button" className="btn" onClick={() => onChange({ ...value, groups: groups.filter((_, i) => i !== index) })}>
              {t('auto.builder.remove')}
            </button>
          )}
        </fieldset>
      ))}
      {disabled ? null : (
        <button
          type="button"
          className="btn"
          style={{ marginLeft: 8 }}
          disabled={groups.length >= 5}
          onClick={() => onChange({ ...value, groups: [...groups, { match: 'all', conditions: [] }] })}
        >
          {t('auto.builder.addGroup')}
        </button>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ steps --

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      {label}
      {children}
    </label>
  );
}

function ActionConfig({
  step,
  onChange,
  catalog,
  labels,
  disabled,
}: {
  step: Extract<Step, { type: 'action' }>;
  onChange: (next: Step) => void;
  catalog: Catalog;
  labels: Labels;
  disabled: boolean;
}) {
  const { t } = labels;
  const config = step.config;
  const set = (key: string, value: unknown) => onChange({ ...step, config: { ...config, [key]: value } });
  const text = (key: string, label: string, max = 1000) => (
    <Field label={label} key={key}>
      <input className="input" maxLength={max} value={String(config[key] ?? '')} disabled={disabled} onChange={(e) => set(key, e.target.value)} />
    </Field>
  );
  const hours = (
    <Field label={t('auto.cfg.dueHours')}>
      <input className="input" type="number" min={1} max={720} value={Number(config.due_hours ?? 24)} disabled={disabled} onChange={(e) => set('due_hours', Number(e.target.value))} />
    </Field>
  );
  switch (step.action) {
    case 'SEND_TEMPLATE': {
      const channel = String(config.channel ?? 'EMAIL');
      return (
        <>
          <Field label={t('auto.cfg.channel')}>
            <select className="select" value={channel} disabled={disabled} onChange={(e) => onChange({ ...step, config: { ...config, channel: e.target.value, template_key: catalog.templates.find((tp) => tp.channel === e.target.value)?.key ?? '' } })}>
              <option value="EMAIL">Email</option>
              <option value="WHATSAPP">WhatsApp</option>
            </select>
          </Field>
          <Field label={t('auto.cfg.template')}>
            <select className="select" value={String(config.template_key ?? '')} disabled={disabled} onChange={(e) => set('template_key', e.target.value)}>
              {catalog.templates.filter((tp) => tp.channel === channel).map((tp) => (
                <option key={tp.key} value={tp.key}>{tp.key}</option>
              ))}
            </select>
          </Field>
          <Field label={t('auto.cfg.language')}>
            <select className="select" value={String(config.locale ?? 'bn')} disabled={disabled} onChange={(e) => set('locale', e.target.value)}>
              <option value="bn">বাংলা</option>
              <option value="en">English</option>
            </select>
          </Field>
          <p className="card__hint">{t('auto.cfg.messageNote')}</p>
        </>
      );
    }
    case 'CREATE_FOLLOWUP':
      return (
        <>
          {text('text', t('auto.cfg.text'))}
          {hours}
          <Field label={t('auto.cfg.assignee')}>
            <input className="input" value={String(config.assignee_id ?? '')} disabled={disabled} onChange={(e) => set('assignee_id', e.target.value || null)} />
          </Field>
        </>
      );
    case 'ADD_TAG':
    case 'REMOVE_TAG':
      return (
        <Field label={t('auto.cfg.tag')}>
          <select className="select" value={String(config.tag_id ?? '')} disabled={disabled} onChange={(e) => set('tag_id', e.target.value)}>
            <option value="">{t('auto.builder.select')}</option>
            {catalog.tags.map((tag) => (
              <option key={tag.id} value={tag.id}>{tag.name}</option>
            ))}
          </select>
        </Field>
      );
    case 'SELLER_NOTIFICATION':
      return (
        <>
          {text('title_en', t('auto.cfg.titleEn'), 160)}
          {text('title_bn', t('auto.cfg.titleBn'), 160)}
          {text('text_en', t('auto.cfg.textEn'))}
          {text('text_bn', t('auto.cfg.textBn'))}
          <Field label={t('auto.cfg.audience')}>
            <select className="select" value={String(config.audience ?? 'OPERATIONS')} disabled={disabled} onChange={(e) => set('audience', e.target.value)}>
              {(['OPERATIONS', 'FINANCE', 'INVENTORY'] as const).map((a) => (
                <option key={a} value={a}>{t(`auto.cfg.audience.${a}`)}</option>
              ))}
            </select>
          </Field>
        </>
      );
    case 'CREATE_TASK':
      return (
        <>
          {text('text_en', t('auto.cfg.textEn'))}
          {text('text_bn', t('auto.cfg.textBn'))}
          {hours}
        </>
      );
    case 'BOOK_COURIER':
      return (
        <>
          <Field label={t('auto.cfg.provider')}>
            <select className="select" value={String(config.provider ?? '')} disabled={disabled} onChange={(e) => set('provider', e.target.value)}>
              {['steadfast', 'pathao', 'redx'].map((p) => (
                <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
              ))}
            </select>
          </Field>
          <p className="card__hint">{t('auto.cfg.bookingNote')}</p>
        </>
      );
    case 'CREATE_TEAM_TASK':
      return (
        <>
          {text('text_en', t('auto.cfg.textEn'))}
          {text('text_bn', t('auto.cfg.textBn'))}
          {hours}
          <Field label={t('auto.cfg.assigneeOptional')}>
            <input className="input" value={String(config.assignee_id ?? '')} disabled={disabled} onChange={(e) => set('assignee_id', e.target.value || null)} />
          </Field>
        </>
      );
    case 'CREATE_DRAFT_PO':
      return (
        <>
          <Field label={t('auto.cfg.quantity')}>
            <input className="input" type="number" min={1} max={100000} value={Number(config.quantity ?? 10)} disabled={disabled} onChange={(e) => set('quantity', Number(e.target.value))} />
          </Field>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={Boolean(config.use_suggested)} disabled={disabled} onChange={(e) => set('use_suggested', e.target.checked)} />
            <span>{t('auto.cfg.useSuggested')}</span>
          </label>
          <p className="card__hint">{t('auto.cfg.draftPoNote')}</p>
        </>
      );
    case 'HOLD_FOR_REVIEW':
      return (
        <>
          <Field label={t('auto.cfg.reviewReason')}>
            <select className="select" value={String(config.reason ?? 'FIRST_PARTY_RISK')} disabled={disabled} onChange={(e) => set('reason', e.target.value)}>
              {(['FIRST_PARTY_RISK', 'EXTERNAL_PROVIDER_FACTS', 'PROVIDER_UNAVAILABLE', 'OTHER'] as const).map((r) => (
                <option key={r} value={r}>{t(`auto.cfg.reviewReason.${r}`)}</option>
              ))}
            </select>
          </Field>
          <p className="card__hint">{t('auto.cfg.reviewNote')}</p>
        </>
      );
    case 'PUSH_STORE_STATUS':
      return <p className="card__hint">{t('auto.cfg.storeNote')}</p>;
    case 'SET_ORDER_LABEL':
    case 'TRIGGER_WEBHOOK':
      return text('label', t('auto.cfg.label'), 40);
    default:
      return null;
  }
}

function StepCard({
  step,
  index,
  total,
  path,
  onChange,
  onRemove,
  onMove,
  catalog,
  allowed,
  followups,
  labels,
  disabled,
  depth,
}: {
  step: Step;
  index: number;
  total: number;
  path: string;
  onChange: (next: Step) => void;
  onRemove: () => void;
  onMove: (delta: -1 | 1) => void;
  catalog: Catalog;
  allowed: Set<string>;
  followups: Step[];
  labels: Labels;
  disabled: boolean;
  depth: number;
}) {
  const { t } = labels;
  const actions = catalog.workflow_actions.filter((a) => a.needs === null || allowed.has(a.needs));
  const events = catalog.wait_events.filter((e) => allowed.has(e.subject));
  const title =
    step.type === 'action' ? labels.action(step.action) : t(`auto.step.${step.type}` as StringKey);
  return (
    <div className="card" style={{ padding: 12, marginBottom: 8, borderLeft: '3px solid var(--stroke)' }} data-step={path}>
      <div style={{ ...ROW, justifyContent: 'space-between' }}>
        <strong>
          {index + 1}. {title}
        </strong>
        {disabled ? null : (
          <span style={{ display: 'flex', gap: 4 }}>
            <button type="button" className="btn" aria-label={t('auto.builder.up')} disabled={index === 0} onClick={() => onMove(-1)}>↑</button>
            <button type="button" className="btn" aria-label={t('auto.builder.down')} disabled={index === total - 1} onClick={() => onMove(1)}>↓</button>
            <button type="button" className="btn" onClick={onRemove}>{t('auto.builder.remove')}</button>
          </span>
        )}
      </div>
      {step.type === 'action' ? (
        <>
          <Field label={t('auto.step.action')}>
            <select className="select" value={step.action} disabled={disabled} onChange={(e) => onChange({ ...step, action: e.target.value, config: defaultConfig(e.target.value, catalog, labels.locale === 'en' ? 'en' : 'bn') })}>
              {actions.map((a) => (
                <option key={a.key} value={a.key}>{labels.action(a.key)}</option>
              ))}
            </select>
          </Field>
          <ActionConfig step={step} onChange={onChange} catalog={catalog} labels={labels} disabled={disabled} />
        </>
      ) : null}
      {step.type === 'delay' ? (
        <>
          <Field label={t('auto.step.delay')}>
            <select className="select" value={step.mode} disabled={disabled} onChange={(e) => onChange({ type: 'delay', id: step.id, mode: e.target.value as typeof step.mode, minutes: 60, time: '09:00', day_offset: 0 })}>
              {(['duration', 'until_time', 'followup_due'] as const).map((m) => (
                <option key={m} value={m}>{t(`auto.delay.${m}`)}</option>
              ))}
            </select>
          </Field>
          {step.mode === 'duration' ? (
            <Field label={t('auto.builder.minutes')}>
              <input className="input" type="number" min={1} max={43200} value={step.minutes ?? 60} disabled={disabled} onChange={(e) => onChange({ ...step, minutes: Number(e.target.value) })} />
            </Field>
          ) : null}
          {step.mode === 'until_time' ? (
            <>
              <Field label={t('auto.builder.at')}>
                <input className="input" type="time" value={step.time ?? '09:00'} disabled={disabled} onChange={(e) => onChange({ ...step, time: e.target.value })} />
              </Field>
              <label>
                <input type="checkbox" checked={step.day_offset === 1} disabled={disabled} onChange={(e) => onChange({ ...step, day_offset: e.target.checked ? 1 : 0 })} /> {t('auto.builder.dayOffset')}
              </label>
            </>
          ) : null}
          {step.mode === 'followup_due' ? (
            <Field label={t('auto.builder.followupStep')}>
              <select className="select" value={step.step ?? ''} disabled={disabled} onChange={(e) => onChange({ ...step, step: e.target.value })}>
                <option value="">{t('auto.builder.select')}</option>
                {followups.map((f) => (
                  <option key={f.id} value={f.id}>{f.id}</option>
                ))}
              </select>
            </Field>
          ) : null}
        </>
      ) : null}
      {step.type === 'wait_event' ? (
        <>
          <Field label={t('auto.step.wait_event')}>
            <select className="select" value={step.event} disabled={disabled} onChange={(e) => onChange({ ...step, event: e.target.value })}>
              {events.map((e) => (
                <option key={e.key} value={e.key}>{labels.event(e.key)}</option>
              ))}
            </select>
          </Field>
          <Field label={t('auto.builder.timeout')}>
            <input className="input" type="number" min={1} max={43200} value={step.timeout_minutes} disabled={disabled} onChange={(e) => onChange({ ...step, timeout_minutes: Number(e.target.value) })} />
          </Field>
          <Field label={t('auto.builder.onTimeout')}>
            <select className="select" value={step.on_timeout} disabled={disabled} onChange={(e) => onChange({ ...step, on_timeout: e.target.value as 'continue' | 'stop' })}>
              <option value="continue">{t('auto.builder.onTimeout.continue')}</option>
              <option value="stop">{t('auto.builder.onTimeout.stop')}</option>
            </select>
          </Field>
        </>
      ) : null}
      {step.type === 'branch' ? (
        <>
          <p><strong>{t('auto.builder.if')}</strong></p>
          <ConditionEditor value={step.conditions} onChange={(next) => onChange({ ...step, conditions: next })} catalog={catalog} allowed={allowed} disabled={disabled} />
          <p><strong>{t('auto.builder.then')}</strong></p>
          <StepList steps={step.then} onChange={(next) => onChange({ ...step, then: next })} catalog={catalog} allowed={allowed} followups={followups} disabled={disabled} depth={depth + 1} path={`${path}.then`} />
          <p><strong>{t('auto.builder.else')}</strong></p>
          <StepList steps={step.else} onChange={(next) => onChange({ ...step, else: next })} catalog={catalog} allowed={allowed} followups={followups} disabled={disabled} depth={depth + 1} path={`${path}.else`} />
        </>
      ) : null}
    </div>
  );
}

export function StepList({
  steps,
  onChange,
  catalog,
  allowed,
  followups,
  disabled,
  depth = 1,
  path = 'steps',
}: {
  steps: Step[];
  onChange: (next: Step[]) => void;
  catalog: Catalog;
  allowed: Set<string>;
  followups: Step[];
  disabled: boolean;
  depth?: number;
  path?: string;
}) {
  const labels = useAutomationLabels();
  const { t } = labels;
  const firstAction = catalog.workflow_actions.find((a) => a.needs === null || allowed.has(a.needs))?.key ?? 'SELLER_NOTIFICATION';
  const firstEvent = catalog.wait_events.find((e) => allowed.has(e.subject))?.key;
  const add = (step: Step) => onChange([...steps, step]);
  const move = (index: number, delta: -1 | 1) => {
    const next = [...steps];
    const [item] = next.splice(index, 1);
    if (!item) return;
    next.splice(index + delta, 0, item);
    onChange(next);
  };
  return (
    <div style={{ marginLeft: depth > 1 ? 16 : 0 }}>
      {steps.map((step, index) => (
        <StepCard
          key={step.id}
          step={step}
          index={index}
          total={steps.length}
          path={`${path}.${index}`}
          catalog={catalog}
          allowed={allowed}
          followups={followups}
          labels={labels}
          disabled={disabled}
          depth={depth}
          onChange={(next) => onChange(steps.map((s, i) => (i === index ? next : s)))}
          onRemove={() => onChange(steps.filter((_, i) => i !== index))}
          onMove={(delta) => move(index, delta)}
        />
      ))}
      {disabled ? null : (
        <div style={ROW}>
          <button type="button" className="btn" onClick={() => add({ type: 'action', id: newId('act'), action: firstAction, config: defaultConfig(firstAction, catalog, labels.locale === 'en' ? 'en' : 'bn') })}>
            + {t('auto.builder.addStep')}
          </button>
          <button type="button" className="btn" onClick={() => add({ type: 'delay', id: newId('wait'), mode: 'duration', minutes: 60 })}>
            + {t('auto.builder.addDelay')}
          </button>
          {firstEvent ? (
            <button type="button" className="btn" onClick={() => add({ type: 'wait_event', id: newId('until'), event: firstEvent, timeout_minutes: 1440, on_timeout: 'continue' })}>
              + {t('auto.builder.addWait')}
            </button>
          ) : null}
          {depth < 4 ? (
            <button type="button" className="btn" onClick={() => add({ type: 'branch', id: newId('if'), conditions: emptyGroup(), then: [], else: [] })}>
              + {t('auto.builder.addBranch')}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function WorkflowBuilder({
  value,
  onChange,
  catalog,
  disabled,
}: {
  value: Definition;
  onChange: (next: Definition) => void;
  catalog: Catalog;
  disabled: boolean;
}) {
  const labels = useAutomationLabels();
  const { t } = labels;
  const allowed = provides(catalog, value.trigger);
  const followups = flatten(value.steps).filter((s) => s.type === 'action' && s.action === 'CREATE_FOLLOWUP');
  return (
    <div>
      <h3>{t('auto.builder.when')}</h3>
      <select
        className="select"
        aria-label={t('auto.builder.when')}
        value={value.trigger}
        disabled={disabled}
        onChange={(e) => onChange({ ...value, trigger: e.target.value, conditions: emptyGroup() })}
      >
        {catalog.workflow_triggers.map((trigger) => (
          <option key={trigger.key} value={trigger.key}>
            {labels.trigger(trigger.key)}
          </option>
        ))}
      </select>
      <h3>{t('auto.builder.if')}</h3>
      <p className="card__hint">{t('auto.builder.ifHint')}</p>
      <ConditionEditor value={value.conditions} onChange={(next) => onChange({ ...value, conditions: next })} catalog={catalog} allowed={allowed} disabled={disabled} />
      <h3>{t('auto.builder.then')}</h3>
      <StepList steps={value.steps} onChange={(next) => onChange({ ...value, steps: next })} catalog={catalog} allowed={allowed} followups={followups} disabled={disabled} />
    </div>
  );
}
