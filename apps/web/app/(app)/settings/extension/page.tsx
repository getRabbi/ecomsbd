'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { minutesLeft, type LinkedExtension, type PairingCode } from '@/lib/extension';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

/**
 * Settings → Browser extension (V4 Phase 5).
 *
 * Pairing uses the WP1.1 codes (`POST /v1/devices/pair`: 6 digits, 10
 * minutes, one use); the extension claims one as platform `extension`, which
 * the server refuses while `extension_enabled` is off. Unlinking, health and
 * the kill switch stay in Connections (rule L2): this page only lists.
 */
export default function ExtensionPage() {
  const { t } = useSession();
  const shop = useShop();
  const canEdit = shop.can('settings.manage');
  const linked = useApi<{ extensions: LinkedExtension[] }>(canEdit ? '/devices/extensions' : null);
  const disabled = linked.error instanceof ApiError && linked.error.code === 'FEATURE_DISABLED';

  return (
    <>
      <PageHeader title={t('nav.extension')} subtitle={t('ext.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        {!canEdit ? (
          <Card>
            <p>{t('ext.readOnly')}</p>
          </Card>
        ) : disabled ? (
          <Card>
            <p>{t('ext.notEnabled')}</p>
          </Card>
        ) : linked.error ? (
          <ErrorState error={linked.error} onRetry={linked.reload} />
        ) : linked.data ? (
          <>
            <Pairing onPaired={linked.reload} />
            <Linked extensions={linked.data.extensions} />
          </>
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </div>
    </>
  );
}

function Pairing({ onPaired }: { onPaired: () => void }) {
  const { t } = useSession();
  const [code, setCode] = useState<PairingCode | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const left = code ? minutesLeft(code.expires_at, now) : 0;

  useEffect(() => {
    if (!code) return undefined;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      onPaired();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [code, onPaired]);

  async function pair() {
    setBusy(true);
    setError(null);
    try {
      setCode(await api.post<PairingCode>('/devices/pair'));
      setNow(Date.now());
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t('ext.how')}>
      <ol style={{ margin: '0 0 12px', paddingLeft: 20, display: 'grid', gap: 4 }}>
        <li>{t('ext.how1')}</li>
        <li>{t('ext.how2')}</li>
        <li>{t('ext.how3')}</li>
      </ol>
      <p className="card__hint">{t('ext.privacy')}</p>
      {code && left > 0 ? (
        <div style={{ margin: '12px 0' }}>
          <div className="card__hint">{t('ext.code')}</div>
          <div className="num" style={{ fontSize: 32, letterSpacing: 6, fontWeight: 600 }} aria-live="polite">
            {code.code}
          </div>
          <div className="card__hint">{t('ext.codeHint', { minutes: left })}</div>
        </div>
      ) : code ? (
        <p className="card__hint">{t('ext.codeExpired')}</p>
      ) : null}
      {error ? <ErrorState error={error} onRetry={() => void pair()} /> : null}
      <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void pair()}>
        {t('ext.pair')}
      </button>
    </Card>
  );
}

function Linked({ extensions }: { extensions: LinkedExtension[] }) {
  const { t, locale } = useSession();
  return (
    <Card title={t('ext.linked')} hint={t('ext.manage')}>
      {extensions.length === 0 ? (
        <p className="card__hint">{t('ext.none')}</p>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
          {extensions.map((row) => (
            <li key={row.connection_id}>
              {row.name ?? t('nav.extension')}
              {row.app_version ? ` · v${row.app_version}` : ''}
              <div className="card__hint">
                {row.last_heartbeat_at
                  ? t('ext.lastSeen', { when: formatDate(row.last_heartbeat_at, { locale, withTime: true }) })
                  : t('ext.neverSeen')}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p style={{ marginTop: 10 }}>
        <Link href="/integrations">{t('nav.connections')}</Link>
      </p>
    </Card>
  );
}
