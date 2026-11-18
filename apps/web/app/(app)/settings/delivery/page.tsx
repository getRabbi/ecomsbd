'use client';

import { useEffect, useState } from 'react';

import { PageHeader } from '@/components/shell';
import { Card, ErrorState } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { LIVE_COURIERS, courierName } from '@/lib/couriers';
import {
  setCallSwitch,
  toForm,
  toPatch,
  type DeliveryRules,
  type RuleField,
  type RulesForm,
} from '@/lib/delivery-rules';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { useApi } from '@/lib/useApi';

const SECTIONS: { title: StringKey; note?: StringKey; fields: [RuleField, StringKey][] }[] = [
  {
    title: 'dr.sectionCharges',
    note: 'dr.chargesNote',
    fields: [
      ['inside_dhaka_charge_paisa', 'dr.inside'],
      ['dhaka_suburb_charge_paisa', 'dr.suburb'],
      ['outside_dhaka_charge_paisa', 'dr.outside'],
    ],
  },
  { title: 'dr.sectionCalls', fields: [['max_auto_cod_paisa', 'dr.maxAutoCod']] },
  {
    title: 'dr.sectionAuto',
    fields: [
      ['autopilot_daily_cap', 'dr.dailyCap'],
      ['quiet_start', 'dr.quietStart'],
      ['quiet_end', 'dr.quietEnd'],
    ],
  },
  {
    title: 'dr.sectionRisk',
    fields: [
      ['risk_low_bps', 'dr.riskLow'],
      ['trusted_network_bps', 'dr.trustedNetwork'],
      ['trusted_repeat_min', 'dr.trustedRepeat'],
    ],
  },
];

/**
 * Settings → Delivery rules (V4 WP0.4), against `GET/PATCH /v1/shop/delivery-rules`.
 * The API needs `settings.manage` and the shop's `delivery_rules_enabled` flag;
 * with the flag off it answers FEATURE_DISABLED and this page says so.
 */
export default function DeliveryRulesPage() {
  const { t } = useSession();
  const shop = useShop();
  const canEdit = shop.can('settings.manage');
  const rules = useApi<DeliveryRules>(canEdit ? '/shop/delivery-rules' : null);
  const disabled = rules.error instanceof ApiError && rules.error.code === 'FEATURE_DISABLED';

  return (
    <>
      <PageHeader title={t('nav.deliveryRules')} subtitle={t('dr.subtitle')} />
      <div className="content" style={{ display: 'grid', gap: 18 }}>
        {!canEdit ? (
          <Card>
            <p>{t('dr.readOnly')}</p>
          </Card>
        ) : disabled ? (
          <Card>
            <p>{t('dr.notEnabled')}</p>
          </Card>
        ) : rules.error ? (
          <ErrorState error={rules.error} onRetry={rules.reload} />
        ) : rules.data ? (
          <RulesEditor initial={rules.data} onSaved={rules.reload} />
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </div>
    </>
  );
}

function RulesEditor({ initial, onSaved }: { initial: DeliveryRules; onSaved: () => void }) {
  const { t } = useSession();
  const [form, setForm] = useState<RulesForm>(() => toForm(initial));
  const [invalid, setInvalid] = useState<RuleField[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setForm(toForm(initial)), [initial]);

  async function save() {
    setSaved(false);
    setError(null);
    const patch = toPatch(form);
    if ('invalid' in patch) {
      setInvalid(patch.invalid);
      return;
    }
    setInvalid([]);
    setBusy(true);
    try {
      await api.patch('/shop/delivery-rules', patch.body);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const input = (key: RuleField, label: StringKey) => (
    <label key={key} style={{ display: 'block', marginTop: 8 }}>
      <span className="card__hint">{t(label)}</span>
      <input
        className="input"
        inputMode={key === 'quiet_start' || key === 'quiet_end' ? 'text' : 'decimal'}
        aria-invalid={invalid.includes(key)}
        value={form.text[key]}
        onChange={(e) => setForm({ ...form, text: { ...form.text, [key]: e.target.value } })}
      />
    </label>
  );

  return (
    <>
      {!initial.configured ? (
        <Card>
          <p className="card__hint">{t('dr.defaultsNote')}</p>
        </Card>
      ) : null}
      {error ? <ErrorState error={error} /> : null}
      {invalid.length ? <p className="card__hint">{t('dr.invalid')}</p> : null}
      {saved ? <p className="card__hint">{t('dr.saved')}</p> : null}
      {SECTIONS.map((section) => (
        <Card key={section.title} title={t(section.title)} hint={section.note ? t(section.note) : undefined}>
          {section.fields.map(([key, label]) => input(key, label))}
          {section.title === 'dr.sectionCalls' ? (
            <>
              {(['call_everyone', 'call_nobody'] as const).map((which) => (
                <label key={which} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
                  <input
                    type="checkbox"
                    checked={form[which]}
                    onChange={(e) => setForm(setCallSwitch(form, which, e.target.checked))}
                  />
                  <span>{t(which === 'call_everyone' ? 'dr.callEveryone' : 'dr.callNobody')}</span>
                </label>
              ))}
            </>
          ) : null}
        </Card>
      ))}
      <Card title={t('dr.sectionCourier')}>
        <label style={{ display: 'block' }}>
          <span className="card__hint">{t('dr.defaultCourier')}</span>
          <select
            className="input"
            value={form.default_courier ?? ''}
            onChange={(e) => setForm({ ...form, default_courier: e.target.value || null })}
          >
            <option value="">{t('dr.courierNone')}</option>
            {LIVE_COURIERS.map((courier) => (
              <option key={courier} value={courier}>
                {courierName(courier)}
              </option>
            ))}
          </select>
        </label>
      </Card>
      <div>
        <button className="btn btn--primary" disabled={busy} onClick={() => void save()}>
          {t('dr.save')}
        </button>
      </div>
    </>
  );
}
