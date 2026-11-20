'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';

import { OrderComposer } from '@/components/OrderComposer';
import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { draftIdFrom, draftPrefill, isOpenDraft, type QuickOrderDraft } from '@/lib/extension';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

/**
 * Quick order (V4 Phase 5): where the browser extension sends the seller
 * after a capture, as `/quick-order?draft=<id>`.
 *
 * The extension's key may only capture. This page reads the draft with the
 * seller's own session (`GET /v1/chat-orders/{id}`, 404 in another shop) and
 * opens the same order composer the Inbox review uses, so the order is made
 * through the chat-order endpoint and linked to its draft.
 */
export default function QuickOrderPage() {
  return (
    <Suspense>
      <QuickOrder />
    </Suspense>
  );
}

function QuickOrder() {
  const { t } = useSession();
  const { can } = useShop();
  const router = useRouter();
  const draftId = draftIdFrom(useSearchParams().get('draft'));
  const canWrite = can('order.write');
  const draft = useApi<QuickOrderDraft>(draftId && canWrite ? `/chat-orders/${draftId}` : null);
  const [composing, setComposing] = useState(true);
  const notFound = draft.error instanceof ApiError && draft.error.status === 404;

  return (
    <>
      <PageHeader title={t('qo.title')} subtitle={t('qo.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        {!canWrite ? (
          <Card>
            <p>{t('err.forbidden')}</p>
          </Card>
        ) : !draftId ? (
          <Card>
            <p>{t('qo.badLink')}</p>
          </Card>
        ) : notFound ? (
          <Card>
            <p>{t('qo.notFound')}</p>
          </Card>
        ) : draft.error ? (
          <ErrorState error={draft.error} onRetry={draft.reload} />
        ) : !draft.data ? (
          <p className="card__hint">{t('common.loading')}</p>
        ) : !isOpenDraft(draft.data) ? (
          <Card>
            <p>{t('qo.closed')}</p>
            {draft.data.confirmed_order_id ? (
              <Link className="btn btn--primary" href={`/orders?order=${encodeURIComponent(draft.data.confirmed_order_id)}`}>
                {t('qo.openOrder')}
              </Link>
            ) : null}
          </Card>
        ) : (
          <Card>
            {draft.data.missing_fields.length > 0 ? (
              <p className="card__hint">{t('qo.missing', { fields: draft.data.missing_fields.join(', ') })}</p>
            ) : null}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="btn btn--primary" onClick={() => setComposing(true)}>
                {t('qo.review')}
              </button>
              <Link className="btn btn--ghost" href="/inbox">
                {t('qo.toInbox')}
              </Link>
            </div>
          </Card>
        )}
      </div>

      {composing && draft.data && isOpenDraft(draft.data) ? (
        <OrderComposer
          prefill={draftPrefill(draft.data)}
          onClose={() => setComposing(false)}
          onCreated={(id) => {
            setComposing(false);
            router.push(`/orders?order=${encodeURIComponent(id)}`);
          }}
        />
      ) : null}
    </>
  );
}
