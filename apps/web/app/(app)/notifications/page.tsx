'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, Chip, EmptyState, ErrorState, LoadingRows, type Tone } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate, formatPaisa } from '@/lib/money';
import {
  type AppNotification,
  hrefFor,
  NOTIFICATION_CATEGORIES,
  NOTIFICATIONS_CHANGED,
  type NotificationPreferences,
} from '@/lib/notifications';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

function toneFor(notification: AppNotification): Tone {
  if (notification.state === 'RESOLVED') return 'good';
  if (notification.severity === 'CRITICAL') return 'bad';
  if (notification.severity === 'WARNING') return 'warn';
  return 'neutral';
}

/**
 * The notification centre.
 *
 * Deliberately small: a list, a read state, a category filter and the member's
 * own category switches. What each member sees — and in which language — is
 * decided by the API, so a role that cannot see Money never receives a payout
 * alert here either.
 */
export default function NotificationsPage() {
  const router = useRouter();
  const { t, locale } = useSession();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [category, setCategory] = useState<string>('');
  const [older, setOlder] = useState<{
    key: string;
    items: AppNotification[];
    cursor: string | null;
  }>({ key: '', items: [], cursor: null });
  const [busy, setBusy] = useState(false);

  const query = { limit: 30, unread_only: unreadOnly, category: category || null, lang: locale };
  const queryKey = JSON.stringify(query);
  const list = useApi<Page<AppNotification>>('/notifications', query);
  const prefs = useApi<NotificationPreferences>('/account/notification-preferences');

  // Pages fetched with "load more" belong to one filter; changing it drops them.
  const extra = older.key === queryKey ? older : { key: queryKey, items: [], cursor: null };
  const items = [...(list.data?.items ?? []), ...extra.items];
  const nextCursor = extra.items.length ? extra.cursor : (list.data?.next_cursor ?? null);

  const changed = useCallback(() => {
    list.reload();
    setOlder({ key: '', items: [], cursor: null });
    window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
  }, [list]);

  async function loadMore() {
    if (!nextCursor) return;
    setBusy(true);
    try {
      const page = await api.get<Page<AppNotification>>('/notifications', {
        ...query,
        cursor: nextCursor,
      });
      setOlder({ key: queryKey, items: [...extra.items, ...page.items], cursor: page.next_cursor });
    } finally {
      setBusy(false);
    }
  }

  async function markRead(notification: AppNotification) {
    if (notification.read_at) return;
    await api.post(`/notifications/${notification.id}/read`, undefined, { lang: locale });
  }

  async function open(notification: AppNotification) {
    await markRead(notification);
    changed();
    const href = hrefFor(notification);
    if (href) router.push(href);
  }

  async function markAll() {
    setBusy(true);
    try {
      await api.post('/notifications/read-all');
      changed();
    } finally {
      setBusy(false);
    }
  }

  async function toggleCategory(value: string, on: boolean) {
    const muted = new Set(prefs.data?.muted_categories ?? []);
    if (on) muted.delete(value);
    else muted.add(value);
    setBusy(true);
    try {
      await api.patch('/account/notification-preferences', { muted_categories: [...muted] });
      prefs.reload();
      changed();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title={t('notif.title')}
        subtitle={t('notif.subtitle')}
        actions={
          <button type="button" className="btn btn--sm" disabled={busy} onClick={markAll}>
            {t('notif.markAll')}
          </button>
        }
      />

      <div className="content">
        <div className="toolbar" role="tablist" style={{ padding: 0, marginBottom: 12 }}>
          {[false, true].map((value) => (
            <button
              key={String(value)}
              type="button"
              role="tab"
              aria-selected={unreadOnly === value}
              className={`btn btn--sm${unreadOnly === value ? ' btn--primary' : ''}`}
              onClick={() => setUnreadOnly(value)}
            >
              {t(value ? 'notif.unread' : 'notif.all')}
            </button>
          ))}
          <label className="field" style={{ margin: 0, marginLeft: 'auto' }}>
            <span className="field__label">{t('notif.category')}</span>
            <select
              className="select"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="">{t('notif.cat.all')}</option>
              {NOTIFICATION_CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {t(`notif.cat.${value}` as StringKey)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <Card padded={false}>
          {list.loading ? (
            <LoadingRows rows={5} columns={3} />
          ) : list.error ? (
            <ErrorState error={list.error} onRetry={list.reload} />
          ) : items.length === 0 ? (
            <EmptyState title={t('notif.empty')} hint={t('notif.emptyHint')} />
          ) : (
            <div className="tablewrap">
              <table className="table">
                <tbody>
                  {items.map((notification) => (
                    <tr
                      key={notification.id}
                      className="table__row--clickable"
                      onClick={() => void open(notification)}
                    >
                      <td style={{ width: 150 }}>
                        <Chip
                          label={
                            notification.state === 'RESOLVED'
                              ? t('notif.resolved')
                              : t(notification.read_at ? 'notif.read' : 'notif.new')
                          }
                          tone={toneFor(notification)}
                        />
                        <div className="table__sub" style={{ marginTop: 6 }}>
                          {t(`notif.cat.${notification.category}` as StringKey)}
                        </div>
                      </td>
                      <td>
                        <div
                          className="table__primary"
                          style={{ fontWeight: notification.read_at ? 500 : 700 }}
                        >
                          {notification.title}
                        </div>
                        <div className="table__sub">{notification.body}</div>
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {notification.amount_paisa ? (
                          <div className="table__primary">
                            {formatPaisa(notification.amount_paisa, { locale })}
                          </div>
                        ) : null}
                        <div className="table__sub">
                          {formatDate(notification.created_at, { locale, withTime: true })}
                        </div>
                        {hrefFor(notification) ? (
                          <span className="table__sub">{t('notif.open')} →</span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {nextCursor ? (
            <div className="pagination">
              <button type="button" className="btn btn--sm" disabled={busy} onClick={loadMore}>
                {t('notif.loadMore')}
              </button>
            </div>
          ) : null}
        </Card>

        {prefs.data && prefs.data.categories.length ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('notif.prefsTitle')} hint={t('notif.prefsHint')}>
              {prefs.data.categories.map((value) => {
                const on = !prefs.data?.muted_categories.includes(value);
                return (
                  <label key={value} className="row" style={{ cursor: busy ? 'wait' : 'pointer' }}>
                    <span className="row__label">{t(`notif.cat.${value}` as StringKey)}</span>
                    <span className="row__value">
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={busy}
                        onChange={(event) => void toggleCategory(value, event.target.checked)}
                      />{' '}
                      {t(on ? 'notif.on' : 'notif.off')}
                    </span>
                  </label>
                );
              })}
            </Card>
          </div>
        ) : null}
      </div>
    </>
  );
}
