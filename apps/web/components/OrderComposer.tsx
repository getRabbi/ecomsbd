'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

import { Chip, Drawer, Row } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import {
  errorMessage,
  newClientId,
  paisaToTaka,
  takaToPaisa,
  type DuplicateCheck,
  type OrderPrefill,
  type RiskCheck,
} from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import { describeRisk } from '@/lib/states';
import { useApi, useDebounced } from '@/lib/useApi';

interface ProductHit {
  id: string;
  name: string;
  sku: string | null;
  default_selling_price_paisa: number;
  stock_tracking_enabled: boolean;
  stock_on_hand: number;
  is_active: boolean;
  has_variants: boolean;
  variants: {
    id: string;
    name: string;
    sku: string | null;
    price_paisa: number | null;
    stock_on_hand: number;
    is_active: boolean;
  }[];
}

interface CustomerContext {
  id: string;
  name: string | null;
  phone_masked: string;
  flag: string;
  order_count: number;
  delivered_count: number;
  returned_count: number;
  success_rate_basis_points: number | null;
  last_order_at: string | null;
  addresses: { id: string; raw_address: string; district: string | null; area: string | null; is_default: boolean }[];
}

interface Line {
  key: string;
  productId: string | null;
  productName: string;
  hasVariants: boolean;
  variants: ProductHit['variants'];
  variantId: string;
  variantLabel: string;
  stock: number | null;
  quantity: string;
  price: string;
}

function blankLine(): Line {
  return {
    key: newClientId(),
    productId: null,
    productName: '',
    hasVariants: false,
    variants: [],
    variantId: '',
    variantLabel: '',
    stock: null,
    quantity: '1',
    price: '',
  };
}

/**
 * The order composer — new order, paste review and chat-draft review all end
 * here, so there is one form and one set of rules.
 *
 * It sends what the seller typed and lets the API decide: the order is created
 * in `DRAFT`, totals and COD defaults are computed server-side, and the
 * duplicate warning comes from `/orders/check-duplicates`, the same advisory
 * check the phone uses. A warning never blocks saving — a customer ordering
 * twice in a day is a real workflow, and the seller is the one who knows.
 */
