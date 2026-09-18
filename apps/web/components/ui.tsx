'use client';

import type { ReactNode } from 'react';

import { ApiError } from '@/lib/api';
import { useSession } from '@/lib/session';

/** Small shared pieces. Deliberately plain — the tables are the product here. */

export type Tone = 'neutral' | 'good' | 'warn' | 'bad';

export function Chip({ label, tone = 'neutral' }: { label: string; tone?: Tone }) {
  return <span className={`chip chip--${tone}`}>{label}</span>;
}

export function Card({
  title,
  hint,
  actions,
  children,
  padded = true,
}: {
  title?: string;
  hint?: string;
  actions?: ReactNode;
  children?: ReactNode;
  padded?: boolean;
}) {
  return (
    <section className="card">
      {title ? (
        <div
          className="card__body"
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            borderBottom: children ? '1px solid var(--stroke)' : undefined,
          }}
        >
          <div>
            <h2 className="card__title">{title}</h2>
            {hint ? <p className="card__hint">{hint}</p> : null}
          </div>
          {actions}
        </div>
      ) : null}
      {children ? <div className={padded ? 'card__body' : undefined}>{children}</div> : null}
    </section>
  );
}

export function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <section className="card">
      <div className="card__body">
        <p className="tile__label">{label}</p>
        <p className="tile__value">{value}</p>
        {hint ? <p className="tile__hint">{hint}</p> : null}
      </div>
    </section>
  );
}

export function LoadingRows({ rows = 6, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <tbody>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <tr key={rowIndex}>
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <td key={columnIndex}>
              <div className="skeleton" style={{ height: 14 }} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="state">
      <p className="state__title">{title}</p>
      {hint ? <p className="state__hint">{hint}</p> : null}
      {action}
    </div>
  );
}

/**
 * An error, rendered as what the server actually said.
 *
 * A permission failure and an outage are different things a seller can do
 * different things about, so they are not flattened into "something went
 * wrong". The API's own message is shown, because it is already written for a
 * seller and already localised by the request's language header.
 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useSession();

  let title = t('common.couldNotLoad');
  let hint = t('err.offline');

  if (error instanceof ApiError) {
    hint = error.message;
    if (error.isPermissionFailure) {
      title = t('err.forbidden');
    }
  }

  return (
    <EmptyState
      title={title}
      hint={hint}
      action={
        onRetry ? (
          <button type="button" className="btn" onClick={onRetry}>
            {t('common.retry')}
          </button>
        ) : null
      }
    />
  );
}

export function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useSession();
  return (
    <>
      <div className="drawer__scrim" onClick={onClose} role="presentation" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer__header">
          <strong>{title}</strong>
          <button type="button" className="btn btn--sm" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </aside>
    </>
  );
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="row">
      <span className="row__label">{label}</span>
      <span className="row__value">{value}</span>
    </div>
  );
}
