'use client';

import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';

import { BrandMark, Card, ErrorState, LocaleToggle } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { useSession } from '@/lib/session';
import { rememberedShop, useShop, workspaceReady } from '@/lib/shop';
import {
  BUSINESS_CATEGORIES,
  createPayload,
  initialShopValues,
  updatePayload,
  validateShop,
  type ShopDetails,
  type ShopFormValues,
} from '@/lib/shop-form';
import { useApi } from '@/lib/useApi';

type Mode = 'create' | 'finish' | 'edit';

/**
 * The shop-details form, used three ways: creating the first shop, finishing
 * one whose setup was never completed, and editing from Settings.
 *
 * Same fields and same order as the mobile onboarding screen, against the same
 * endpoints, so a shop set up on either client reads identically on the other.
 */
export function ShopForm({
  mode,
  shop,
  readOnly = false,
  onDone,
}: {
  mode: Mode;
  shop: ShopDetails | null;
  readOnly?: boolean;
  onDone: () => void;
}) {
  const { t } = useSession();
  const initial = useMemo(() => initialShopValues(shop), [shop]);
  const [values, setValues] = useState<ShopFormValues>(initial);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [notice, setNotice] = useState<'saved' | 'unchanged' | null>(null);
  // A retry after the completion step failed must not create a second shop.
  const [created, setCreated] = useState(false);

  const problems = attempted ? validateShop(values) : [];

  function set<K extends keyof ShopFormValues>(field: K, value: ShopFormValues[K]) {
    setValues((current) => ({ ...current, [field]: value }));
    setNotice(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    if (validateShop(values).length > 0) {
      return;
    }
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      if (mode === 'create') {
        if (!created) {
          await api.post('/tenants', createPayload(values));
          setCreated(true);
        }
        await api.patch('/tenant', { onboarding_step: 'COMPLETE' });
      } else if (mode === 'finish') {
        await api.patch('/tenant', { ...updatePayload(initial, values), onboarding_step: 'COMPLETE' });
      } else {
        const changes = updatePayload(initial, values);
        if (Object.keys(changes).length === 0) {
          setNotice('unchanged');
          return;
        }
        await api.patch('/tenant', changes);
        setValues((current) => ({ ...current, pickup_phone: '' }));
        setNotice('saved');
      }
      onDone();
    } catch (caught) {
      setFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  const submitLabel =
    mode === 'create'
      ? busy
        ? t('onb.creating')
        : t('onb.create')
      : mode === 'finish'
        ? t('onb.finish')
        : busy
          ? t('set.saving')
          : t('set.save');

  return (
    <form className="shopform" onSubmit={submit} noValidate>
      <div className="formgrid">
        <Field label={t('onb.shopName')} full error={problems.includes('name') ? t('onb.shopNameRequired') : null}>
          {(id, describedBy) => (
            <input
              id={id}
              className="input"
              value={values.name}
              maxLength={160}
              required
              readOnly={readOnly}
              aria-invalid={problems.includes('name')}
              aria-describedby={describedBy}
              autoComplete="organization"
              autoFocus={mode !== 'edit'}
              onChange={(event) => set('name', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('onb.businessType')}>
          {(id) => (
            <select
              id={id}
              className="select"
              value={values.business_category}
              disabled={readOnly}
              onChange={(event) => set('business_category', event.target.value)}
            >
              {BUSINESS_CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {t(`onb.category.${category}` as StringKey)}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>

      <div className="formsection">
        <h3 className="formsection__title">{t('onb.pickupSection')}</h3>
        <p className="card__hint">{t('onb.pickupHint')}</p>
      </div>
      <div className="formgrid">
        <Field label={t('onb.pickupContact')}>
          {(id) => (
            <input
              id={id}
              className="input"
              value={values.pickup_contact_name}
              maxLength={160}
              readOnly={readOnly}
              autoComplete="name"
              onChange={(event) => set('pickup_contact_name', event.target.value)}
            />
          )}
        </Field>
        <Field
          label={t('onb.pickupPhone')}
          hint={mode === 'create' ? undefined : t('onb.pickupPhoneKept')}
          error={problems.includes('phone') ? t('onb.pickupPhoneInvalid') : null}
        >
          {(id, describedBy) => (
            <input
              id={id}
              className="input"
              type="tel"
              inputMode="tel"
              value={values.pickup_phone}
              maxLength={24}
              readOnly={readOnly}
              placeholder="01XXXXXXXXX"
              aria-invalid={problems.includes('phone')}
              aria-describedby={describedBy}
              autoComplete="tel"
              onChange={(event) => set('pickup_phone', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('onb.pickupAddress')} full>
          {(id) => (
            <textarea
              id={id}
              className="input textarea"
              value={values.pickup_address}
              maxLength={1000}
              rows={2}
              readOnly={readOnly}
              autoComplete="street-address"
              onChange={(event) => set('pickup_address', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('onb.district')}>
          {(id) => (
            <input
              id={id}
              className="input"
              value={values.pickup_district}
              maxLength={80}
              readOnly={readOnly}
              onChange={(event) => set('pickup_district', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('onb.area')}>
          {(id) => (
            <input
              id={id}
              className="input"
              value={values.pickup_area}
              maxLength={120}
              readOnly={readOnly}
              onChange={(event) => set('pickup_area', event.target.value)}
            />
          )}
        </Field>
      </div>

      {mode === 'create' ? (
        <div className="notice">
          <div>
            <strong>{t('onb.manualTitle')}</strong>
            <p className="card__hint">{t('onb.manualBody')}</p>
          </div>
        </div>
      ) : null}

      {failure ? <p className="formerror" role="alert">{failureText(failure, t)}</p> : null}

      {readOnly ? null : (
        <div className="formactions">
          <span role="status" className="card__hint">
            {notice === 'saved' ? t('set.saved') : notice === 'unchanged' ? t('set.noChanges') : ''}
          </span>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {submitLabel}
          </button>
        </div>
      )}
    </form>
  );
}

function failureText(failure: unknown, t: ReturnType<typeof useSession>['t']): string {
  if (failure instanceof ApiError) {
    return failure.isPermissionFailure ? t('set.readOnly') : failure.message;
  }
  return t('err.offline');
}

function Field({
  label,
  hint,
  error,
  full = false,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  full?: boolean;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className={`field${full ? ' formgrid__full' : ''}`}>
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children(id, describedBy)}
      {error ? (
        <span id={`${id}-error`} className="field__error">
          {error}
        </span>
      ) : hint ? (
        <span id={`${id}-hint`} className="field__hint">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

/** Full-page frame for the screens shown before the workspace opens. */
function SetupFrame({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  const { t, signOut } = useSession();
  return (
    <main className="setuppage">
      <div className="setuppage__inner">
        <div className="setuppage__top">
          <span className="brandpill">
            <BrandMark size={28} />
            {t('app.name')}
          </span>
          <span className="setuppage__tools">
            <LocaleToggle />
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => void signOut()}>
              {t('auth.signOut')}
            </button>
          </span>
        </div>
        <h1 className="setuppage__title">{title}</h1>
        {subtitle ? <p className="setuppage__subtitle">{subtitle}</p> : null}
        {children}
      </div>
    </main>
  );
}

function FinishSetup() {
  const { t } = useSession();
  const shop = useShop();
  const tenant = useApi<ShopDetails>('/tenant');
  return (
    <SetupFrame title={t('onb.finishTitle')} subtitle={t('onb.finishSubtitle')}>
      <Card>
        {tenant.error ? (
          <ErrorState error={tenant.error} onRetry={tenant.reload} />
        ) : tenant.data ? (
          <ShopForm mode="finish" shop={tenant.data} onDone={shop.reload} />
        ) : (
          <p className="card__hint">{t('common.loading')}</p>
        )}
      </Card>
    </SetupFrame>
  );
}

/**
 * The member belongs to several shops and this session has none selected yet.
 * The shop last used in this browser opens directly; otherwise they choose.
 */
function ShopPicker() {
  const { t } = useSession();
  const { me, switchShop } = useShop();
  const shops = me?.tenants ?? [];
  const [remembered] = useState(() => rememberedShop());
  const [failed, setFailed] = useState(false);
  const [choosing, setChoosing] = useState<string | null>(null);
  const attempted = useRef(false);
  const autoOpen = !failed && remembered !== null && shops.some((shop) => shop.id === remembered);

  useEffect(() => {
    if (!autoOpen || attempted.current || remembered === null) {
      return;
    }
    attempted.current = true;
    switchShop(remembered).catch(() => setFailed(true));
  }, [autoOpen, remembered, switchShop]);

  if (autoOpen) {
    return <FullPageMessage text={t('shop.switching')} />;
  }

  async function choose(id: string) {
    setChoosing(id);
    setFailed(false);
    try {
      await switchShop(id);
    } catch {
      setFailed(true);
      setChoosing(null);
    }
  }

  return (
    <SetupFrame title={t('shop.chooseTitle')} subtitle={t('shop.chooseHint')}>
      <Card padded={false}>
        <ul className="shoplist">
          {shops.map((shop) => (
            <li key={shop.id}>
              <button
                type="button"
                className="shoplist__item"
                disabled={choosing !== null}
                onClick={() => void choose(shop.id)}
              >
                <span className="shopavatar" aria-hidden="true">
                  {initialOf(shop.name)}
                </span>
                <span className="shoplist__main">
                  <strong>{shop.name}</strong>
                  <span className="table__sub">
                    {roleLabel(shop.role, t)}
                    {shop.onboarding_complete ? '' : ` · ${t('shop.setupIncomplete')}`}
                  </span>
                </span>
                <span className="shoplist__go" aria-hidden="true">
                  {choosing === shop.id ? t('common.loading') : '→'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {failed ? <p className="formerror" role="alert">{t('shop.switchFailed')}</p> : null}
    </SetupFrame>
  );
}

export function FullPageMessage({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <div className="authpage">
      <div style={{ textAlign: 'center' }}>
        <p className="card__hint">{text}</p>
        {children}
      </div>
    </div>
  );
}

/**
 * The workspace renders for a session that points at a shop whose setup is
 * done — confirmed by `/me`, or remembered from this browser while `/me`
 * confirms. Anyone else gets the browser's own shop setup or shop picker, so a
 * seller who only ever uses a laptop is never told to finish on a phone.
 */
export function ShopGate({ children }: { children: ReactNode }) {
  const { t, signOut } = useSession();
  const shop = useShop();
  const me = shop.me;

  if (shop.switchingTo) {
    return shop.error ? (
      <FullPageMessage text={t('shop.switchFailed')}>
        <ErrorState error={shop.error} onRetry={shop.reload} />
      </FullPageMessage>
    ) : (
      <FullPageMessage text={t('shop.switching')} />
    );
  }

  if (!me) {
    // This browser has opened this person's shop before: show it now and let
    // `/me` confirm. If `/me` fails, the screens report their own errors.
    if (shop.workspaceKey) {
      return <>{children}</>;
    }
    if (shop.error) {
      return (
        <FullPageMessage text={t('shop.loadFailed')}>
          <ErrorState error={shop.error} onRetry={shop.reload} />
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void signOut()}>
            {t('auth.signOut')}
          </button>
        </FullPageMessage>
      );
    }
    return <FullPageMessage text={t('common.loading')} />;
  }

  if (workspaceReady(me)) {
    return <>{children}</>;
  }

  if (me.tenant_id === null) {
    if (me.tenants.length === 0) {
      return (
        <SetupFrame title={t('onb.title')} subtitle={t('onb.subtitle')}>
          <Card>
            <ShopForm mode="create" shop={null} onDone={shop.reload} />
          </Card>
        </SetupFrame>
      );
    }
    return <ShopPicker />;
  }

  return <FinishSetup />;
}

export function initialOf(name: string | null | undefined): string {
  const trimmed = (name ?? '').trim();
  return trimmed ? Array.from(trimmed)[0]!.toUpperCase() : '?';
}

export function roleLabel(role: string | null | undefined, t: ReturnType<typeof useSession>['t']): string {
  if (!role) {
    return '';
  }
  const key = `role.${role}` as StringKey;
  const label = t(key);
  return label === key ? role.replaceAll('_', ' ').toLowerCase() : label;
}
