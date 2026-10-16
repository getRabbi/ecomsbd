'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';

import { Card, Chip, ErrorState, Row } from '@/components/ui';
import { api, download } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate } from '@/lib/money';
import { errorMessage } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

interface SessionRow {
  id: string;
  device_id: string | null;
  created_at: string;
  last_seen_at: string;
  is_current: boolean;
}

interface Privacy {
  retention_note: string;
  grace_days: number;
  deletion_requested: boolean;
  scheduled_for: string | null;
  anonymised_on_deletion: string[];
  retained_after_deletion: string[];
}

interface ExportJob {
  id: string;
  kind: string;
  status: string;
  row_count: number;
  filename: string | null;
  created_at: string;
  expires_at: string | null;
  downloaded_at: string | null;
  download_token?: string | null;
}

const EXPORT_KINDS = ['ORDERS', 'CUSTOMERS', 'PRODUCTS', 'PAYOUTS', 'RECONCILIATION', 'PROFIT_SUMMARY'] as const;

/** Account identity, sign-in security and active sessions. */
export function AccountSecurity() {
  const { t, locale, session, signOut } = useSession();
  const { me } = useShop();
  const sessions = useApi<SessionRow[]>('/account/sessions');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function logoutOthers() {
    setBusy(true);
    setNotice(null);
    try {
      await api.post('/account/logout-others');
      setNotice(t('acct.othersSignedOut'));
      sessions.reload();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t('acct.title')} hint={t('acct.hint')}>
      <Row label={t('acct.email')} value={session?.user.email ?? '—'} />
      <Row label={t('oc.name')} value={me?.display_name ?? '—'} />
      <Row label={t('oc.phone')} value={me?.masked_phone ?? '—'} />
      <Row label={t('acct.signInWith')} value={(session?.user.app_metadata?.providers as string[] | undefined)?.join(', ') ?? session?.user.app_metadata?.provider ?? '—'} />
      <div className="toolbar" style={{ padding: '10px 0', flexWrap: 'wrap' }}>
        <Link className="btn btn--sm" href="/reset-password">{t('acct.changePassword')}</Link>
        <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void logoutOthers()}>{t('acct.signOutOthers')}</button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void signOut()}>{t('auth.signOut')}</button>
      </div>
      {notice ? <p className="card__hint">{notice}</p> : null}
      <h3 className="card__title" style={{ marginTop: 8 }}>{t('acct.sessions')}</h3>
      {sessions.error ? <ErrorState error={sessions.error} onRetry={sessions.reload} /> : null}
      {(sessions.data ?? []).map((row) => (
        <Row
          key={row.id}
          label={`${formatDate(row.created_at, { locale })}${row.device_id ? ` · ${t('acct.app')}` : ` · ${t('acct.browser')}`}`}
          value={
            <>
              {t('acct.lastSeen')} {formatDate(row.last_seen_at, { locale, withTime: true })} {row.is_current ? <Chip label={t('acct.thisSession')} tone="good" /> : null}
            </>
          }
        />
      ))}
    </Card>
  );
}

/**
 * Data & privacy: exports, the retention note, and shop deletion with its
 * cooling-off period. Deletion is owner-only and re-checked by the API against
 * the membership table; the typed confirmation is a deliberate step, not a
 * checkbox.
 */