export function OrderComposer({
  prefill,
  onClose,
  onCreated,
}: {
  prefill?: OrderPrefill;
  onClose: () => void;
  onCreated: (orderId: string) => void;
}) {
  const { t, locale } = useSession();
  const { can } = useShop();

  const [phone, setPhone] = useState(prefill?.phone ?? prefill?.phones?.[0] ?? '');
  const [name, setName] = useState(prefill?.customer_name ?? '');
  const [address, setAddress] = useState(prefill?.address ?? '');
  const [district, setDistrict] = useState('');
  const [area, setArea] = useState('');
  const [lines, setLines] = useState<Line[]>(() =>
    prefill?.items?.length
      ? prefill.items.map((item) => ({
          ...blankLine(),
          productName: item.name,
          variantLabel: item.variant_label ?? '',
          quantity: String(item.quantity || 1),
          price: paisaToTaka(item.unit_price_paisa),
        }))
      : [blankLine()],
  );
  const [deliveryFee, setDeliveryFee] = useState('');
  const [discount, setDiscount] = useState('');
  const [cod, setCod] = useState(paisaToTaka(prefill?.cod_amount_paisa ?? null));
  const [note, setNote] = useState(prefill?.note ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clientId] = useState(newClientId);
  const [risk, setRisk] = useState<RiskCheck | null>(null);
  const [riskError, setRiskError] = useState<string | null>(null);

  const digits = phone.replace(/\D/g, '');
  const phoneReady = digits.length >= 10;
  const settledPhone = useDebounced(phoneReady ? phone.trim() : '', 500);

  const customer = useApi<CustomerContext | null>(
    settledPhone && can('customer.view') ? '/customers/lookup' : null,
    { phone: settledPhone },
  );

  // Fill blanks from the customer's record — never overwrite what the seller typed.
  const known = customer.data;
  useEffect(() => {
    if (!known) return;
    const timer = setTimeout(() => {
      setName((current) => current || known.name || '');
      const preferred = known.addresses.find((row) => row.is_default) ?? known.addresses[0];
      if (preferred) {
        setAddress((current) => current || preferred.raw_address);
        setDistrict((current) => current || preferred.district || '');
        setArea((current) => current || preferred.area || '');
      }
    }, 0);
    return () => clearTimeout(timer);
  }, [known]);

  const itemNames = lines.map((line) => line.productName.trim()).filter(Boolean);
  const codPaisa = takaToPaisa(cod);
  const probe = useDebounced(
    JSON.stringify({ phone: settledPhone, cod: codPaisa ?? 0, items: itemNames }),
    600,
  );
  const [duplicates, setDuplicates] = useState<DuplicateCheck | null>(null);
  useEffect(() => {
    const parsed = JSON.parse(probe) as { phone: string; cod: number; items: string[] };
    if (!parsed.phone) return;
    let cancelled = false;
    api
      .post<DuplicateCheck>('/orders/check-duplicates', {
        phone: parsed.phone,
        cod_amount_paisa: parsed.cod,
        item_names: parsed.items,
      })
      .then((result) => {
        if (!cancelled) setDuplicates(result);
      })
      .catch(() => {
        // Advisory only; a failed probe must not block the form.
      });
    return () => {
      cancelled = true;
    };
  }, [probe]);

  function update(key: string, patch: Partial<Line>) {
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...patch } : line)));
  }

  async function checkRisk() {
    setRiskError(null);
    try {
      setRisk(await api.get<RiskCheck>('/customers/risk-check', { phone: phone.trim() }));
    } catch (caught) {
      setRiskError(errorMessage(caught));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    const items = [];
    for (const line of lines) {
      if (!line.productId && !line.productName.trim()) continue;
      const quantity = Number(line.quantity);
      if (!Number.isInteger(quantity) || quantity < 1) {
        setError(t('oc.badQuantity'));
        return;
      }
      if (line.hasVariants && !line.variantId) {
        setError(t('oc.chooseVariant', { name: line.productName }));
        return;
      }
      const price = line.price.trim() ? takaToPaisa(line.price) : null;
      if (line.price.trim() && price === null) {
        setError(t('oc.badAmount'));
        return;
      }
      items.push({
        product_id: line.productId,
        name: line.productId ? undefined : line.productName.trim(),
        variant_id: line.variantId || undefined,
        variant_label: !line.productId && line.variantLabel.trim() ? line.variantLabel.trim() : undefined,
        quantity,
        unit_price_paisa: price ?? undefined,
      });
    }
    if (items.length === 0) {
      setError(t('oc.needItem'));
      return;
    }
    const fee = deliveryFee.trim() ? takaToPaisa(deliveryFee) : 0;
    const off = discount.trim() ? takaToPaisa(discount) : 0;
    if (fee === null || off === null || (cod.trim() && codPaisa === null)) {
      setError(t('oc.badAmount'));
      return;
    }

    const body = {
      phone: phone.trim(),
      items,
      customer_name: name.trim() || undefined,
      address: address.trim() || undefined,
      district: district.trim() || undefined,
      area: area.trim() || undefined,
      cod_amount_paisa: cod.trim() ? codPaisa : undefined,
      delivery_fee_paisa: fee,
      discount_paisa: off,
      note: note.trim() || undefined,
    };

    setBusy(true);
    try {
      const created = prefill?.chatDraftId
        ? await api.post<{ order: { id: string } }>(`/chat-orders/${prefill.chatDraftId}/confirm`, body)
        : await api.post<{ order: { id: string } }>('/orders', {
            ...body,
            source_text: prefill?.source_text || undefined,
            channel: prefill?.channel ?? 'MANUAL',
            client_id: clientId,
          });
      onCreated(created.order.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const riskState = risk ? describeRisk(risk.state, locale) : null;

  return (
    <Drawer title={prefill?.source_text ? t('oc.reviewTitle') : t('oc.title')} wide onClose={onClose}>
      <form onSubmit={submit}>
        {prefill?.source_text ? (
          <details className="card" style={{ padding: 10, marginBottom: 12 }}>
            <summary className="card__hint">{t('oc.original')}</summary>
            <pre style={{ whiteSpace: 'pre-wrap', margin: '8px 0 0', fontFamily: 'inherit' }}>{prefill.source_text}</pre>
          </details>
        ) : null}

        <div className="formsection__title">{t('oc.customer')}</div>
        <div className="formgrid">
          <label className="field">
            <span className="field__label">{t('oc.phone')} *</span>
            {prefill?.phones && prefill.phones.length > 1 ? (
              <select className="select" value={phone} onChange={(event) => setPhone(event.target.value)}>
                {prefill.phones.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                type="tel"
                required
                minLength={6}
                maxLength={24}
                autoFocus
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
              />
            )}
          </label>
          <label className="field">
            <span className="field__label">{t('oc.name')}</span>
            <input className="input" maxLength={160} value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="field formgrid__full">
            <span className="field__label">{t('oc.address')}</span>
            <textarea className="textarea" rows={2} maxLength={1000} value={address} onChange={(event) => setAddress(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('oc.district')}</span>
            <input className="input" maxLength={80} value={district} onChange={(event) => setDistrict(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">{t('oc.area')}</span>
            <input className="input" maxLength={120} value={area} onChange={(event) => setArea(event.target.value)} />
          </label>
        </div>

        {known ? (
          <div className="notice" style={{ display: 'block' }}>
            <strong>{t('oc.returning', { name: known.name || known.phone_masked })}</strong>{' '}
            <Link href={`/customers/${known.id}`} target="_blank" className="linkbtn">
              {t('oc.openCustomer')}
            </Link>
            <p className="card__hint" style={{ margin: '4px 0 0' }}>
              {t('oc.history', {
                orders: known.order_count,
                delivered: known.delivered_count,
                returned: known.returned_count,
              })}
              {known.flag !== 'NONE' ? ` · ${known.flag}` : ''}
            </p>
          </div>
        ) : null}

        {phoneReady && can('customer.risk_view') ? (
          <div style={{ marginBottom: 12 }}>
            {risk && riskState ? (
              <p className="card__hint">
                {t('orders.risk')}: <Chip label={riskState.label} tone={riskState.tone} />{' '}
                {t('oc.history', {
                  orders: risk.order_count,
                  delivered: risk.delivered_count,
                  returned: risk.returned_count,
                })}
                <br />
                {t('oc.riskAdvisory')}
              </p>
            ) : (
              <button type="button" className="btn btn--sm" onClick={() => void checkRisk()}>
                {t('act.riskCheck')}
              </button>
            )}
            {riskError ? <p className="formerror">{riskError}</p> : null}
          </div>
        ) : null}

        <div className="formsection">
          <div className="formsection__title">{t('orders.items')}</div>
          {lines.map((line) => (
            <ItemLine
              key={line.key}
              line={line}
              onChange={(patch) => update(line.key, patch)}
              onRemove={lines.length > 1 ? () => setLines((current) => current.filter((row) => row.key !== line.key)) : undefined}
            />
          ))}
          <button type="button" className="btn btn--sm" onClick={() => setLines((current) => [...current, blankLine()])}>
            {t('oc.addItem')}
          </button>
        </div>

        <div className="formsection formgrid">
          <label className="field">
            <span className="field__label">{t('oc.deliveryFee')}</span>
            <input className="input" inputMode="decimal" value={deliveryFee} onChange={(event) => setDeliveryFee(event.target.value)} placeholder="0" />
          </label>
          <label className="field">
            <span className="field__label">{t('oc.discount')}</span>
            <input className="input" inputMode="decimal" value={discount} onChange={(event) => setDiscount(event.target.value)} placeholder="0" />
          </label>
          <label className="field">
            <span className="field__label">{t('orders.cod')}</span>
            <input className="input" inputMode="decimal" value={cod} onChange={(event) => setCod(event.target.value)} />
            <span className="field__hint">{t('oc.codHint')}</span>
          </label>
          <label className="field formgrid__full">
            <span className="field__label">{t('oc.note')}</span>
            <textarea className="textarea" rows={2} maxLength={4000} value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
        </div>

        {duplicates?.possible_duplicate ? (
          <div className="notice notice--warn" style={{ display: 'block' }} role="status">
            <strong>{t('oc.duplicateTitle')}</strong>
            <p className="card__hint" style={{ margin: '4px 0' }}>{duplicates.message || t('oc.duplicateHint', { hours: duplicates.window_hours })}</p>
            {duplicates.candidates.map((candidate) => (
              <Row
                key={candidate.order_id}
                label={`${candidate.order_number} · ${t('oc.hoursAgo', { hours: Math.round(candidate.hours_ago) })}`}
                value={
                  <Link href={`/orders?order=${candidate.order_id}`} target="_blank">
                    {formatPaisa(candidate.cod_amount_paisa, { locale })}
                    {candidate.is_strong ? ` · ${t('oc.strongMatch')}` : ''}
                  </Link>
                }
              />
            ))}
          </div>
        ) : null}

        {error ? (
          <p className="formerror" role="alert">
            {error}
          </p>
        ) : null}

        <div className="formactions">
          <span className="card__hint">{t('oc.draftNote')}</span>
          <button type="button" className="btn" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? t('set.saving') : t('oc.save')}
          </button>
        </div>
      </form>
    </Drawer>
  );
}

/** One item: a catalogue product (with variant) or a free-text line. */
function ItemLine({
  line,
  onChange,
  onRemove,
}: {
  line: Line;
  onChange: (patch: Partial<Line>) => void;
  onRemove?: () => void;
}) {
  const { t, locale } = useSession();
  const { can } = useShop();
  const [focused, setFocused] = useState(false);
  const term = useDebounced(line.productId ? '' : line.productName.trim(), 300);
  const hits = useApi<Page<ProductHit>>(
    focused && term.length >= 2 && can('product.view') ? '/products' : null,
    { search: term, limit: 8 },
  );
  const options = useMemo(() => (hits.data?.items ?? []).filter((item) => item.is_active), [hits.data]);

  function pick(product: ProductHit) {
    onChange({
      productId: product.id,
      productName: product.name,
      hasVariants: product.has_variants,
      variants: product.variants.filter((variant) => variant.is_active),
      variantId: '',
      stock: product.stock_tracking_enabled && !product.has_variants ? product.stock_on_hand : null,
      price: paisaToTaka(product.default_selling_price_paisa),
    });
    setFocused(false);
  }

  return (
    <div className="card" style={{ padding: 10, marginBottom: 8 }}>
      <div className="formgrid">
        <label className="field formgrid__full" style={{ position: 'relative' }}>
          <span className="field__label">{t('oc.product')}</span>
          <input
            className="input"
            value={line.productName}
            maxLength={200}
            placeholder={t('oc.productHint')}
            onFocus={() => setFocused(true)}
            onBlur={() => setTimeout(() => setFocused(false), 150)}
            onChange={(event) =>
              onChange({ productName: event.target.value, productId: null, hasVariants: false, variants: [], variantId: '', stock: null })
            }
          />
          {focused && options.length > 0 ? (
            <ul className="popover" style={{ position: 'absolute', zIndex: 5, left: 0, right: 0, listStyle: 'none', margin: 0, padding: 4 }}>
              {options.map((product) => (
                <li key={product.id}>
                  <button type="button" className="menuitem" onMouseDown={(event) => event.preventDefault()} onClick={() => pick(product)}>
                    <span className="menuitem__main">
                      <span className="menuitem__title">{product.name}</span>
                      <span className="menuitem__sub">
                        {[product.sku, formatPaisa(product.default_selling_price_paisa, { locale }), product.stock_tracking_enabled && !product.has_variants ? `${t('prod.stock')}: ${product.stock_on_hand}` : null]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {line.productId ? <span className="field__hint">{t('oc.fromCatalogue')}</span> : line.productName ? <span className="field__hint">{t('oc.freeText')}</span> : null}
        </label>
        {line.hasVariants ? (
          <label className="field">
            <span className="field__label">{t('inv.variant')} *</span>
            <select
              className="select"
              value={line.variantId}
              required
              onChange={(event) => {
                const variant = line.variants.find((row) => row.id === event.target.value);
                onChange({
                  variantId: event.target.value,
                  stock: variant ? variant.stock_on_hand : null,
                  price: variant && variant.price_paisa !== null ? paisaToTaka(variant.price_paisa) : line.price,
                });
              }}
            >
              <option value="">{t('inv.chooseVariant')}</option>
              {line.variants.map((variant) => (
                <option key={variant.id} value={variant.id}>
                  {variant.name} ({variant.stock_on_hand})
                </option>
              ))}
            </select>
          </label>
        ) : !line.productId ? (
          <label className="field">
            <span className="field__label">{t('oc.variantLabel')}</span>
            <input className="input" maxLength={120} value={line.variantLabel} onChange={(event) => onChange({ variantLabel: event.target.value })} />
          </label>
        ) : null}
        <label className="field">
          <span className="field__label">{t('oc.quantity')}</span>
          <input className="input" type="number" min={1} max={9999} value={line.quantity} onChange={(event) => onChange({ quantity: event.target.value })} />
          {line.stock !== null ? (
            <span className={`field__hint${Number(line.quantity) > line.stock ? ' text--bad' : ''}`}>
              {t('oc.inStock', { count: line.stock })}
            </span>
          ) : null}
        </label>
        <label className="field">
          <span className="field__label">{t('oc.unitPrice')}</span>
          <input className="input" inputMode="decimal" value={line.price} onChange={(event) => onChange({ price: event.target.value })} />
        </label>
      </div>
      {onRemove ? (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onRemove}>
          {t('oc.removeItem')}
        </button>
      ) : null}
    </div>
  );
}
