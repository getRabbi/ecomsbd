'use client';

import { useRouter } from 'next/navigation';
import { useId, useMemo, useState, type KeyboardEvent } from 'react';

import { Icon, type IconName } from '@/components/icons';
import type { Page } from '@/lib/api';
import { formatPaisa } from '@/lib/money';
import { allowed, NAV, QUICK_ACTIONS, visibleNav } from '@/lib/nav';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
import type { OrderSummary } from '@/lib/types';
import { useApi, useDebounced } from '@/lib/useApi';

interface CustomerHit {
  id: string;
  name: string | null;
  phone_masked: string;
  order_count: number;
}

interface ProductHit {
  id: string;
  name: string;
  sku: string | null;
  stock_on_hand: number;
}

interface Entry {
  key: string;
  href: string;
  icon: IconName;
  title: string;
  sub?: string;
}

interface Section {
  key: string;
  label: string;
  entries: Entry[];
  status?: string;
}

const PER_SECTION = 5;

/**
 * Search and quick actions, opened with `/` or Ctrl/⌘ K.
 *
 * There is no global search endpoint, so this asks the three list endpoints
 * the mobile search screen asks — orders, customers, products — each with the
 * API's own `search` and a limit of five. Bounded, debounced, and a superseded
 * request is discarded by `useApi`, so typing never fans out into a full-table
 * fetch. A section the member may not read is not asked at all.
 */
export function CommandPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const { t, locale } = useSession();
  const { permissions, can } = useShop();
  const listId = useId();

  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const term = useDebounced(query.trim(), 250);
  const searching = term.length >= 2;

  const orders = useApi<Page<OrderSummary>>(
    searching && can('order.view') ? '/orders' : null,
    { search: term, limit: PER_SECTION },
  );
  const customers = useApi<Page<CustomerHit>>(
    searching && can('customer.view') ? '/customers' : null,
    { search: term, limit: PER_SECTION },
  );
  const products = useApi<Page<ProductHit>>(
    searching && can('product.view') ? '/products' : null,
    { search: term, limit: PER_SECTION },
  );

  const sections = useMemo<Section[]>(() => {
    const needle = query.trim().toLowerCase();
    const matches = (label: string) => !needle || label.toLowerCase().includes(needle);
    const out: Section[] = [];

    if (searching) {
      const status = (state: { loading: boolean; error: unknown }, empty: boolean) =>
        state.loading ? t('pal.searching') : state.error ? t('pal.sectionFailed') : empty ? t('pal.noResults') : undefined;

      if (can('order.view')) {
        const items = orders.data?.items ?? [];
        out.push({
          key: 'orders',
          label: t('nav.orders'),
          status: status(orders, items.length === 0),
          entries: items.map((order) => ({
            key: `order-${order.id}`,
            href: `/orders?order=${encodeURIComponent(order.id)}`,
            icon: 'orders',
            title: order.order_number,
            sub: [order.customer_name, order.customer_phone_masked, formatPaisa(order.cod_amount_paisa, { locale })]
              .filter(Boolean)
              .join(' · '),
          })),
        });
      }
      if (can('customer.view')) {
        const items = customers.data?.items ?? [];
        out.push({
          key: 'customers',
          label: t('nav.customers'),
          status: status(customers, items.length === 0),
          entries: items.map((customer) => ({
            key: `customer-${customer.id}`,
            href: `/customers/${encodeURIComponent(customer.id)}`,
            icon: 'customers',
            title: customer.name || customer.phone_masked,
            sub: `${customer.phone_masked} · ${customer.order_count} ${t('nav.orders').toLowerCase()}`,
          })),
        });
      }
      if (can('product.view')) {
        const items = products.data?.items ?? [];
        out.push({
          key: 'products',
          label: t('nav.products'),
          status: status(products, items.length === 0),
          entries: items.map((product) => ({
            key: `product-${product.id}`,
            href: `/products?product=${encodeURIComponent(product.id)}`,
            icon: 'products',
            title: product.name,
            sub: [product.sku, `${t('prod.stock')}: ${product.stock_on_hand}`].filter(Boolean).join(' · '),
          })),
        });
      }
    }

    const actions = allowed(QUICK_ACTIONS, permissions)
      .map((action) => ({
        key: `action-${action.id}`,
        href: action.href,
        icon: action.icon,
        title: t(action.labelKey),
      }))
      .filter((entry) => matches(entry.title));
    if (actions.length > 0) {
      out.push({ key: 'actions', label: t('pal.actions'), entries: actions });
    }

    const pages = visibleNav(NAV, permissions)
      .flatMap((group) => group.items)
      .map((item) => ({ key: `page-${item.href}`, href: item.href, icon: item.icon, title: t(item.labelKey) }))
      .filter((entry) => matches(entry.title));
    if (pages.length > 0) {
      out.push({ key: 'pages', label: t('pal.pages'), entries: pages });
    }
    return out;
  }, [query, searching, permissions, can, orders, customers, products, t, locale]);

  const flat: Entry[] = [];
  const positions = new Map<string, number>();
  for (const section of sections) {
    for (const entry of section.entries) {
      positions.set(entry.key, flat.length);
      flat.push(entry);
    }
  }
  const current = flat.length === 0 ? -1 : Math.min(active, flat.length - 1);

  function open(entry: Entry | undefined) {
    if (!entry) {
      return;
    }
    onClose();
    router.push(entry.href);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive(flat.length === 0 ? 0 : (current + 1) % flat.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(flat.length === 0 ? 0 : (current - 1 + flat.length) % flat.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      open(flat[current]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  }

  const optionId = (position: number) => `${listId}-option-${position}`;

  return (
    <div
      className="palette__scrim"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="palette" role="dialog" aria-modal="true" aria-label={t('pal.label')}>
        <div className="palette__inputrow">
          <Icon name="search" />
          <input
            className="palette__input"
            autoFocus
            value={query}
            placeholder={t('pal.placeholder')}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={current >= 0 ? optionId(current) : undefined}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          <kbd>Esc</kbd>
        </div>

        <div className="palette__list" id={listId} role="listbox" aria-label={t('pal.label')}>
          {query.trim().length > 0 && query.trim().length < 2 ? (
            <p className="palette__note">{t('pal.minChars')}</p>
          ) : null}
          {sections.map((section) => (
            <div key={section.key} role="group" aria-label={section.label}>
              <div className="palette__section">{section.label}</div>
              {section.status ? <p className="palette__note">{section.status}</p> : null}
              {section.entries.map((entry) => {
                const position = positions.get(entry.key) ?? -1;
                return (
                  <div
                    key={entry.key}
                    id={optionId(position)}
                    role="option"
                    aria-selected={position === current}
                    className="palette__option"
                    onMouseMove={() => {
                      if (position !== current) {
                        setActive(position);
                      }
                    }}
                    onClick={() => open(entry)}
                  >
                    <span className="palette__icon">
                      <Icon name={entry.icon} size={16} />
                    </span>
                    <span className="palette__main">
                      <span className="palette__title">{entry.title}</span>
                      {entry.sub ? <span className="palette__sub">{entry.sub}</span> : null}
                    </span>
                    <Icon name="arrowRight" size={14} />
                  </div>
                );
              })}
            </div>
          ))}
          {searching && can('customer.view') ? (
            <p className="palette__note palette__note--quiet">{t('pal.customerHint')}</p>
          ) : null}
        </div>

        <div className="palette__footer">{t('pal.hint')}</div>
      </div>
    </div>
  );
}
