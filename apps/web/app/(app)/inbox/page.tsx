'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { OrderComposer } from '@/components/OrderComposer';
import { PasteDetect } from '@/components/PasteDetect';
import { PageHeader } from '@/components/shell';
import { Card, Chip, ErrorState } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate, formatPaisa } from '@/lib/money';
import { errorMessage, type OrderPrefill } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

interface DraftItem {
  name?: string;
  quantity?: number;
  unit_price_paisa?: number | null;
  size?: string | null;
  color?: string | null;
}

interface ChatDraft {
  id: string;
  provider: string;
  status: string;
  sender: { display_name: string | null; phone_masked: string | null; known: boolean };
  customer: { id: string; name: string | null; phone_masked: string } | null;
  customer_name: string | null;
  phones: string[];
  selected_phone: string | null;
  address: string | null;
  items: DraftItem[];
  cod_amount_paisa: number | null;
  notes: string | null;
  missing_fields: string[];
  uncertain_fields: string[];
  last_message_at: string | null;
  confirmed_order_id: string | null;
}

interface DraftSummary {
  ready: number;
  needs_info: number;
  attention: number;
}

/**
 * Inbox — where orders arrive as text.
 *
 * Today that is the seller's own paste: Paste → Detect runs the same
 * deterministic parser as the phone and hands the result to the normal order
 * composer. Live capture from Messenger and WhatsApp is Coming Soon for this
 * launch, so this screen offers no connection, setup or provider control.
 * Drafts captured before the gate (development data) are listed for review
 * rather than hidden or destroyed; a review opens the same composer and
 * confirms through the chat-order endpoint, which links the draft to its order.
 */
export default function InboxPage() {
  const { t } = useSession();
  const { can } = useShop();
  const router = useRouter();
  const [prefill, setPrefill] = useState<OrderPrefill | null>(null);
  const canWrite = can('order.write');

  return (
    <>
      <PageHeader title={t('inbox.title')} subtitle={t('inbox.subtitle')} />
      <div className="content">
        <div className="notice">
          <span>
            <strong>{t('inbox.liveSoon')}</strong> <Chip label={t('int.comingSoon')} tone="neutral" />
            <br />
            <span className="card__hint">{t('inbox.liveSoonHint')}</span>
          </span>
        </div>

        <Card title={t('act.pasteOrder')} hint={t('inbox.pasteHint')}>
          {canWrite ? <PasteDetect onReview={setPrefill} /> : <p className="card__hint">{t('err.forbidden')}</p>}
        </Card>

        {canWrite ? <Drafts onReview={setPrefill} /> : null}
      </div>

      {prefill ? (
        <OrderComposer
          prefill={prefill}
          onClose={() => setPrefill(null)}
          onCreated={(id) => {
            setPrefill(null);
            router.push(`/orders?order=${encodeURIComponent(id)}`);
          }}
        />
      ) : null}
    </>
  );
}

function Drafts({ onReview }: { onReview: (prefill: OrderPrefill) => void }) {
  const { t, locale } = useSession();
  const summary = useApi<DraftSummary>('/chat-orders/summary');
  const hasDrafts = (summary.data?.ready ?? 0) + (summary.data?.needs_info ?? 0) > 0;
  const drafts = useApi<{ items: ChatDraft[] }>(hasDrafts ? '/chat-orders' : null, { status: 'review', limit: 50 });
  const [failure, setFailure] = useState<string | null>(null);

  if (summary.error || !hasDrafts) {
    // Nothing to review, or the draft service is not reachable — either way the
    // launch-gated channel has nothing to say on this screen.
    return null;
  }

  async function ignore(id: string) {
    setFailure(null);
    try {
      await api.post(`/chat-orders/${id}/ignore`);
      drafts.reload();
      summary.reload();
    } catch (caught) {
      setFailure(errorMessage(caught));
    }
  }

  return (
    <div style={{ marginTop: 18 }}>
      <Card title={t('inbox.drafts')} hint={t('inbox.draftsHint')} padded={false}>
        {drafts.error ? <ErrorState error={drafts.error} onRetry={drafts.reload} /> : null}
        {failure ? <p className="formerror" style={{ padding: '0 16px' }}>{failure}</p> : null}
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('inbox.source')}</th>
                <th>{t('orders.customer')}</th>
                <th>{t('orders.items')}</th>
                <th className="num">{t('orders.cod')}</th>
                <th>{t('inbox.missing')}</th>
                <th>{t('inbox.updated')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(drafts.data?.items ?? []).map((draft) => (
                <tr key={draft.id}>
                  <td>
                    {draft.provider}
                    <div className="table__sub">{draft.status.replaceAll('_', ' ').toLowerCase()}</div>
                  </td>
                  <td>
                    {draft.customer?.name ?? draft.customer_name ?? draft.sender.display_name ?? '—'}
                    <div className="table__sub">{draft.customer?.phone_masked ?? draft.sender.phone_masked ?? ''}</div>
                  </td>
                  <td>{draft.items.map((item) => `${item.name ?? '?'} × ${item.quantity ?? 1}`).join(', ') || '—'}</td>
                  <td className="num">{draft.cod_amount_paisa !== null ? formatPaisa(draft.cod_amount_paisa, { locale }) : '—'}</td>
                  <td>{[...draft.missing_fields, ...draft.uncertain_fields].join(', ') || '—'}</td>
                  <td>{formatDate(draft.last_message_at, { locale, withTime: true })}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn--sm btn--primary"
                      onClick={() =>
                        onReview({
                          chatDraftId: draft.id,
                          phone: draft.selected_phone ?? undefined,
                          phones: draft.phones.length > 1 ? draft.phones : undefined,
                          customer_name: draft.customer_name ?? undefined,
                          address: draft.address ?? undefined,
                          items: draft.items.map((item) => ({
                            name: item.name ?? '',
                            quantity: item.quantity ?? 1,
                            unit_price_paisa: item.unit_price_paisa ?? null,
                            variant_label: [item.size, item.color].filter(Boolean).join(' / ') || null,
                          })),
                          cod_amount_paisa: draft.cod_amount_paisa,
                          note: draft.notes ?? undefined,
                        })
                      }
                    >
                      {t('inbox.review')}
                    </button>{' '}
                    <button type="button" className="btn btn--sm btn--ghost" onClick={() => void ignore(draft.id)}>
                      {t('inbox.ignore')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
