'use client';

import { useState } from 'react';

import { problemText, useIntegrationLabels } from '@/components/Integrations';
import { Card, Chip, EmptyState, ErrorState, Row } from '@/components/ui';
import { api, type Page } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { strings } from '@/lib/i18n';
import type { Connection } from '@/lib/integrations';
import {
  LINK_STATES,
  SYNC_STATES,
  type Conflict,
  type Link,
  type LinkPage,
  type SyncSettings,
  type SyncView,
} from '@/lib/integrations-sync';
import { useApi, useDebounced } from '@/lib/useApi';

function key(value: string): StringKey {
  return (value in strings.en ? value : 'int.none') as StringKey;
}

function useRun() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      return true;
    } catch (failure) {
      setError(failure);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

// ---------------------------------------------------------------- settings ---

type Choice = 'products' | 'inventory' | 'order_status' | 'fulfillment';
const CHOICES: { field: Choice; feature: string; options: string[] }[] = [
  { field: 'products', feature: 'catalog', options: ['MANUAL', 'EXTERNAL', 'ECOMSBD'] },
  { field: 'inventory', feature: 'inventory', options: ['NONE', 'ECOMSBD', 'EXTERNAL'] },
  { field: 'order_status', feature: 'order_status', options: ['OFF', 'TWO_WAY'] },
  { field: 'fulfillment', feature: 'fulfillment', options: ['OFF', 'ON'] },
];

export function SyncSettingsPanel({
  connection,
  canManage,
  onChanged,
}: {
  connection: Connection;
  canManage: boolean;
  onChanged: () => void;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const view = useApi<SyncView>(`/integrations/${connection.id}/sync-settings`);
  const [draft, setDraft] = useState<SyncSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useRun();
  const shopify = connection.provider === 'SHOPIFY';
  const current = draft ?? view.data?.settings ?? null;
  const locations = useApi<{ items: { id: string; name: string; active: boolean }[] }>(
    shopify && canManage && current?.inventory !== 'NONE' && !view.data?.reconnect_scopes.length
      ? `/integrations/${connection.id}/locations`
      : null,
  );

  if (view.error) return <ErrorState error={view.error} onRetry={view.reload} />;
  if (!current || !view.data) return null;
  const caps = view.data.capabilities;
  const change = (patch: Partial<SyncSettings>) => {
    setSaved(false);
    setDraft({ ...current, ...patch });
  };
  const save = () =>
    run(async () => {
      await api.put(`/integrations/${connection.id}/sync-settings`, current);
      setDraft(null);
      setSaved(true);
      view.reload();
      onChanged();
    });
  const reconnect = () =>
    run(async () => {
      const result = await api.post<{ authorize_url: string | null }>(
        `/integrations/${connection.id}/connect`,
        { shop: connection.account_id },
      );
      if (result.authorize_url) window.location.assign(result.authorize_url);
    });
  const problem = problemText(error, labels.code);
  return (
    <div className="grid2">
      <Card title={t('sync.title')} hint={t('sync.hint')}>
        <div className="wizard__body">
          <Row label={t('sync.orders')} value={t('sync.ordersFixed')} />
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="checkbox"
              disabled={!canManage || busy}
              checked={current.catalog}
              onChange={(event) => change({ catalog: event.target.checked })}
            />
            {t('sync.catalog')}
          </label>
          {CHOICES.map(({ field, feature, options }) => (
            <fieldset key={field} className="wizard__choices" style={{ gridTemplateColumns: '1fr' }} disabled={!canManage || busy}>
              <legend>
                <strong>{t(key(`sync.${field}`))}</strong>
              </legend>
              {options.map((option) => (
                <label key={option} className="choice" data-checked={current[field] === option}>
                  <input
                    type="radio"
                    name={field}
                    value={option}
                    checked={current[field] === option}
                    onChange={() => change({ [field]: option } as Partial<SyncSettings>)}
                  />
                  <span>{t(key(`sync.${field}.${option}`))}</span>
                </label>
              ))}
              {caps[feature]?.blocker && caps[feature]?.enabled ? (
                <p className="text--bad">{t(key(`sync.blocker.${caps[feature].blocker}`))}</p>
              ) : null}
            </fieldset>
          ))}
          {shopify && current.inventory !== 'NONE' && locations.data ? (
            <label className="field">
              <span className="field__label">{t('sync.location')}</span>
              <select
                className="select"
                disabled={!canManage || busy}
                value={current.location_id ?? ''}
                onChange={(event) => change({ location_id: event.target.value || null })}
              >
                <option value="">{t('sync.chooseLocation')}</option>
                {locations.data.items
                  .filter((location) => location.active)
                  .map((location) => (
                    <option key={location.id} value={location.id}>
                      {location.name}
                    </option>
                  ))}
              </select>
            </label>
          ) : null}
          {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
          {saved ? <p className="text--good">{t('sync.saved')}</p> : null}
          {canManage ? (
            <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="btn btn--primary" disabled={busy || !draft} onClick={() => void save()}>
                {t('sync.save')}
              </button>
              {shopify && view.data.reconnect_scopes.length && !draft ? (
                <button type="button" className="btn" disabled={busy} onClick={() => void reconnect()}>
                  {t('sync.reconnect')}
                </button>
              ) : null}
            </span>
          ) : null}
          {shopify && view.data.reconnect_scopes.length ? (
            <p className="card__hint">
              {t('sync.reconnectHint', { scopes: view.data.reconnect_scopes.join(', ') })}
            </p>
          ) : null}
        </div>
      </Card>
      <Card title={t('sync.statusMap')}>
        {SYNC_STATES.map((state) => (
          <Row
            key={state}
            label={t(key(`sync.state.${state}`))}
            value={view.data?.status_map[state] ?? <span className="table__sub">{t('sync.notPushed')}</span>}
          />
        ))}
        <Row label={t('sync.lastCatalog')} value={labels.when(view.data.catalog_synced_at)} />
        <Row label={t('sync.lastInventory')} value={labels.when(view.data.inventory_synced_at)} />
        <p className="card__hint">{t('sync.every5')}</p>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------- mapping ---

interface ProductOption {
  id: string;
  name: string;
  sku: string | null;
  has_variants: boolean;
  variants: { id: string; name: string; sku: string | null }[];
}

export function MappingPanel({ connection, canManage }: { connection: Connection; canManage: boolean }) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const [state, setState] = useState<string>('');
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search);
  const page = useApi<LinkPage>(`/integrations/${connection.id}/links`, {
    state: state || undefined,
    q: debounced || undefined,
  });
  const [editing, setEditing] = useState<Link | null>(null);
  const [queued, setQueued] = useState(false);
  const { busy, error, run } = useRun();
  const problem = problemText(error, labels.code);
  const counts = page.data?.counts ?? {};
  const act = (link: Link, body: Record<string, unknown>) =>
    run(async () => {
      await api.put(`/integrations/${connection.id}/links/${link.id}`, body);
      setEditing(null);
      page.reload();
    });

  return (
    <Card
      title={t('map.title')}
      hint={t('map.hint')}
      padded={false}
      actions={
        canManage ? (
          <button
            type="button"
            className="btn btn--sm"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.post(`/integrations/${connection.id}/catalog/sync`);
                setQueued(true);
              })
            }
          >
            {t('map.sync')}
          </button>
        ) : null
      }
    >
      <div className="toolbar" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: 12 }}>
        <button type="button" className={`btn btn--sm${state === '' ? ' btn--primary' : ''}`} onClick={() => setState('')}>
          {t('map.all')}
        </button>
        {LINK_STATES.map((value) => (
          <button
            key={value}
            type="button"
            className={`btn btn--sm${state === value ? ' btn--primary' : ''}`}
            onClick={() => setState(value)}
          >
            {t(key(`map.state.${value}`))} {counts[value] ? `(${counts[value]})` : ''}
          </button>
        ))}
        <input
          className="input input--search"
          placeholder={t('map.search')}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {queued ? <p className="card__hint" style={{ padding: '0 12px' }}>{t('map.syncQueued')}</p> : null}
      {problem ? <p className="formerror" style={{ padding: '0 12px' }}>{problem}</p> : error ? <ErrorState error={error} /> : null}
      {page.error ? <ErrorState error={page.error} onRetry={page.reload} /> : null}
      {page.data && page.data.items.length === 0 ? (
        <EmptyState title={t('map.none')} />
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('map.col.store')}</th>
                <th>{t('map.col.sku')}</th>
                <th>{t('map.col.ecomsbd')}</th>
                <th className="num">{t('map.col.stock')}</th>
                <th>{t('map.col.state')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(page.data?.items ?? []).map((link) => (
                <tr key={link.id}>
                  <td style={{ whiteSpace: 'normal' }}>
                    <span className="table__primary">{link.external_title}</span>
                  </td>
                  <td>{link.external_sku ?? '—'}</td>
                  <td style={{ whiteSpace: 'normal' }}>
                    {editing?.id === link.id ? (
                      <ProductPicker busy={busy} onPick={(product, variant) => void act(link, { product_id: product, variant_id: variant })} />
                    ) : (
                      <>
                        <span>{link.internal_name ?? '—'}</span>
                        {link.match_source ? (
                          <span className="table__sub">{t(key(`map.source.${link.match_source}`))}</span>
                        ) : null}
                      </>
                    )}
                  </td>
                  <td className="num">
                    {link.external_tracked ? (link.external_qty ?? '—') : <span className="table__sub">{t('map.notTracked')}</span>}
                  </td>
                  <td>
                    <Chip
                      label={t(key(`map.state.${link.state}`))}
                      tone={link.state === 'MATCHED' ? 'good' : link.state === 'CONFLICT' ? 'bad' : link.state === 'UNMATCHED' ? 'warn' : 'neutral'}
                    />
                  </td>
                  <td>
                    {canManage ? (
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        <button type="button" className="btn btn--sm" disabled={busy} onClick={() => setEditing(editing?.id === link.id ? null : link)}>
                          {t('map.choose')}
                        </button>
                        {link.product_id ? (
                          <button type="button" className="btn btn--sm btn--ghost" disabled={busy} onClick={() => void act(link, { product_id: null })}>
                            {t('map.unmap')}
                          </button>
                        ) : null}
                        {link.state !== 'IGNORED' ? (
                          <button type="button" className="btn btn--sm btn--ghost" disabled={busy} onClick={() => void act(link, { ignore: true })}>
                            {t('map.ignore')}
                          </button>
                        ) : null}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ProductPicker({
  busy,
  onPick,
}: {
  busy: boolean;
  onPick: (productId: string, variantId: string | null) => void;
}) {
  const { t } = useIntegrationLabels();
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search);
  const [product, setProduct] = useState<ProductOption | null>(null);
  const [variant, setVariant] = useState('');
  const results = useApi<Page<ProductOption>>(debounced ? '/products' : null, { search: debounced, limit: 10 });
  return (
    <span style={{ display: 'grid', gap: 6, minWidth: 220 }}>
      <input
        className="input"
        placeholder={t('map.product')}
        value={search}
        onChange={(event) => {
          setSearch(event.target.value);
          setProduct(null);
        }}
      />
      {!product && results.data ? (
        <span style={{ display: 'grid', gap: 4 }}>
          {results.data.items.map((option) => (
            <button key={option.id} type="button" className="btn btn--sm btn--ghost" onClick={() => setProduct(option)}>
              {option.name}
              {option.sku ? ` · ${option.sku}` : ''}
            </button>
          ))}
        </span>
      ) : null}
      {product?.has_variants ? (
        <select className="select" value={variant} onChange={(event) => setVariant(event.target.value)}>
          <option value="">{t('map.variant')}</option>
          {product.variants.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
              {option.sku ? ` · ${option.sku}` : ''}
            </option>
          ))}
        </select>
      ) : null}
      {product ? (
        <button
          type="button"
          className="btn btn--sm btn--primary"
          disabled={busy || (product.has_variants && !variant)}
          onClick={() => onPick(product.id, variant || null)}
        >
          {t('map.save')}
        </button>
      ) : null}
    </span>
  );
}

// --------------------------------------------------------------- conflicts ---

function side(value: Record<string, unknown> | undefined, t: ReturnType<typeof useIntegrationLabels>['t']): string {
  if (!value) return '—';
  if (typeof value.available === 'number') return t('conf.available', { count: value.available });
  return Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => {
      if (Array.isArray(v)) {
        return v.map((item) => (typeof item === 'object' && item ? `${(item as { name?: string }).name ?? ''} ×${(item as { quantity?: number }).quantity ?? ''}` : String(item))).join(', ');
      }
      return k === 'cod_amount_paisa' ? `৳${Number(v) / 100}` : String(v);
    })
    .join(' · ');
}

export function ConflictsPanel({
  connectionId,
  canManage,
  canRetry,
  onChanged,
}: {
  connectionId?: string;
  canManage: boolean;
  canRetry: boolean;
  onChanged?: () => void;
}) {
  const labels = useIntegrationLabels();
  const { t } = labels;
  const list = useApi<{ items: Conflict[] }>('/integrations/conflicts', {
    connection_id: connectionId,
  });
  const { busy, error, run } = useRun();
  const problem = problemText(error, labels.code);
  const items = list.data?.items ?? [];
  return (
    <Card title={t('conf.title')} hint={t('conf.hint')}>
      {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
      {problem ? <p className="formerror">{problem}</p> : error ? <ErrorState error={error} /> : null}
      {items.length === 0 && list.data ? <EmptyState title={t('conf.empty')} /> : null}
      <div style={{ display: 'grid', gap: 14 }}>
        {items.map((conflict) => {
          const allowed = conflict.entity === 'ORDER' ? canRetry : canManage;
          return (
            <section key={conflict.id} style={{ borderTop: '1px solid var(--stroke)', paddingTop: 12 }}>
              <strong>{t(key(`conf.kind.${conflict.kind}`))}</strong>
              {conflict.detail.title ? <span className="table__sub">{conflict.detail.title}</span> : null}
              <Row label={t('conf.ecomsbd')} value={side(conflict.detail.ecomsbd, t)} />
              <Row label={t('conf.store')} value={side(conflict.detail.external, t)} />
              {typeof conflict.detail.last_agreed === 'number' ? (
                <p className="table__sub">{t('conf.lastAgreed', { count: conflict.detail.last_agreed })}</p>
              ) : null}
              <p className="card__hint">
                {t('conf.recommended')}: {t(key(`conf.action.${conflict.recommended}`))}
              </p>
              {allowed ? (
                <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {conflict.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`btn btn--sm${option === conflict.recommended ? ' btn--primary' : ''}`}
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await api.post(`/integrations/conflicts/${conflict.id}/resolve`, { resolution: option });
                          list.reload();
                          onChanged?.();
                        })
                      }
                    >
                      {t(key(`conf.action.${option}`))}
                    </button>
                  ))}
                </span>
              ) : null}
            </section>
          );
        })}
      </div>
    </Card>
  );
}