export function PrivacyData() {
  const { t, locale } = useSession();
  const { can, current } = useShop();
  const privacy = useApi<Privacy>('/account/privacy');
  const exports = useApi<ExportJob[]>('/exports');
  const [kind, setKind] = useState<string>('ORDERS');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [reason, setReason] = useState('');
  const bn = locale === 'bn';

  async function exportNow(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const job = await api.post<ExportJob>('/exports', { kind, since: since || undefined, until: until || undefined });
      // The token is one-time and only ever held in this call's memory.
      if (job.download_token) {
        await download(`/exports/${job.id}/download?token=${encodeURIComponent(job.download_token)}`, job.filename ?? `${kind.toLowerCase()}.csv`);
      }
      setNotice(t('acct.exportReady', { count: job.row_count }));
      exports.reload();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function requestDeletion(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      await api.post('/account/delete', { confirm: confirmText, reason: reason.trim() || undefined });
      setDeleting(false);
      privacy.reload();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function cancelDeletion() {
    setBusy(true);
    setNotice(null);
    try {
      await api.post('/account/delete/cancel');
      privacy.reload();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const shopName = current?.name ?? '';

  return (
    <Card title={t('acct.privacy')} hint={t('acct.privacyHint')}>
      <form onSubmit={exportNow}>
        <div className="formgrid">
          <label className="field">
            <span className="field__label">{t('acct.exportKind')}</span>
            <select className="select" value={kind} onChange={(event) => setKind(event.target.value)}>
              {EXPORT_KINDS.map((value) => (
                <option key={value} value={value}>{t(`acct.export.${value}` as StringKey)}</option>
              ))}
            </select>
          </label>
          <span />
          <label className="field">
            <span className="field__label">{t('ins.from')}</span>
            <input className="input" type="date" value={since} onChange={(event) => setSince(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('ins.to')}</span>
            <input className="input" type="date" value={until} onChange={(event) => setUntil(event.target.value)} />
          </label>
        </div>
        <button type="submit" className="btn btn--sm" disabled={busy}>{t('acct.exportButton')}</button>
      </form>
      {(exports.data ?? []).slice(0, 5).map((job) => (
        <Row key={job.id} label={`${t(`acct.export.${job.kind}` as StringKey)} · ${formatDate(job.created_at, { locale, withTime: true })}`} value={`${job.row_count} · ${job.status.toLowerCase()}`} />
      ))}
      {notice ? <p className="card__hint" role="status">{notice}</p> : null}

      <div className="formsection">
        <div className="formsection__title">{t('acct.deletion')}</div>
        {privacy.data ? (
          <>
            <p className="card__hint">{privacy.data.retention_note}</p>
            {privacy.data.deletion_requested ? (
              <div className="notice notice--warn" style={{ display: 'block' }}>
                {t('acct.deletionScheduled', { date: formatDate(privacy.data.scheduled_for, { locale }) })}
                {can('settings.manage') ? (
                  <div style={{ marginTop: 8 }}>
                    <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void cancelDeletion()}>{t('acct.cancelDeletion')}</button>
                  </div>
                ) : null}
              </div>
            ) : can('settings.manage') ? (
              deleting ? (
                <form onSubmit={requestDeletion}>
                  <p className="card__hint">{t('acct.deletionWarning', { days: privacy.data.grace_days })}</p>
                  <label className="field">
                    <span className="field__label">{t('acct.typeShopName', { name: shopName })}</span>
                    <input className="input" required value={confirmText} onChange={(event) => setConfirmText(event.target.value)} />
                  </label>
                  <label className="field">
                    <span className="field__label">{t('ml.reason')}</span>
                    <input className="input" maxLength={400} value={reason} onChange={(event) => setReason(event.target.value)} />
                  </label>
                  <button type="submit" className="btn btn--sm" disabled={busy || confirmText.trim() !== shopName.trim() || !shopName}>{t('acct.confirmDeletion')}</button>{' '}
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeleting(false)}>{t('common.cancel')}</button>
                </form>
              ) : (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeleting(true)}>{t('acct.deleteShop')}</button>
              )
            ) : (
              <p className="card__hint">{t('set.readOnly')}</p>
            )}
          </>
        ) : null}
        <p className="card__hint" style={{ marginTop: 8 }}>
          <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{t('legal.privacy')}</Link> · <Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{t('legal.deletion')}</Link> · <Link href="/ecomsbd-support">{t('acct.support')}</Link>
        </p>
      </div>
    </Card>
  );
}
