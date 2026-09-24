'use client';

import Link from 'next/link';
import { useState } from 'react';

import { Chip, EmptyState, ErrorState } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { strings, type StringKey } from '@/lib/i18n';
import { healthTone, statusTone, type Connection, type IntegrationEvent } from '@/lib/integrations';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';

function known(key: string): key is StringKey {
  return key in strings.en;
}

/** Labels for server codes, falling back to a readable generic rather than a raw code. */
export function useIntegrationLabels() {
  const { t, locale } = useSession();
  const pick = (prefix: string, value: string | null | undefined, fallback: StringKey) => {
    const key = `${prefix}.${value ?? ''}`;
    return known(key) ? t(key) : t(fallback);
  };
  return {
    t,
    locale,
    health: (value: string) => pick('int.health', value, 'int.health.DEGRADED'),
    code: (value: string | null | undefined) =>
      value ? pick('int.code', value, 'int.code.PROVIDER_REJECTED') : t('int.none'),
    status: (value: string) => pick('int.status', value, 'int.status.IGNORED'),
    provider: (value: string) => pick('int.provider', value, 'int.provider.CUSTOM_WEBSITE'),
    blocker: (value: string | null | undefined) => pick('int.blocker', value, 'int.setup.title'),
    when: (value: string | null | undefined) =>
      value ? formatDate(value, { locale, withTime: true }) : t('int.never'),
  };
}

/** What a failed call means to a seller: the stable code when the API sent one. */
export function problemText(error: unknown, code: (value: string) => string): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    const value = error.details?.code ?? error.details?.blocker;
    if (typeof value === 'string') return code(value);
    return error.message;
  }
  return null;
}

export function HealthChip({ connection }: { connection: Pick<Connection, 'health'> }) {
  const { health } = useIntegrationLabels();
  return <Chip label={health(connection.health)} tone={healthTone(connection.health)} />;
}

/** A secret the API returns exactly once. Nothing here stores it. */
export function ShowOnce({
  title,
  value,
  onDone,
}: {
  title: string;
  value: string;
  onDone: () => void;
}) {
  const { t } = useSession();
  const [copied, setCopied] = useState(false);
  return (
    <section className="card" role="status" style={{ borderColor: 'var(--brand)' }}>
      <div className="card__body" style={{ display: 'grid', gap: 10 }}>
        <strong>{title}</strong>
        <code style={{ overflowWrap: 'anywhere', userSelect: 'all' }}>{value}</code>
        <span style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => {
              void navigator.clipboard?.writeText(value).then(() => setCopied(true));
            }}
          >
            {copied ? t('int.dev.copied') : t('int.dev.copy')}
          </button>
          <button type="button" className="btn btn--primary btn--sm" onClick={onDone}>
            {t('int.dev.saved')}
          </button>
        </span>
      </div>
    </section>
  );
}

export function CopyValue({ value }: { value: string }) {
  const { t } = useSession();
  const [copied, setCopied] = useState(false);
  return (
    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', maxWidth: '100%' }}>
      <code style={{ overflowWrap: 'anywhere' }}>{value}</code>
      <button
        type="button"
        className="btn btn--sm btn--ghost"
        onClick={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}
      >
        {copied ? t('int.dev.copied') : t('int.dev.copy')}
      </button>
    </span>
  );
}

/**
 * Problems and recent activity. Retry puts the same store order back through
 * the same dedupe, so it can finish an import but never duplicate one.
 */
export function EventsTable({
  events,
  connections,
  canRetry,
  onChanged,
  emptyTitle,
}: {
  events: IntegrationEvent[];
  connections?: Connection[];
  canRetry: boolean;
  onChanged: () => void;
  emptyTitle: string;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [queued, setQueued] = useState<Set<string>>(new Set());

  async function act(event: IntegrationEvent, action: 'retry' | 'resolve') {
    setBusy(event.id);
    setError(null);
    try {
      await api.post(`/integrations/events/${event.id}/${action}`);
      if (action === 'retry') setQueued(new Set([...queued, event.id]));
      onChanged();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  if (events.length === 0) {
    return <EmptyState title={emptyTitle} />;
  }
  const name = (id: string) => connections?.find((c) => c.id === id)?.name;
  const problem = problemText(error, labels.code);
  return (
    <>
      {error ? problem ? <p className="formerror">{problem}</p> : <ErrorState error={error} /> : null}
      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('int.col.when')}</th>
              {connections ? <th>{t('int.col.connection')}</th> : null}
              <th>{t('int.col.problem')}</th>
              <th>{t('int.col.order')}</th>
              <th>{t('int.col.state')}</th>
              <th>{t('int.col.action')}</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td>{labels.when(event.updated_at)}</td>
                {connections ? (
                  <td>
                    <Link href={`/integrations/${event.connection_id}`}>
                      {name(event.connection_id) ?? labels.provider(event.provider)}
                    </Link>
                  </td>
                ) : null}
                <td style={{ whiteSpace: 'normal' }}>
                  <span className="table__primary">
                    {event.kind === 'OUTBOUND' && event.operation && known(`int.op.${event.operation}`)
                      ? t(`int.op.${event.operation}` as StringKey)
                      : event.code
                        ? labels.code(event.code)
                        : labels.status(event.status)}
                  </span>
                  {event.kind === 'OUTBOUND' && event.code ? (
                    <span className="table__sub">{labels.code(event.code)}</span>
                  ) : null}
                  {event.attempts > 1 ? (
                    <span className="table__sub">{t('int.times', { count: event.attempts })}</span>
                  ) : null}
                </td>
                <td>{event.external_ref ?? '—'}</td>
                <td>
                  <Chip label={labels.status(event.status)} tone={statusTone(event.status)} />
                </td>
                <td>
                  {queued.has(event.id) ? (
                    <span className="table__sub">{t('int.queued')}</span>
                  ) : event.status === 'FAILED' ? (
                    <span style={{ display: 'inline-flex', gap: 6 }}>
                      {event.action === 'RECONNECT' ? (
                        <Link className="btn btn--sm" href={`/integrations/${event.connection_id}`}>
                          {t('int.reconnect')}
                        </Link>
                      ) : null}
                      {event.retryable && canRetry ? (
                        <button
                          type="button"
                          className="btn btn--sm btn--primary"
                          disabled={busy === event.id}
                          onClick={() => void act(event, 'retry')}
                        >
                          {t('int.retry')}
                        </button>
                      ) : null}
                      {canRetry ? (
                        <button
                          type="button"
                          className="btn btn--sm btn--ghost"
                          disabled={busy === event.id}
                          onClick={() => void act(event, 'resolve')}
                        >
                          {t('int.resolve')}
                        </button>
                      ) : null}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
