'use client';

import Link from 'next/link';
import { useState } from 'react';

import { Card, Chip, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { homeCard, reasonKey, type AutopilotStatus, type AutopilotToday } from '@/lib/autopilot';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

/**
 * Home → "Autopilot today" (V4 Phase 4): today's orders made by Autopilot,
 * how their confirmations stand, the last five decisions with the reason,
 * and "Pause 1 hour". Renders nothing while Autopilot is unavailable
 * (FEATURE_DISABLED) or has never been on.
 */
export function AutopilotCard() {
  const { t } = useSession();
  const shop = useShop();
  const status = useApi<AutopilotStatus>(shop.can('order.view') ? '/autopilot' : null);
  const today = useApi<AutopilotToday>(status.data ? '/autopilot/today' : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (!status.data) return null;
  const view = homeCard(status.data, today.data, (permission) => shop.can(permission));
  if (!view.show) return null;

  async function press(path: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post(path);
      status.reload();
      today.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const d = today.data;
  return (
    <Card
      title={t('ap.home')}
      actions={
        view.canPause ? (
          <button className="btn btn--sm" disabled={busy} onClick={() => void press('/autopilot/pause')}>
            {t('ap.pause')}
          </button>
        ) : view.canResume ? (
          <button className="btn btn--sm" disabled={busy} onClick={() => void press('/autopilot/resume')}>
            {t('ap.resume')}
          </button>
        ) : null
      }
    >
      {error ? <ErrorState error={error} /> : null}
      {view.paused ? <p className="card__hint">{t('ap.pausedNote')}</p> : null}
      {d ? (
        <>
          <div className="minitiles">
            <Tile label={t('ap.made')} value={d.converted} />
            <Tile label={t('ap.confirmed')} value={d.confirmed} />
            <Tile label={t('ap.cancelled')} value={d.cancelled} />
            <Tile label={t('ap.waiting')} value={d.waiting} />
          </div>
          <p className="card__hint" style={{ marginTop: 12 }}>
            {t('ap.recent')}
          </p>
          {d.recent.length === 0 ? (
            <p className="card__hint">{t('ap.none')}</p>
          ) : (
            <ul className="linklist">
              {d.recent.map((row) => (
                <li key={row.draft_id}>
                  {row.outcome === 'CONVERTED' && row.order_id ? (
                    <Link href={`/orders?order=${row.order_id}`} className="linklist__item">
                      <Chip label="✓" tone="good" />
                      <span>{t('ap.madeOrder', { number: row.order_number ?? '' })}</span>
                    </Link>
                  ) : (
                    <Link href="/inbox" className="linklist__item">
                      <Chip label="•" tone="warn" />
                      <span>{t('ap.heldFor', { reason: t(reasonKey(row.reason)) })}</span>
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : today.error ? (
        <ErrorState error={today.error} onRetry={today.reload} />
      ) : (
        <p className="card__hint">{t('common.loading')}</p>
      )}
    </Card>
  );
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="mini">
      <span className="tile__label">{label}</span>
      <span className="mini__value">{value}</span>
    </div>
  );
}
