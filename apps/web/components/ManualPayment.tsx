'use client';

import { useState, type ReactNode } from 'react';

import { Card, Chip, ErrorState, type Tone } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import {
  MONTH_CHOICES,
  amountPaisa,
  formatTaka,
  invalidFields,
  methodKey,
  refusalKey,
  stateKey,
  submitBody,
  type ManualPaymentOptions,
  type ManualPaymentSubmission,
  type PaymentField,
  type PaymentForm,
  type SubmissionState,
} from '@/lib/manual-payment';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

const STATE_TONE: Record<SubmissionState, Tone> = {
  SUBMITTED: 'warn',
  APPROVED: 'good',
  REJECTED: 'bad',
};

/**
 * Plans → "Pay by bKash or Nagad" (V4 WP2.5).
 *
 * Renders nothing unless `GET /v1/billing/manual-payments/options` says the
 * form is available: the server owns the flag, Free Launch Mode, the build's
 * channel and the receiving numbers. Only the owner (`billing.manage`) asks.
 * `fallback` shows once the answer is "not available" (or the reader is not
 * the owner), never while loading.
 */
export function ManualPayment({ fallback = null }: { fallback?: ReactNode }) {
  const { t } = useSession();
  const shop = useShop();
  const canPay = shop.can('billing.manage');
  const options = useApi<ManualPaymentOptions>(canPay ? '/billing/manual-payments/options' : null);
  const history = useApi<ManualPaymentSubmission[]>(
    options.data?.available ? '/billing/manual-payments' : null,
  );

  if (!canPay || options.error || (options.data && !options.data.available)) {
    return <>{fallback}</>;
  }
  if (!options.data) {
    return null;
  }
  return (
    <>
      <PaymentFormCard options={options.data} onSubmitted={history.reload} />
      {history.data && history.data.length > 0 ? (
        <Card title={t('mp.history')}>
          {history.data.map((row) => (
            <div key={row.id} style={{ padding: '10px 0', borderBottom: '1px solid var(--stroke)' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <strong>{formatTaka(row.amount.amount_paisa)}</strong>
                <span>
                  {t(methodKey(row.method))} · {row.trx_id}
                </span>
                <Chip label={t(stateKey(row.state))} tone={STATE_TONE[row.state]} />
              </div>
              {row.state === 'APPROVED' && row.period_end ? (
                <p className="card__hint">{t('mp.until', { date: formatDate(row.period_end) })}</p>
              ) : null}
              {row.state === 'REJECTED' && row.reason ? (
                <p className="card__hint">{t('mp.why', { reason: row.reason })}</p>
              ) : null}
            </div>
          ))}
        </Card>
      ) : null}
    </>
  );
}

function PaymentFormCard({
  options,
  onSubmitted,
}: {
  options: ManualPaymentOptions;
  onSubmitted: () => void;
}) {
  const { t } = useSession();
  const [form, setForm] = useState<PaymentForm>({
    plan: options.plans[0]?.code ?? '',
    months: 1,
    method: options.accounts[0]?.method ?? '',
    payer: '',
    trx: '',
  });
  const [invalid, setInvalid] = useState<PaymentField[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [sent, setSent] = useState(false);

  const plan = options.plans.find((candidate) => candidate.code === form.plan);
  const accounts = options.accounts.filter((account) => account.method === form.method);
  const methods = [...new Set(options.accounts.map((account) => account.method))];
  const refusal = error instanceof ApiError ? refusalKey(error.details) : null;

  async function submit() {
    setError(null);
    setSent(false);
    const bad = invalidFields(form, options);
    setInvalid(bad);
    if (bad.length) {
      return;
    }
    setBusy(true);
    try {
      await api.post('/billing/manual-payments', submitBody(form));
      setForm({ ...form, payer: '', trx: '' });
      setSent(true);
      onSubmitted();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const label = (text: string, field: PaymentField, control: ReactNode) => (
    <label style={{ display: 'block', marginTop: 10 }} aria-invalid={invalid.includes(field)}>
      <span className="card__hint">{text}</span>
      {control}
    </label>
  );

  return (
    <Card title={t('mp.title')} hint={t('mp.intro')}>
      {label(
        t('mp.plan'),
        'plan',
        <select
          className="input"
          value={form.plan}
          onChange={(e) => setForm({ ...form, plan: e.target.value })}
        >
          {options.plans.map((candidate) => (
            <option key={candidate.code} value={candidate.code}>
              {candidate.name} — {formatTaka(candidate.monthly_price.amount_paisa)}
            </option>
          ))}
        </select>,
      )}
      {label(
        t('mp.months'),
        'months',
        <select
          className="input"
          value={form.months}
          onChange={(e) => setForm({ ...form, months: Number(e.target.value) })}
        >
          {MONTH_CHOICES.filter((m) => m >= options.min_months && m <= options.max_months).map((m) => (
            <option key={m} value={m}>
              {t('mp.monthsValue', { count: m })}
            </option>
          ))}
        </select>,
      )}
      {label(
        t('mp.method'),
        'method',
        <select
          className="input"
          value={form.method}
          onChange={(e) => setForm({ ...form, method: e.target.value as PaymentForm['method'] })}
        >
          {methods.map((method) => (
            <option key={method} value={method}>
              {t(methodKey(method))}
            </option>
          ))}
        </select>,
      )}
      <div style={{ marginTop: 14 }}>
        <p className="card__hint">{t('mp.amount')}</p>
        <p style={{ fontSize: 22, fontWeight: 700 }}>{formatTaka(amountPaisa(plan, form.months))}</p>
        <p className="card__hint">{t('mp.sendTo')}</p>
        {accounts.map((account) => (
          <p key={account.number}>
            <strong>{account.number}</strong>
            {account.label ? ` · ${account.label}` : ''}
          </p>
        ))}
      </div>
      {label(
        t('mp.payer'),
        'payer',
        <input
          className="input"
          inputMode="tel"
          autoComplete="tel"
          value={form.payer}
          onChange={(e) => setForm({ ...form, payer: e.target.value })}
        />,
      )}
      {label(
        t('mp.trx'),
        'trx',
        <input
          className="input"
          autoCapitalize="characters"
          spellCheck={false}
          value={form.trx}
          onChange={(e) => setForm({ ...form, trx: e.target.value })}
        />,
      )}
      <p className="card__hint">{t('mp.trxHint')}</p>
      {invalid.length ? <p className="card__hint">{t('mp.invalid')}</p> : null}
      {refusal ? <p className="card__hint">{t(refusal)}</p> : error ? <ErrorState error={error} /> : null}
      {sent ? <p className="card__hint">{t('mp.sent')}</p> : null}
      <div style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy} onClick={() => void submit()}>
          {busy ? t('mp.sending') : t('mp.submit')}
        </button>
      </div>
    </Card>
  );
}
