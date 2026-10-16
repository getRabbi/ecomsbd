'use client';

import { useState, type FormEvent } from 'react';

import { Chip, Row } from '@/components/ui';
import { api } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import { errorMessage, type OrderPrefill, type ParseResult } from '@/lib/orders';
import { useSession } from '@/lib/session';

/**
 * Paste → Detect: the mobile Inbox's manual fallback, on the desktop.
 *
 * The text goes to `POST /orders/parse` — the same deterministic, offline
 * parser the phone uses, with no model and no network. Nothing is created
 * here: the seller sees what was found, how sure the parser is, and every
 * warning, then reviews it in the normal order composer before saving. A field
 * the parser is unsure about comes back empty rather than guessed.
 */
export function PasteDetect({ onReview }: { onReview: (prefill: OrderPrefill) => void }) {
  const { t, locale } = useSession();
  const [text, setText] = useState('');
  const [result, setResult] = useState<ParseResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function detect(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<ParseResult>('/orders/parse', { text }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function review() {
    if (!result) return;
    onReview({
      phone: result.selected_phone ?? undefined,
      phones: result.phones.length > 1 ? result.phones : undefined,
      customer_name: result.customer_name ?? undefined,
      address: result.address ?? undefined,
      items: result.items.map((item) => ({
        name: item.name,
        quantity: item.quantity,
        unit_price_paisa: item.unit_price_paisa,
        variant_label: [item.size, item.color].filter(Boolean).join(' / ') || null,
      })),
      cod_amount_paisa: result.cod_amount_paisa,
      note: result.notes ?? undefined,
      source_text: result.source_text || text,
      channel: 'PASTE_PARSE',
    });
  }

  const confidence = (field: string) => {
    const value = result?.confidence[field];
    if (value === undefined) return null;
    const percent = Math.round(value * 100);
    return <Chip label={`${percent}%`} tone={percent >= 80 ? 'good' : percent >= 50 ? 'warn' : 'bad'} />;
  };

  return (
    <div className="grid2">
      <form onSubmit={detect}>
        <label className="field">
          <span className="field__label">{t('pd.paste')}</span>
          <textarea
            className="textarea"
            rows={12}
            maxLength={8000}
            value={text}
            placeholder={t('pd.placeholder')}
            onChange={(event) => setText(event.target.value)}
          />
          <span className="field__hint">{t('pd.hint')}</span>
        </label>
        {error ? <p className="formerror">{error}</p> : null}
        <div className="toolbar" style={{ padding: 0 }}>
          <button type="submit" className="btn btn--primary" disabled={busy || !text.trim()}>
            {busy ? t('pd.detecting') : t('pd.detect')}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => {
              setText('');
              setResult(null);
              setError(null);
            }}
          >
            {t('pd.clear')}
          </button>
        </div>
      </form>

      <div>
        {result ? (
          <>
            {result.is_low_confidence ? (
              <div className="notice notice--warn">{t('pd.lowConfidence')}</div>
            ) : null}
            {result.warnings.length > 0 ? (
              <ul className="card__hint" style={{ margin: '0 0 10px', paddingLeft: 18 }}>
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
            <Row label={<>{t('oc.name')} {confidence('name')}</>} value={result.customer_name ?? '—'} />
            <Row
              label={<>{t('oc.phone')} {confidence('phone')}</>}
              value={
                result.phones.length === 0
                  ? '—'
                  : result.needs_phone_selection
                    ? `${result.phones.join(', ')} — ${t('pd.choosePhone')}`
                    : (result.selected_phone ?? result.phones[0])
              }
            />
            <Row label={<>{t('oc.address')} {confidence('address')}</>} value={result.address ?? '—'} />
            <Row
              label={<>{t('orders.items')} {confidence('items')}</>}
              value={
                result.items.length === 0
                  ? '—'
                  : result.items
                      .map((item) =>
                        [
                          `${item.name} × ${item.quantity}`,
                          [item.size, item.color].filter(Boolean).join(' / '),
                          item.unit_price_paisa !== null ? formatPaisa(item.unit_price_paisa, { locale }) : '',
                        ]
                          .filter(Boolean)
                          .join(' · '),
                      )
                      .join('; ')
              }
            />
            <Row
              label={<>{t('orders.cod')} {confidence('amount')}</>}
              value={result.cod_amount_paisa !== null ? formatPaisa(result.cod_amount_paisa, { locale }) : '—'}
            />
            {result.notes ? <Row label={t('oc.note')} value={result.notes} /> : null}
            <p className="card__hint" style={{ marginTop: 10 }}>
              {t('pd.reviewNote')}
            </p>
            <button type="button" className="btn btn--primary" onClick={review}>
              {t('pd.review')}
            </button>
          </>
        ) : (
          <p className="card__hint">{t('pd.empty')}</p>
        )}
      </div>
    </div>
  );
}
