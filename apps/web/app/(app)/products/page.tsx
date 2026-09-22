'use client';

import { useMemo, useState, type FormEvent } from 'react';

import { DataTable, useCursorStack, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, ErrorState, Row, Tile, type Tone } from '@/components/ui';
import { api, ApiError, type Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi, useDebounced } from '@/lib/useApi';

interface Variant {
  id: string;
  name: string;
  sku: string | null;
  stock_on_hand: number;
  low_stock_threshold: number | null;
  is_low_stock: boolean;
  is_active: boolean;
}

interface Product {
  id: string;
  name: string;
  sku: string | null;
  cost_paisa: number;
  default_selling_price_paisa: number;
  stock_tracking_enabled: boolean;
  stock_on_hand: number;
  low_stock_threshold: number | null;
  is_low_stock: boolean;
  is_active: boolean;
  is_archived: boolean;
  has_variants: boolean;
  variants: Variant[];
}

interface Movement {
  id: string;
  quantity_delta: number;
  balance_after: number;
  reason: string;
  note: string | null;
  reference: string | null;
  variant_name: string | null;
  order_number: string | null;
  actor_name: string | null;
  occurred_at: string;
}

interface StockSummary {
  tracked_products: number;
  total_units: number;
  low_stock_items: number;
  out_of_stock_items: number;
}

type StockFilter = 'all' | 'low' | 'out';

const MOVEMENT_TYPES = [
  'OPENING',
  'BOOKED_DECREMENT',
  'RESTOCK',
  'RETURN_RESTORE',
  'PARTIAL_RETURN_RESTORE',
  'CANCEL_RESTORE',
  'MANUAL_ADJUSTMENT',
  'DAMAGED_WRITE_OFF',
  'IMPORT_ADJUSTMENT',
] as const;

function stockTone(stock: number, low: boolean): Tone | null {
  if (stock <= 0) return 'bad';
  return low ? 'warn' : null;
}

/**
 * Products and inventory.
 *
 * Stock figures and the low-stock judgement are the API's, never recomputed
 * here: thresholds are per product or per variant and can be absent, and a
 * second implementation in the browser would give two answers to "should I
 * reorder?". Every change goes through the ledger — the page never sends a
 * total.
 */
