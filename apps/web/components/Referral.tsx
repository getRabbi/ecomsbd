'use client';

import { useState } from 'react';

import { Card, Chip, ErrorState, type Tone } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { formatDate } from '@/lib/money';
import {
  cleanCode,
  creditKey,
  isCode,
  refusalKey,
  stateKey,
  type ReferralOverview,
  type ReferralState,
} from '@/lib/referral';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

const STATE_TONE: Record<ReferralState, Tone> = {
  PENDING: 'warn',
  REWARDED: 'good',
  VOID: 'neutral',
};

/**
 * Plans → "Refer a shop" (V4 WP7.4).
 *
 * Renders nothing unless `GET /v1/referrals` says referrals are available: the
 * server owns the flag and the TrxID-payment gate the credit follows. Only the
 * owner (`billing.manage`) asks. Other shops appear as states and dates only.
 */
export function Referral() {
  const { t } = useSession();
  const shop = useShop();
  const canUse = shop.can('billing.manage');
  const overview = useApi<ReferralOverview>(canUse ? '/referrals' : null);
  const [busy, setBusy] = useState<'code' | 'apply' | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [entered, setEntered] = useState('');
  const [invalid, setInvalid] = useState(false);
  const [copied, setCopied] = useState(false);

  const data = overview.data;
  if (!canUse || overview.error || !data || !data.available) {
    return null;
  }
  const days = data.credit_days;
  const refusal = error instanceof ApiError ? refusalKey(error.details) : null;

  async function makeCode() {
    setError(null);
    setBusy('code');
    try {
      await api.post('/referrals/code', {});
      overview.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    setError(null);
    const ok = isCode(entered);
    setInvalid(!ok);
    if (!ok) {
      return;
    }
    setBusy('apply');
    try {
      await api.post('/referrals/apply', { code: cleanCode(entered) });
      setEntered('');
      overview.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  function copy(code: string) {
    void navigator.clipboard?.writeText(t('ref.share', { code, days })).then(() => setCopied(true));
  }

  return (
    <Card title={t('ref.title')} hint={t('ref.intro', { days })}>
      <p className="card__hint">{t('ref.yourCode')}</p>
      {data.code ? (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 22, letterSpacing: 2 }}>{data.code}</strong>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => copy(data.code ?? '')}>
            {copied ? t('ref.copied') : t('ref.copy')}
          </button>
        </div>
      ) : (
        <button className="btn btn--primary" disabled={busy !== null} onClick={() => void makeCode()}>
          {t('ref.makeCode')}
        </button>
      )}

      <p className="card__hint" style={{ marginTop: 14 }}>{t('ref.joined')}</p>
      {data.referrals.length === 0 ? <p className="card__hint">{t('ref.none')}</p> : null}
      {data.referrals.map((row) => (
        <div key={row.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--stroke)' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{t('ref.joinedOn', { date: formatDate(row.joined_at) })}</span>
            <Chip label={t(stateKey(row.state))} tone={STATE_TONE[row.state]} />
          </div>
          {row.state === 'REWARDED' ? (
            <p className="card__hint">{t(creditKey(row.credit_state), { days })}</p>
          ) : null}
        </div>
      ))}

      <div style={{ marginTop: 14 }}>
        {data.referred_by ? (
          <>
            <p>{t('ref.usedCode', { code: data.referred_by.code })}</p>
            <p className="card__hint">{t(creditKey(data.referred_by.credit_state), { days })}</p>
          </>
        ) : (
          <label style={{ display: 'block' }} aria-invalid={invalid}>
            <span className="card__hint">{t('ref.haveCode')} {t('ref.haveHint')}</span>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input
                className="input"
                autoCapitalize="characters"
                spellCheck={false}
                maxLength={16}
                value={entered}
                onChange={(e) => setEntered(e.target.value)}
              />
              <button className="btn" disabled={busy !== null} onClick={() => void apply()}>
                {busy === 'apply' ? t('ref.applying') : t('ref.apply')}
              </button>
            </div>
          </label>
        )}
        {invalid ? <p className="card__hint">{t('ref.errInvalid')}</p> : null}
        {refusal ? <p className="card__hint">{t(refusal)}</p> : error ? <ErrorState error={error} /> : null}
      </div>
    </Card>
  );
}
