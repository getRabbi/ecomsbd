'use client';

import { use, useEffect, useState } from 'react';

import { ApiError, publicApi } from '@/lib/api';
import {
  closedMessage,
  confirmText,
  formatCod,
  isToken,
  itemLine,
  type ConfirmKey,
  type ConfirmLocale,
  type LinkView,
} from '@/lib/confirm-link';

/**
 * Where the customer's SMS link lands (V4 WP2.3). No sign-in.
 *
 * Opening the page only reads (`GET` changes nothing, so an SMS app that
 * previews the link answers nothing); one of the two buttons sends the answer,
 * once. An unknown, expired or used link shows the same short message.
 */
export default function ConfirmLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [locale, setLocale] = useState<ConfirmLocale>('bn');
  const [fetched, setView] = useState<LinkView | null>(null);
  const [problem, setProblem] = useState<ConfirmKey | null>(null);
  const [busy, setBusy] = useState(false);
  const t = (key: ConfirmKey, values?: Record<string, string | number>) =>
    confirmText(locale, key, values);
  const path = `/public/confirmations/${encodeURIComponent(token)}`;
  const view: LinkView | null = isToken(token) ? fetched : UNAVAILABLE;

  useEffect(() => {
    if (!isToken(token)) return; // shown as unavailable below; nothing to ask
    const controller = new AbortController();
    publicApi
      .get<LinkView>(path, controller.signal)
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setProblem(failure(error));
      });
    return () => controller.abort();
  }, [path, token]);

  async function answer(result: 'confirm' | 'cancel') {
    setBusy(true);
    setProblem(null);
    try {
      setView(await publicApi.post<LinkView>(path, { result }));
    } catch (error) {
      setProblem(failure(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main lang={locale} style={{ maxWidth: 440, margin: '0 auto', padding: '24px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => setLocale(locale === 'bn' ? 'en' : 'bn')}
        >
          {t('cl.language')}
        </button>
      </div>
      <h1 style={{ fontSize: 22, margin: '8px 0 16px' }}>{t('cl.title')}</h1>
      {view === null && problem === null ? <p>{t('cl.loading')}</p> : null}
      {view !== null && view.status !== 'OPEN' ? (
        <div className={`notice ${view.status === 'ANSWERED' ? 'notice--good' : 'notice--warn'}`}>
          {view.shop_name ? <p>{t('cl.from', { shop: view.shop_name })}</p> : null}
          <p>{t(closedMessage(view))}</p>
        </div>
      ) : null}
      {view !== null && view.status === 'OPEN' ? (
        <section className="card" style={{ padding: 16 }}>
          {view.shop_name ? (
            <p style={{ fontWeight: 600 }}>{t('cl.from', { shop: view.shop_name })}</p>
          ) : null}
          <p style={{ margin: '12px 0 4px' }}>{t('cl.items')}</p>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {view.items.map((item, index) => (
              <li key={index} style={{ overflowWrap: 'anywhere' }}>
                {itemLine(item)}
              </li>
            ))}
          </ul>
          {view.item_count > view.items.length ? (
            <p>{t('cl.more', { count: view.item_count - view.items.length })}</p>
          ) : null}
          {view.cod_amount_paisa !== null ? (
            <p style={{ fontSize: 20, fontWeight: 700, margin: '16px 0 4px' }}>
              {t('cl.cod')}: {formatCod(view.cod_amount_paisa)}
            </p>
          ) : null}
          {view.phone_masked ? (
            <p>
              {t('cl.phone')}: {view.phone_masked}
            </p>
          ) : null}
          <p style={{ margin: '16px 0 8px' }}>{t('cl.question')}</p>
          <div style={{ display: 'grid', gap: 8 }}>
            <button
              type="button"
              className="btn btn--primary btn--block"
              disabled={busy}
              onClick={() => void answer('confirm')}
            >
              {busy ? t('cl.sending') : t('cl.confirm')}
            </button>
            <button
              type="button"
              className="btn btn--block"
              disabled={busy}
              onClick={() => void answer('cancel')}
            >
              {t('cl.cancel')}
            </button>
          </div>
        </section>
      ) : null}
      {problem !== null ? (
        <div className="notice notice--warn" role="alert">
          <p>{t(problem)}</p>
        </div>
      ) : null}
    </main>
  );
}

const UNAVAILABLE: LinkView = {
  status: 'UNAVAILABLE',
  result: null,
  shop_name: null,
  items: [],
  item_count: 0,
  cod_amount_paisa: null,
  phone_masked: null,
  expires_at: null,
};

function failure(error: unknown): ConfirmKey {
  return error instanceof ApiError && error.status === 429 ? 'cl.busy' : 'cl.error';
}