export default function ProductsPage() {
  const { t, locale } = useSession();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<StockFilter>('all');
  const [openId, setOpenId] = useState<string | null>(null);
  const debounced = useDebounced(search);
  const paging = useCursorStack();

  const query = useMemo(
    () => ({
      limit: 50,
      cursor: paging.cursor,
      search: debounced.trim() || undefined,
      low_stock_only: filter === 'low' || undefined,
      out_of_stock_only: filter === 'out' || undefined,
    }),
    [paging.cursor, debounced, filter],
  );

  const { data, loading, error, reload } = useApi<Page<Product>>('/products', query);
  const summary = useApi<StockSummary>('/products/stock-summary');
  const rows = data?.items ?? [];

  const columns: Column<Product>[] = [
    {
      key: 'name',
      header: t('prod.name'),
      render: (row) => (
        <>
          <div className="table__primary">{row.name}</div>
          {row.sku ? <div className="table__sub">{row.sku}</div> : null}
        </>
      ),
    },
    {
      key: 'variant',
      header: t('inv.variant'),
      render: (row) =>
        row.has_variants ? (
          row.variants.map((variant) => {
            const tone = stockTone(variant.stock_on_hand, variant.is_low_stock);
            return (
              <div key={variant.id} className="table__sub">
                {variant.name}
                {variant.sku ? ` · ${variant.sku}` : ''} — {variant.stock_on_hand}
                {variant.low_stock_threshold !== null ? ` / ${variant.low_stock_threshold}` : ''}
                {tone && variant.is_active ? (
                  <>
                    {' '}
                    <Chip
                      label={t(tone === 'bad' ? 'inv.outOfStock' : 'prod.lowStock')}
                      tone={tone}
                    />
                  </>
                ) : null}
              </div>
            );
          })
        ) : (
          <span className="table__sub">—</span>
        ),
    },
    {
      key: 'price',
      header: t('prod.price'),
      numeric: true,
      render: (row) => formatPaisa(row.default_selling_price_paisa, { locale }),
    },
    {
      key: 'stock',
      header: t('prod.stock'),
      numeric: true,
      render: (row) => {
        if (!row.stock_tracking_enabled) {
          // Not zero. A product that does not track stock has no number here,
          // and showing 0 would read as "sold out".
          return '—';
        }
        const tone = stockTone(row.stock_on_hand, row.is_low_stock);
        return (
          <>
            {row.stock_on_hand}
            {tone ? (
              <>
                {' '}
                <Chip
                  label={t(tone === 'bad' && !row.has_variants ? 'inv.outOfStock' : 'prod.lowStock')}
                  tone={tone}
                />
              </>
            ) : null}
          </>
        );
      },
    },
    {
      key: 'threshold',
      header: t('inv.threshold'),
      numeric: true,
      render: (row) =>
        row.has_variants ? t('inv.perVariant') : (row.low_stock_threshold ?? '—'),
    },
    {
      key: 'status',
      header: t('inv.status'),
      render: (row) =>
        row.is_archived ? (
          <Chip label={t('inv.archived')} />
        ) : row.is_active ? (
          <Chip label={t('inv.active')} tone="good" />
        ) : (
          <Chip label={t('inv.inactive')} />
        ),
    },
    {
      key: 'actions',
      header: '',
      render: (row) =>
        row.stock_tracking_enabled ? (
          <button
            type="button"
            className="btn btn--sm"
            onClick={(event) => {
              event.stopPropagation();
              setOpenId(row.id);
            }}
          >
            {t('inv.adjustOrRestock')}
          </button>
        ) : null,
    },
  ];

  const tiles = summary.data;

  return (
    <>
      <PageHeader title={t('prod.title')} subtitle={t('prod.subtitle')} />
      <div className="content">
        {tiles ? (
          <div className="tiles">
            <Tile label={t('inv.trackedProducts')} value={String(tiles.tracked_products)} />
            <Tile label={t('inv.unitsInStock')} value={String(tiles.total_units)} />
            <Tile label={t('prod.lowStock')} value={String(tiles.low_stock_items)} />
            <Tile label={t('inv.outOfStock')} value={String(tiles.out_of_stock_items)} />
          </div>
        ) : null}
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={rows}
            loading={loading}
            error={error}
            onRetry={reload}
            emptyTitle={t('prod.empty')}
            onRowClick={(row) => setOpenId(row.id)}
            toolbar={
              <>
                <input
                  className="input input--search"
                  type="search"
                  placeholder={t('prod.searchHint')}
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    paging.reset();
                  }}
                  aria-label={t('common.search')}
                />
                <select
                  className="select"
                  value={filter}
                  aria-label={t('inv.stockFilter')}
                  onChange={(event) => {
                    setFilter(event.target.value as StockFilter);
                    paging.reset();
                  }}
                >
                  <option value="all">{t('inv.filterAll')}</option>
                  <option value="low">{t('prod.lowStock')}</option>
                  <option value="out">{t('inv.outOfStock')}</option>
                </select>
              </>
            }
            pagination={{
              canGoBack: paging.canGoBack,
              canGoForward: Boolean(data?.has_more && data?.next_cursor),
              onBack: paging.back,
              onForward: () => paging.forward(data?.next_cursor ?? null),
            }}
          />
        </Card>
      </div>
      {openId ? (
        <ProductDrawer
          productId={openId}
          onClose={() => setOpenId(null)}
          onChanged={() => {
            reload();
            summary.reload();
          }}
        />
      ) : null}
    </>
  );
}

// --------------------------------------------------------------------------- //
// Drawer: variants, stock actions, history
// --------------------------------------------------------------------------- //

type Action = 'adjust' | 'restock' | 'variant';

