'use client';

import Link from 'next/link';
import { useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import {
  canTurnOn,
  courierProblemKey,
  reasonKey,
  type AutopilotStatus,
  type CheckLine,
} from '@/lib/autopilot';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

/**
 * Settings → Autopilot (V4 Phase 4), against `/v1/autopilot`.
 *
 * Shows the shop-gate checklist exactly as the server computed it, the
 * manual-queue choice and the consent, and turns Autopilot on or off. The
 * server refuses "on" while any line fails (`AUTOPILOT_NOT_READY`) and checks
 * every line again before each order. Needs `settings.manage` and the
 * `autopilot_enabled` flag (FEATURE_DISABLED otherwise).
 */
export default function AutopilotPage() {
  const { t } = useSession();
  const shop = useShop();
  const canEdit = shop.can('settings.manage') && shop.can('automation.manage');
  const status = useApi<AutopilotStatus>(canEdit ? '/autopilot' : null);
  const disabled = status.error instanceof ApiError && status.error.code === 'FEATURE_DISABLED';

  return (
    <>
      <PageHeader title={t('nav.autopilot')} subtitle={t('ap.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        {!canEdit ? (
          <Card>
            <p>{t('ap.readOnly')}</p>
          </Card>
        ) : disabled ? (
          <Card>
            <p>{t('ap.notEnabled')}</p>
          </Card>
        ) : status.error ? (
          <ErrorState error={status.error} onRetry={status.reload} />
        ) : status.data ? (
          <AutopilotPanel status={status.data} onChanged={status.reload} />
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </div>
    </>
  );
}

function lineLabel(t: ReturnType<typeof useSession>['t'], line: CheckLine): string {
  const key = `ap.l.${line.key}` as StringKey;
  if (line.key === 'HISTORY') {
    return t(key, { needed: Number(line.detail.needed ?? 20), confirmed: Number(line.detail.confirmed ?? 0) });
  }
  return t(key);
}

function AutopilotPanel({ status, onChanged }: { status: AutopilotStatus; onChanged: () => void }) {
  const { t, locale } = useSession();
  const [consent, setConsent] = useState(status.consent_at !== null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api.put('/autopilot', body);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const pausedAt = status.paused_until
    ? new Date(status.paused_until).toLocaleTimeString(locale === 'bn' ? 'bn-BD' : 'en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Dhaka',
      })
    : null;

  return (
    <>
      {error ? <ErrorState error={error} /> : null}
      <Card title={t('ap.what')}>
        <ul style={{ display: 'grid', gap: 8, paddingLeft: 18, margin: 0 }}>
          <li>{t('ap.what1')}</li>
          <li>{t('ap.what2')}</li>
          <li>{t('ap.what3')}</li>
        </ul>
      </Card>

      <Card title={t('ap.checklist')} hint={t('ap.checklistHint')}>
        <ul className="linklist">
          {status.checklist.map((line) => (
            <li key={line.key} className="linklist__item">
              <Chip label={line.ok ? '✓' : '!'} tone={line.ok ? 'good' : 'bad'} />
              <span>
                {lineLabel(t, line)}
                {!line.ok && line.key === 'COURIER' ? (
                  <span className="card__hint" style={{ display: 'block' }}>
                    {t(courierProblemKey(line.detail.problem))}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        <label style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <input
            type="checkbox"
            checked={status.manual_queue}
            disabled={busy}
            onChange={(event) => void run({ manual_queue: event.target.checked })}
          />
          <span>{t('ap.manualQueue')}</span>
        </label>
      </Card>

      <Card title={t('nav.autopilot')}>
        <p>{status.on ? t('ap.on') : t('ap.off')}</p>
        {pausedAt ? <p className="card__hint">{t('ap.pausedUntil', { time: pausedAt })}</p> : null}
        {status.on && status.reason ? <p className="card__hint">{t(reasonKey(status.reason))}</p> : null}
        {status.daily_cap !== null ? <p className="card__hint">{t('ap.dailyCap', { cap: status.daily_cap })}</p> : null}
        {!status.workflow.enabled ? (
          <p className="card__hint">
            {t('ap.workflowOff')} <Link href="/automation">{t('ap.workflowLink')}</Link>
          </p>
        ) : null}
        {status.on ? (
          <button className="btn" disabled={busy} onClick={() => void run({ on: false })}>
            {t('ap.turnOff')}
          </button>
        ) : (
          <div style={{ display: 'grid', gap: 10 }}>
            {status.consent_at === null ? (
              <label style={{ display: 'flex', gap: 8 }}>
                <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                <span>{t('ap.consent')}</span>
              </label>
            ) : null}
            <div>
              <button
                className="btn btn--primary"
                disabled={busy || !canTurnOn(status, consent)}
                onClick={() => void run({ on: true, consent })}
              >
                {t('ap.turnOn')}
              </button>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}
