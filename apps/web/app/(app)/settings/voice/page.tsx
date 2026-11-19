'use client';

import { useEffect, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { ApiError, api, fetchBlob } from '@/lib/api';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';
import { durationLabel, introOffer, type IntroStatus, type IntroVersion } from '@/lib/voice-intro';

/**
 * Settings → Confirmation call voice (V4 WP3.3), against `/v1/voice/intro`.
 *
 * The seller hears the recording here before calls can play it: playing it is
 * what the server records as the preview, and `turn-on` refuses a recording
 * nobody played. Needs `settings.manage` and the shop's
 * `voice_confirmation_enabled` flag (FEATURE_DISABLED otherwise).
 */
export default function CallVoicePage() {
  const { t } = useSession();
  const shop = useShop();
  const canEdit = shop.can('settings.manage');
  const status = useApi<IntroStatus>(canEdit ? '/voice/intro' : null);
  const disabled = status.error instanceof ApiError && status.error.code === 'FEATURE_DISABLED';

  return (
    <>
      <PageHeader title={t('nav.callVoice')} subtitle={t('vi.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        {!canEdit ? (
          <Card>
            <p>{t('vi.readOnly')}</p>
          </Card>
        ) : disabled ? (
          <Card>
            <p>{t('vi.notEnabled')}</p>
          </Card>
        ) : status.error ? (
          <ErrorState error={status.error} onRetry={status.reload} />
        ) : status.data ? (
          <IntroPanel status={status.data} onChanged={status.reload} />
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </div>
    </>
  );
}

function IntroPanel({ status, onChanged }: { status: IntroStatus; onChanged: () => void }) {
  const { t } = useSession();
  const [heard, setHeard] = useState<Set<number>>(() => new Set());
  const [playing, setPlaying] = useState<{ version: number; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [requested, setRequested] = useState(false);
  const offer = introOffer(status, heard);

  useEffect(() => () => (playing ? URL.revokeObjectURL(playing.url) : undefined), [playing]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function listen(version: number) {
    setError(null);
    try {
      const blob = await fetchBlob(`/voice/intro/${version}/audio`);
      setPlaying({ version, url: URL.createObjectURL(blob) });
      setHeard((before) => new Set(before).add(version));
    } catch (e) {
      setError(e);
    }
  }

  const describe = (row: IntroVersion) =>
    [
      t('vi.version', { version: row.version }),
      t(row.source === 'TTS' ? 'vi.sourceTts' : 'vi.sourceUpload'),
      durationLabel(row.duration_ms),
    ]
      .filter(Boolean)
      .join(' · ');

  const player = (row: IntroVersion) => (
    <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
      <span className="card__hint">{describe(row)}</span>
      <div>
        <button className="btn" disabled={busy} onClick={() => void listen(row.version)}>
          {heard.has(row.version) || row.previewed ? t('vi.listenAgain') : t('vi.listen')}
        </button>
      </div>
      {playing?.version === row.version ? (
        // The script card above is the recording's transcript.
        <audio controls autoPlay src={playing.url} style={{ width: '100%' }} />
      ) : null}
    </div>
  );

  return (
    <>
      {error ? <ErrorState error={error} /> : null}
      <Card title={t('vi.scriptTitle')} hint={t('vi.scriptHint')}>
        <p lang="bn">{status.script}</p>
      </Card>

      <Card title={t('vi.statusTitle')}>
        {status.live ? (
          <>
            <p>{offer.onButIdle ? t('vi.statusOnIdle') : t('vi.statusOn')}</p>
            {player(status.live)}
            <div style={{ marginTop: 12 }}>
              <button
                className="btn"
                disabled={busy}
                onClick={() => void run(() => api.post('/voice/intro/turn-off'))}
              >
                {t('vi.turnOff')}
              </button>
            </div>
          </>
        ) : offer.candidate ? null : (
          <>
            <p>{t('vi.statusNone')}</p>
            {offer.waitingForTeam ? <p className="card__hint">{t('vi.waitingForTeam')}</p> : null}
            {offer.canRequest ? (
              requested ? (
                <p className="card__hint">{t('vi.requested')}</p>
              ) : (
                <button
                  className="btn btn--primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api.post('/voice/intro/request');
                      setRequested(true);
                    })
                  }
                >
                  {t('vi.request')}
                </button>
              )
            ) : null}
          </>
        )}

        {offer.candidate ? (
          <div style={{ marginTop: status.live ? 18 : 0 }}>
            <p>{t('vi.newReady')}</p>
            {player(offer.candidate)}
            <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
              <div>
                <button
                  className="btn btn--primary"
                  disabled={busy || !offer.canTurnOn}
                  onClick={() =>
                    void run(() => api.post(`/voice/intro/${offer.candidate?.version}/turn-on`))
                  }
                >
                  {t('vi.turnOn')}
                </button>
              </div>
              {!offer.canTurnOn ? <span className="card__hint">{t('vi.turnOnHint')}</span> : null}
            </div>
          </div>
        ) : null}
      </Card>
    </>
  );
}