function ProductDrawer({
  productId,
  onClose,
  onChanged,
}: {
  productId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t, locale } = useSession();
  const product = useApi<Product>(`/products/${productId}`);
  const [action, setAction] = useState<Action>('adjust');
  const [historyNonce, setHistoryNonce] = useState(0);
  const data = product.data;

  const changed = () => {
    product.reload();
    setHistoryNonce((value) => value + 1);
    onChanged();
  };

  return (
    <Drawer title={data?.name ?? t('prod.name')} onClose={onClose}>
      {product.error ? <ErrorState error={product.error} onRetry={product.reload} /> : null}
      {data ? (
        <>
          <Row label={t('prod.sku')} value={data.sku ?? '—'} />
          <Row label={t('prod.stock')} value={String(data.stock_on_hand)} />
          <Row label={t('prod.cost')} value={formatPaisa(data.cost_paisa, { locale })} />
          {data.has_variants ? (
            <table className="table">
              <thead>
                <tr>
                  <th>{t('inv.variant')}</th>
                  <th>{t('prod.sku')}</th>
                  <th className="num">{t('prod.stock')}</th>
                  <th className="num">{t('inv.threshold')}</th>
                </tr>
              </thead>
              <tbody>
                {data.variants.map((variant) => (
                  <tr key={variant.id}>
                    <td>
                      {variant.name}
                      {!variant.is_active ? ` (${t('inv.inactive')})` : ''}
                    </td>
                    <td>{variant.sku ?? '—'}</td>
                    <td className="num">
                      {variant.stock_on_hand}
                      {variant.is_low_stock ? (
                        <>
                          {' '}
                          <Chip label={t('prod.lowStock')} tone="warn" />
                        </>
                      ) : null}
                    </td>
                    <td className="num">{variant.low_stock_threshold ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}

          {data.stock_tracking_enabled ? (
            <>
              <div className="toolbar" role="tablist">
                {(['adjust', 'restock', 'variant'] as const).map((key) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={action === key}
                    className={action === key ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
                    onClick={() => setAction(key)}
                  >
                    {t(`inv.action.${key}` as StringKey)}
                  </button>
                ))}
              </div>
              {action === 'adjust' ? <AdjustForm product={data} onDone={changed} /> : null}
              {action === 'restock' ? <RestockForm product={data} onDone={changed} /> : null}
              {action === 'variant' ? <VariantForm product={data} onDone={changed} /> : null}
            </>
          ) : null}

          <StockHistory product={data} nonce={historyNonce} />
        </>
      ) : null}
    </Drawer>
  );
}

function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : String(error);
}

function VariantSelect({
  product,
  value,
  onChange,
}: {
  product: Product;
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useSession();
  if (!product.has_variants) return null;
  return (
    <label className="field">
      <span className="field__label">{t('inv.variant')}</span>
      <select
        className="select"
        value={value}
        required
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t('inv.chooseVariant')}</option>
        {product.variants
          .filter((variant) => variant.is_active)
          .map((variant) => (
            <option key={variant.id} value={variant.id}>
              {variant.name} ({variant.stock_on_hand})
            </option>
          ))}
      </select>
    </label>
  );
}

/** A client request id, so a double-submit records one movement. */
function newRequestId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function AdjustForm({ product, onDone }: { product: Product; onDone: () => void }) {
  const { t } = useSession();
  const [variantId, setVariantId] = useState('');
  const [direction, setDirection] = useState<'increase' | 'decrease'>('decrease');
  const [quantity, setQuantity] = useState('');
  const [reason, setReason] = useState('MANUAL_ADJUSTMENT');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [requestId, setRequestId] = useState(newRequestId);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const amount = Number.parseInt(quantity, 10);
    if (!Number.isFinite(amount) || amount <= 0) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/products/${product.id}/stock-adjustments`, {
        quantity_delta: direction === 'increase' ? amount : -amount,
        reason: direction === 'increase' && reason === 'DAMAGED_WRITE_OFF' ? 'MANUAL_ADJUSTMENT' : reason,
        note: note.trim(),
        variant_id: variantId || undefined,
        idempotency_key: requestId,
      });
      setQuantity('');
      setNote('');
      setRequestId(newRequestId());
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <VariantSelect product={product} value={variantId} onChange={setVariantId} />
      <label className="field">
        <span className="field__label">{t('inv.direction')}</span>
        <select
          className="select"
          value={direction}
          onChange={(event) => setDirection(event.target.value as 'increase' | 'decrease')}
        >
          <option value="increase">{t('inv.increase')}</option>
          <option value="decrease">{t('inv.decrease')}</option>
        </select>
      </label>
      <label className="field">
        <span className="field__label">{t('inv.quantity')}</span>
        <input
          className="input"
          inputMode="numeric"
          required
          value={quantity}
          onChange={(event) => setQuantity(event.target.value.replace(/\D/g, ''))}
        />
      </label>
      <label className="field">
        <span className="field__label">{t('inv.reason')}</span>
        <select className="select" value={reason} onChange={(event) => setReason(event.target.value)}>
          <option value="MANUAL_ADJUSTMENT">{t('inv.reason.MANUAL_ADJUSTMENT')}</option>
          {direction === 'decrease' ? (
            <option value="DAMAGED_WRITE_OFF">{t('inv.reason.DAMAGED_WRITE_OFF')}</option>
          ) : null}
        </select>
      </label>
      <label className="field">
        <span className="field__label">{t('inv.note')}</span>
        <input
          className="input"
          required
          maxLength={400}
          placeholder={t('inv.noteHint')}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {errorText(error) ? <p className="formerror" role="alert">{errorText(error)}</p> : null}
      <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
        {t('inv.recordAdjustment')}
      </button>
    </form>
  );
}

function RestockForm({ product, onDone }: { product: Product; onDone: () => void }) {
  const { t } = useSession();
  const [variantId, setVariantId] = useState('');
  const [quantity, setQuantity] = useState('');
  const [unitCost, setUnitCost] = useState('');
  const [updateCost, setUpdateCost] = useState(false);
  const [reference, setReference] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [requestId, setRequestId] = useState(newRequestId);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const amount = Number.parseInt(quantity, 10);
    if (!Number.isFinite(amount) || amount <= 0) return;
    // Taka to paisa without floating point: "12.5" -> 1250.
    const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(unitCost.trim());
    const costPaisa = match
      ? Number.parseInt(match[1] ?? '0', 10) * 100 + Number.parseInt((match[2] ?? '').padEnd(2, '0'), 10)
      : undefined;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/products/${product.id}/restocks`, {
        quantity: amount,
        variant_id: variantId || undefined,
        unit_cost_paisa: costPaisa,
        update_cost: updateCost && costPaisa !== undefined,
        reference: reference.trim() || undefined,
        note: note.trim() || undefined,
        idempotency_key: requestId,
      });
      setQuantity('');
      setUnitCost('');
      setReference('');
      setNote('');
      setRequestId(newRequestId());
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <VariantSelect product={product} value={variantId} onChange={setVariantId} />
      <label className="field">
        <span className="field__label">{t('inv.quantity')}</span>
        <input
          className="input"
          inputMode="numeric"
          required
          value={quantity}
          onChange={(event) => setQuantity(event.target.value.replace(/\D/g, ''))}
        />
      </label>
      <label className="field">
        <span className="field__label">{t('inv.unitCost')}</span>
        <input
          className="input"
          inputMode="decimal"
          value={unitCost}
          onChange={(event) => setUnitCost(event.target.value.replace(/[^\d.]/g, ''))}
        />
      </label>
      {unitCost ? (
        <label className="field">
          <input
            type="checkbox"
            checked={updateCost}
            onChange={(event) => setUpdateCost(event.target.checked)}
          />{' '}
          {t('inv.updateCost')}
        </label>
      ) : null}
      <label className="field">
        <span className="field__label">{t('inv.reference')}</span>
        <input
          className="input"
          maxLength={120}
          value={reference}
          onChange={(event) => setReference(event.target.value)}
        />
      </label>
      <label className="field">
        <span className="field__label">{t('inv.note')}</span>
        <input
          className="input"
          maxLength={400}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {errorText(error) ? <p className="formerror" role="alert">{errorText(error)}</p> : null}
      <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
        {t('inv.recordRestock')}
      </button>
    </form>
  );
}

function VariantForm({ product, onDone }: { product: Product; onDone: () => void }) {
  const { t } = useSession();
  const [name, setName] = useState('');
  const [sku, setSku] = useState('');
  const [opening, setOpening] = useState('');
  const [threshold, setThreshold] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/products/${product.id}/variants`, {
        name: name.trim(),
        sku: sku.trim() || undefined,
        opening_stock: Number.parseInt(opening, 10) || 0,
        low_stock_threshold: threshold ? Number.parseInt(threshold, 10) : undefined,
      });
      setName('');
      setSku('');
      setOpening('');
      setThreshold('');
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      {!product.has_variants && product.stock_on_hand !== 0 ? (
        <p className="table__sub">{t('inv.splitNote', { count: product.stock_on_hand })}</p>
      ) : null}
      <label className="field">
        <span className="field__label">{t('inv.variantName')}</span>
        <input
          className="input"
          required
          maxLength={120}
          placeholder={t('inv.variantNameHint')}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="field">
        <span className="field__label">{t('prod.sku')}</span>
        <input className="input" maxLength={64} value={sku} onChange={(event) => setSku(event.target.value)} />
      </label>
      <label className="field">
        <span className="field__label">{t('inv.openingStock')}</span>
        <input
          className="input"
          inputMode="numeric"
          value={opening}
          onChange={(event) => setOpening(event.target.value.replace(/\D/g, ''))}
        />
      </label>
      <label className="field">
        <span className="field__label">{t('inv.threshold')}</span>
        <input
          className="input"
          inputMode="numeric"
          value={threshold}
          onChange={(event) => setThreshold(event.target.value.replace(/\D/g, ''))}
        />
      </label>
      {errorText(error) ? <p className="formerror" role="alert">{errorText(error)}</p> : null}
      <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
        {t('inv.addVariant')}
      </button>
    </form>
  );
}

/** Local-day bounds as ISO instants, so "from 10 Sep" means the seller's day. */
function dayStart(value: string): string | undefined {
  return value ? new Date(`${value}T00:00:00`).toISOString() : undefined;
}

function dayAfter(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + 1);
  return date.toISOString();
}

function StockHistory({ product, nonce }: { product: Product; nonce: number }) {
  const { t, locale } = useSession();
  const [reason, setReason] = useState('');
  const [variantId, setVariantId] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const paging = useCursorStack();

  const query = useMemo(
    () => ({
      limit: 20,
      cursor: paging.cursor,
      reason: reason || undefined,
      variant_id: variantId || undefined,
      occurred_from: dayStart(from),
      occurred_to: dayAfter(to),
      // Forces a refetch after a change made in this drawer.
      _: nonce,
    }),
    [paging.cursor, reason, variantId, from, to, nonce],
  );
  const history = useApi<Page<Movement>>(`/products/${product.id}/stock-movements`, query);

  const columns: Column<Movement>[] = [
    {
      key: 'when',
      header: t('inv.when'),
      render: (row) => new Date(row.occurred_at).toLocaleString(locale === 'bn' ? 'bn-BD' : 'en-GB'),
    },
    {
      key: 'type',
      header: t('inv.movement'),
      render: (row) => (
        <>
          <div className="table__primary">{t(`inv.reason.${row.reason}` as StringKey)}</div>
          {row.variant_name ? <div className="table__sub">{row.variant_name}</div> : null}
        </>
      ),
    },
    {
      key: 'delta',
      header: '±',
      numeric: true,
      render: (row) => (
        <Chip
          label={`${row.quantity_delta > 0 ? '+' : ''}${row.quantity_delta}`}
          tone={row.quantity_delta > 0 ? 'good' : 'bad'}
        />
      ),
    },
    { key: 'balance', header: t('inv.balance'), numeric: true, render: (row) => row.balance_after },
    {
      key: 'ref',
      header: t('inv.reference'),
      render: (row) =>
        [row.order_number, row.reference, row.note].filter(Boolean).join(' · ') || '—',
    },
    { key: 'actor', header: t('inv.actor'), render: (row) => row.actor_name ?? '—' },
  ];

  return (
    <>
      <h3>{t('inv.history')}</h3>
      <DataTable
        columns={columns}
        rows={history.data?.items ?? []}
        loading={history.loading}
        error={history.error}
        onRetry={history.reload}
        emptyTitle={t('inv.historyEmpty')}
        toolbar={
          <>
            <select
              className="select"
              value={reason}
              aria-label={t('inv.movement')}
              onChange={(event) => {
                setReason(event.target.value);
                paging.reset();
              }}
            >
              <option value="">{t('inv.filterAll')}</option>
              {MOVEMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`inv.reason.${type}` as StringKey)}
                </option>
              ))}
            </select>
            {product.has_variants ? (
              <select
                className="select"
                value={variantId}
                aria-label={t('inv.variant')}
                onChange={(event) => {
                  setVariantId(event.target.value);
                  paging.reset();
                }}
              >
                <option value="">{t('inv.allVariants')}</option>
                {product.variants.map((variant) => (
                  <option key={variant.id} value={variant.id}>
                    {variant.name}
                  </option>
                ))}
              </select>
            ) : null}
            <input
              className="input"
              type="date"
              aria-label={t('inv.from')}
              value={from}
              onChange={(event) => {
                setFrom(event.target.value);
                paging.reset();
              }}
            />
            <input
              className="input"
              type="date"
              aria-label={t('inv.to')}
              value={to}
              onChange={(event) => {
                setTo(event.target.value);
                paging.reset();
              }}
            />
          </>
        }
        pagination={{
          canGoBack: paging.canGoBack,
          canGoForward: Boolean(history.data?.has_more && history.data?.next_cursor),
          onBack: paging.back,
          onForward: () => paging.forward(history.data?.next_cursor ?? null),
        }}
      />
    </>
  );
}
