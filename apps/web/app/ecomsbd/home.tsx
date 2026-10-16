import Image from 'next/image';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { Icon, type IconName } from '@/components/icons';
import { formatPaisa } from '@/lib/money';

import {
  COURIERS,
  copy,
  deletionPath,
  fill,
  homeUrl,
  links,
  parcelTone,
  privacyPath,
  sample,
  sectionIds,
  SITE_URL,
  statusTone,
  type Locale,
  type SiteCopy,
  type Tone,
} from './content';
import { MobileMenu } from './mobile-menu';
import s from './home.module.css';

const toneClass: Record<Tone, string | undefined> = { good: s.good, warn: s.warn, bad: s.bad, neutral: s.neutral };

function taka(paisa: number, locale: Locale): string {
  return formatPaisa(paisa, { locale });
}

function num(value: number, locale: Locale): string {
  return new Intl.NumberFormat(locale === 'bn' ? 'bn-BD' : 'en-BD').format(value);
}

function cx(...names: (string | false | undefined)[]): string {
  return names.filter(Boolean).join(' ');
}

/**
 * The lists in content.ts and the icon lists here line up by position. The page
 * is prerendered, so a missing entry fails the build instead of shipping a gap.
 */
function at<T>(list: readonly T[], index: number): T {
  const item = list[index];
  if (item === undefined) {
    throw new Error(`Homepage content: no entry at position ${index}`);
  }
  return item;
}

/** Glyphs the workspace icon set has no use for, drawn on the same grid. */
const GLYPHS = {
  lock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
    </>
  ),
  shield: <path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.9 7.5-9.5V6z" />,
  monitor: (
    <>
      <rect x="2.5" y="4" width="19" height="12.5" rx="2" />
      <path d="M8.5 20.5h7M12 16.5v4" />
    </>
  ),
  phone: (
    <>
      <rect x="6.5" y="2.5" width="11" height="19" rx="2.5" />
      <path d="M11 18.5h2" />
    </>
  ),
  history: (
    <>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6" />
      <path d="M3.5 4.5V9H8M12 7.5V12l3 2" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  download: <path d="M12 3.5v12M7 10.5l5 5 5-5M4.5 20.5h15" />,
};

function Glyph({ name, size = 20 }: { name: keyof typeof GLYPHS; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {GLYPHS[name]}
    </svg>
  );
}

function Chip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={cx(s.chip, toneClass[tone])}>{children}</span>;
}

function Logo({ size }: { size: number }) {
  return <Image src="/brand/logo-mark.png" alt="" width={size} height={size} unoptimized priority className={s.logo} />;
}

function SectionHead({
  id,
  eyebrow,
  title,
  lead,
  center,
  dark,
}: {
  id: string;
  eyebrow: string;
  title: string;
  lead?: string;
  center?: boolean;
  dark?: boolean;
}) {
  return (
    <div className={cx(s.sectionHead, center && s.center, dark && s.onDark)}>
      <p className={s.eyebrow}>{eyebrow}</p>
      <h2 id={id}>{title}</h2>
      {lead ? <p className={s.lead}>{lead}</p> : null}
    </div>
  );
}

function WindowBar({ url }: { url: string }) {
  return (
    <div className={s.windowBar}>
      <span className={s.dots} aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className={s.url}>
        <Glyph name="lock" size={11} />
        {url}
      </span>
    </div>
  );
}

const APP_NAV_ICONS: IconName[] = ['dashboard', 'orders', 'customers', 'products', 'couriers', 'money', 'reconcile', 'insights'];

function DashboardPreview({ t, locale }: { t: SiteCopy; locale: Locale }) {
  const ui = t.heroUi;
  const kpiValues = [
    taka(sample.kpis[0], locale),
    num(sample.kpis[1], locale),
    taka(sample.kpis[2], locale),
    taka(sample.kpis[3], locale),
  ];
  const attention = sample.attention.map((item, index) =>
    fill(at(ui.attention, index), {
      count: item.count === null ? '' : num(item.count, locale),
      amount: item.amount === null ? '' : taka(item.amount, locale),
    }),
  );

  return (
    <div className={s.heroStage} role="img" aria-label={ui.alt}>
      <div className={s.window}>
        <WindowBar url="app.ecomsbd.com/dashboard" />
        <div className={s.appFrame}>
          <div className={s.appSide}>
            <div className={s.appBrand}>
              <Logo size={20} />
              <span>ecomsbd</span>
            </div>
            <ul>
              {ui.nav.map((label, index) => (
                <li key={label} className={index === 0 ? s.appNavActive : undefined}>
                  <Icon name={at(APP_NAV_ICONS, index)} size={14} />
                  <span>{label}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className={s.appMain}>
            <div className={s.appTop}>
              <div>
                <strong>{ui.shop}</strong>
                <span>{ui.subtitle}</span>
              </div>
              <span className={s.appIcons}>
                <Icon name="search" size={15} />
                <Icon name="bell" size={15} />
              </span>
            </div>
            <div className={s.kpiBand}>
              <div className={s.kpiHead}>
                <span>{ui.today}</span>
                <span className={s.kpiBadge}>{ui.estimate}</span>
              </div>
              <dl className={s.kpis}>
                {ui.kpis.map((label, index) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{kpiValues[index]}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className={s.appCard}>
              <div className={s.appCardHead}>{ui.recent}</div>
              <table className={s.miniTable}>
                <thead>
                  <tr>
                    {ui.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sample.orders.map((order, index) => (
                    <tr key={order.id}>
                      <td className={s.mono}>{order.id}</td>
                      <td>
                        <strong>{at(at(ui.customers, index), 0)}</strong>
                        <small>{at(at(ui.customers, index), 1)}</small>
                      </td>
                      <td className={s.amount}>{taka(order.amount, locale)}</td>
                      <td>
                        <Chip tone={at(statusTone, order.status)}>{at(ui.statuses, order.status)}</Chip>
                      </td>
                      <td>
                        <Chip tone={at(parcelTone, order.parcel)}>{at(ui.parcels, order.parcel)}</Chip>
                        {order.courier ? <small>{order.courier}</small> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
      <div className={s.attention}>
        <p>{ui.attentionTitle}</p>
        <ul>
          {sample.attention.map((item, index) => (
            <li key={attention[index]}>
              <span className={cx(s.dot, toneClass[item.tone])} />
              {attention[index]}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function OrdersPreview({ t, locale }: { t: SiteCopy; locale: Locale }) {
  const ui = t.desktop.ui;
  return (
    <div className={cx(s.window, s.ordersWindow)} role="img" aria-label={ui.alt}>
      <WindowBar url="app.ecomsbd.com/orders" />
      <div className={s.ordersApp}>
        <div className={s.ordersHead}>
          <strong>{ui.title}</strong>
          <span className={s.searchBox}>
            <Icon name="search" size={13} />
          </span>
        </div>
        <div className={s.tabs}>
          {ui.tabs.map((tab, index) => (
            <span key={tab} className={index === 1 ? s.tabActive : undefined}>
              {tab}
            </span>
          ))}
        </div>
        <div className={s.bulkBar}>
          <strong>{ui.selected}</strong>
          <span className={s.bulkButton}>
            <Icon name="couriers" size={14} />
            {ui.book}
          </span>
          <small>{ui.hint}</small>
        </div>
        <table className={cx(s.miniTable, s.ordersTable)}>
          <thead>
            <tr>
              <th aria-hidden="true">
                <span className={s.check} />
              </th>
              {ui.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ui.rows.map((row, index) => (
              <tr key={row[0]} className={index < 3 ? s.rowSelected : undefined}>
                <td>
                  <span className={cx(s.check, index < 3 && s.checked)}>{index < 3 ? <Icon name="check" size={11} /> : null}</span>
                </td>
                <td className={s.mono}>{row[0]}</td>
                <td>
                  <strong>{row[1]}</strong>
                </td>
                <td className={s.muted}>{row[2]}</td>
                <td className={s.amount}>{taka(at(sample.queue, index), locale)}</td>
                <td>
                  <Chip tone="neutral">{t.heroUi.statuses[0]}</Chip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const PHONE_TONES: Tone[] = ['good', 'warn', 'warn', 'bad'];
const PHONE_ICONS: IconName[] = ['couriers', 'reconcile', 'products', 'returns'];

function PhonePreview({ t, locale }: { t: SiteCopy; locale: Locale }) {
  const ui = t.mobile.ui;
  return (
    <div className={s.phone} role="img" aria-label={ui.alt}>
      <div className={s.phoneScreen}>
        <div className={s.phoneStatus}>
          <span>9:41</span>
          <span className={s.phoneSignal} />
        </div>
        <div className={s.phoneHeader}>
          <Logo size={22} />
          <strong>{t.heroUi.shop}</strong>
          <Icon name="bell" size={18} />
        </div>
        <div className={s.phoneHero}>
          <span>{ui.greeting}</span>
          <div>
            <small>{ui.sales}</small>
            <strong>{taka(sample.kpis[0], locale)}</strong>
          </div>
          <div>
            <small>{ui.cod}</small>
            <strong>{taka(sample.kpis[2], locale)}</strong>
          </div>
        </div>
        <p className={s.phoneLabel}>{ui.notifications}</p>
        <ul className={s.phoneList}>
          {ui.items.map((item, index) => (
            <li key={item.title}>
              <span className={cx(s.phoneIcon, toneClass[at(PHONE_TONES, index)])}>
                <Icon name={at(PHONE_ICONS, index)} size={15} />
              </span>
              <span>
                <strong>{item.title}</strong>
                <small>{fill(item.meta, { amount: taka(sample.payout.difference * -1, locale), count: num(2, locale) })}</small>
              </span>
            </li>
          ))}
        </ul>
        <div className={s.phoneTabs} aria-hidden="true">
          <Icon name="dashboard" size={18} />
          <Icon name="orders" size={18} />
          <span className={s.phoneFab}>
            <Icon name="plus" size={18} />
          </span>
          <Icon name="money" size={18} />
          <Icon name="settings" size={18} />
        </div>
      </div>
    </div>
  );
}

function PayoutPreview({ t, locale }: { t: SiteCopy; locale: Locale }) {
  const ui = t.money.ui;
  const p = sample.payout;
  const values = [p.cod, p.delivery, p.codCharge, p.expected, p.paid];
  const lines = [
    { label: ui.lines[0], value: p.matched, tone: 'good' as Tone },
    { label: ui.lines[1], value: p.charge, tone: 'warn' as Tone },
    { label: ui.lines[2], value: p.missing, tone: 'bad' as Tone },
  ];
  return (
    <div className={s.payout} role="img" aria-label={ui.alt}>
      <div className={s.payoutHead}>
        <div>
          <strong>{ui.title}</strong>
          <span>{fill(ui.courier, { count: num(p.parcels, locale) })}</span>
        </div>
        <Chip tone="warn">{ui.status}</Chip>
      </div>
      <dl className={s.payoutRows}>
        {ui.rows.map((label, index) => (
          <div key={label} className={index === 3 ? s.payoutTotal : undefined}>
            <dt>{label}</dt>
            <dd className={at(values, index) < 0 ? s.deduction : undefined}>{taka(at(values, index), locale)}</dd>
          </div>
        ))}
      </dl>
      <div className={s.payoutDiff}>
        <span>{ui.difference}</span>
        <strong>{taka(p.difference, locale)}</strong>
      </div>
      <div className={s.lineBar} aria-hidden="true">
        {lines.map((line) => (
          <span key={line.label} className={toneClass[line.tone]} style={{ flexGrow: line.value }} />
        ))}
      </div>
      <ul className={s.lineLegend}>
        {lines.map((line) => (
          <li key={line.label}>
            <span className={cx(s.dot, toneClass[line.tone])} />
            {line.label}
            <strong>{num(line.value, locale)}</strong>
          </li>
        ))}
      </ul>
      <div className={s.payoutCase}>
        <Icon name="reconcile" size={16} />
        <span>
          {ui.openCase} · {taka(Math.abs(p.difference), locale)}
        </span>
      </div>
    </div>
  );
}

function TrendChart() {
  const width = 320;
  const height = 76;
  const top = Math.max(...sample.trend) * 1.15;
  const step = width / (sample.trend.length - 1);
  const points = sample.trend.map((value, index) => [index * step, height - (value / top) * height] as const);
  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lastX, lastY] = at(points, points.length - 1);
  return (
    <svg className={s.trend} viewBox={`-6 -6 ${width + 12} ${height + 8}`} aria-hidden="true" focusable="false">
      {[0, 1, 2].map((row) => (
        <line key={row} x1="0" x2={width} y1={(height / 3) * row + height / 3} y2={(height / 3) * row + height / 3} className={s.grid} />
      ))}
      <polygon points={`0,${height} ${line} ${width},${height}`} className={s.area} />
      <polyline points={line} className={s.line} />
      <circle cx={lastX} cy={lastY} r="4.5" className={s.marker} />
    </svg>
  );
}

function InsightsPreview({ t, locale }: { t: SiteCopy; locale: Locale }) {
  const ui = t.insights.ui;
  const d = sample.insights;
  const topUnits = Math.max(...sample.products);
  return (
    <div className={s.insights} role="img" aria-label={ui.alt}>
      <div className={s.insHead}>
        <strong>{ui.title}</strong>
        <span className={s.rangePill}>{ui.range}</span>
      </div>
      <div className={s.insTiles}>
        <div>
          <small>{ui.sales}</small>
          <strong>{taka(d.sales, locale)}</strong>
          <span className={s.up}>{fill(ui.vsPrev, { change: num(d.change, locale) })}</span>
        </div>
        <div>
          <small>{ui.profit}</small>
          <strong>{taka(d.profit, locale)}</strong>
          <Chip tone="warn">{ui.estimate}</Chip>
        </div>
        <div>
          <small>{ui.rto}</small>
          <strong>{num(d.rto, locale)}%</strong>
          <span>{fill(ui.rtoOf, { rto: num(d.rtoCount, locale), completed: num(d.completed, locale) })}</span>
        </div>
        <div>
          <small>{ui.receivable}</small>
          <strong>{taka(d.receivable, locale)}</strong>
          <span>{fill(ui.overdue, { amount: taka(d.overdue, locale) })}</span>
        </div>
      </div>
      <div className={s.insCharts}>
        <div className={s.insCard}>
          <div className={s.insCardHead}>
            <strong>{ui.trend}</strong>
            <small>{ui.perDay}</small>
          </div>
          <TrendChart />
        </div>
        <div className={s.insCard}>
          <div className={s.insCardHead}>
            <strong>{ui.top}</strong>
          </div>
          <ul className={s.bars}>
            {sample.products.map((units, index) => (
              <li key={ui.products[index]}>
                <span className={s.barLabel}>
                  {ui.products[index]}
                  <small>{fill(ui.units, { count: num(units, locale) })}</small>
                </span>
                <span className={s.barTrack}>
                  <span style={{ width: `${(units / topUnits) * 100}%` }} />
                </span>
              </li>
            ))}
          </ul>
          <p className={s.stockLine}>
            <span className={cx(s.dot, s.warn)} />
            {ui.stock}: {fill(ui.stockValue, { low: num(d.low, locale), out: num(d.out, locale) })}
          </p>
        </div>
      </div>
    </div>
  );
}

function PlayButton({ t }: { t: SiteCopy }) {
  return (
    <a className={s.playButton} href={links.play}>
      <Glyph name="download" size={22} />
      <span>
        <small>{t.cta.playTop}</small>
        <strong>{t.cta.playBottom}</strong>
      </span>
    </a>
  );
}

const STRIP_ICONS: IconName[] = ['orders', 'couriers', 'money', 'insights', 'products', 'dashboard'];
const FEATURE_ICONS: IconName[] = ['orders', 'couriers', 'money', 'products', 'insights', 'team'];
const DESKTOP_ICONS: IconName[] = ['dashboard', 'orders', 'check', 'products', 'reconcile', 'insights', 'procurement', 'team'];
const COURIER_ICONS: IconName[] = ['connections', 'couriers', 'search', 'money', 'reconcile'];
const MONEY_ICONS: IconName[] = ['document', 'money', 'reconcile', 'returns', 'bell', 'insights'];
const TRUST_ICONS: (keyof typeof GLYPHS | IconName)[] = ['team', 'store', 'history', 'shield', 'lock'];

function AnyIcon({ name, size }: { name: keyof typeof GLYPHS | IconName; size: number }) {
  return name in GLYPHS ? <Glyph name={name as keyof typeof GLYPHS} size={size} /> : <Icon name={name as IconName} size={size} />;
}

/** The public ecomsbd.com homepage, server-rendered in either language. */
export function HomePage({ locale }: { locale: Locale }) {
  const t = copy[locale];
  const other: Locale = locale === 'en' ? 'bn' : 'en';
  // A year is not a quantity: no thousands separator.
  const year = new Intl.NumberFormat(locale === 'bn' ? 'bn-BD' : 'en-BD', { useGrouping: false }).format(new Date().getFullYear());
  // Steps are numbered 1–7 straight through the phases.
  const phaseStart = t.how.phases.map((_, index) =>
    t.how.phases.slice(0, index).reduce((count, phase) => count + phase.steps.length, 0),
  );

  const nav = [
    { href: `#${sectionIds.features}`, label: t.nav.features },
    { href: `#${sectionIds.how}`, label: t.nav.how },
    { href: `#${sectionIds.couriers}`, label: t.nav.couriers },
    { href: `#${sectionIds.webMobile}`, label: t.nav.webMobile },
    { href: `#${sectionIds.faq}`, label: t.nav.faq },
  ];

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'ecomsbd',
    url: SITE_URL,
    description: t.meta.description,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Web, Android',
    inLanguage: locale === 'bn' ? 'bn-BD' : 'en',
    installUrl: links.play,
    image: `${SITE_URL}/brand/og-image.jpg`,
  };

  const languageLink = (className: string | undefined) => (
    <a className={className} href={homeUrl[other]} hrefLang={other} lang={other} aria-label={t.nav.otherLanguageLabel}>
      <Icon name="globe" size={16} />
      {t.nav.otherLanguage}
    </a>
  );

  return (
    <div className={s.site} lang={locale} id="top">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }} />
      <a className={s.skip} href="#main">
        {t.skip}
      </a>

      <header className={s.header}>
        <div className={cx(s.container, s.headerInner)}>
          <a className={s.brand} href="#top">
            <Logo size={34} />
            <span>
              <strong>ecomsbd</strong>
              <small>{t.brandTagline}</small>
            </span>
          </a>
          <nav className={s.nav} aria-label={t.nav.label}>
            <ul>
              {nav.map((item) => (
                <li key={item.href}>
                  <a href={item.href}>{item.label}</a>
                </li>
              ))}
              <li>
                <Link href={links.support}>{t.nav.support}</Link>
              </li>
            </ul>
          </nav>
          <div className={s.headerActions}>
            {languageLink(s.lang)}
            <a className={s.signIn} href={links.signIn}>
              {t.cta.signIn}
            </a>
            <a className={cx(s.btn, s.btnPrimary, s.btnSm)} href={links.start}>
              {t.cta.start}
            </a>
            <MobileMenu className={s.menu} summaryClassName={s.menuButton} label={t.nav.menu} icon={<Icon name="menu" size={22} />}>
              <div className={s.menuPanel}>
                <ul>
                  {nav.map((item) => (
                    <li key={item.href}>
                      <a href={item.href}>{item.label}</a>
                    </li>
                  ))}
                  <li>
                    <Link href={links.support}>{t.nav.support}</Link>
                  </li>
                </ul>
                <div className={s.menuActions}>
                  <a className={cx(s.btn, s.btnPrimary)} href={links.start}>
                    {t.cta.start}
                  </a>
                  <a className={cx(s.btn, s.btnSecondary)} href={links.signIn}>
                    {t.cta.signIn}
                  </a>
                  {languageLink(s.menuLang)}
                </div>
              </div>
            </MobileMenu>
          </div>
        </div>
      </header>

      <main id="main">
        <section className={s.hero} aria-labelledby="hero-title">
          <div className={cx(s.container, s.heroGrid)}>
            <div className={s.heroCopy}>
              <p className={s.eyebrow}>{t.hero.eyebrow}</p>
              <h1 id="hero-title">
                {t.hero.title} <span>{t.hero.titleAccent}</span>
              </h1>
              <p className={s.heroLead}>{t.hero.lead}</p>
              <div className={s.ctaRow}>
                <a className={cx(s.btn, s.btnPrimary, s.btnLg)} href={links.start}>
                  {t.cta.start}
                  <Icon name="arrowRight" size={18} />
                </a>
                <a className={cx(s.btn, s.btnSecondary, s.btnLg)} href={links.signIn}>
                  {t.cta.signIn}
                </a>
              </div>
              <ul className={s.heroPoints}>
                {t.hero.points.map((point) => (
                  <li key={point}>
                    <Icon name="check" size={16} />
                    {point}
                  </li>
                ))}
              </ul>
              <p className={s.heroHint}>{t.hero.hint}</p>
            </div>
            <figure className={s.heroVisual}>
              <DashboardPreview t={t} locale={locale} />
              <figcaption className={s.caption}>{t.hero.caption}</figcaption>
            </figure>
          </div>
        </section>

        <div className={s.strip}>
          <ul className={cx(s.container, s.stripGrid)}>
            {t.strip.map((item, index) => (
              <li key={item.title}>
                <span className={s.stripIcon}>
                  <Icon name={at(STRIP_ICONS, index)} size={20} />
                </span>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.body}</small>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <section id={sectionIds.features} className={cx(s.section, s.grey)} aria-labelledby="features-title">
          <div className={s.container}>
            <SectionHead id="features-title" eyebrow={t.features.eyebrow} title={t.features.title} lead={t.features.lead} center />
            <div className={s.featureGrid}>
              {t.features.groups.map((group, index) => (
                <article key={group.title} className={s.featureCard}>
                  <span className={s.featureIcon}>
                    <Icon name={at(FEATURE_ICONS, index)} size={22} />
                  </span>
                  <h3>{group.title}</h3>
                  <p>{group.summary}</p>
                  <ul className={s.checkList}>
                    {group.items.map((item) => (
                      <li key={item}>
                        <Icon name="check" size={15} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id={sectionIds.how} className={s.section} aria-labelledby="how-title">
          <div className={s.container}>
            <SectionHead id="how-title" eyebrow={t.how.eyebrow} title={t.how.title} lead={t.how.lead} center />
            <ol className={s.phases}>
              {t.how.phases.map((phase, phaseIndex) => (
                <li key={phase.label} className={cx(s.phase, phase.steps.length === 1 && s.phaseSingle)}>
                  <p className={s.phaseLabel}>{phase.label}</p>
                  <ol className={s.steps}>
                    {phase.steps.map((step, stepIndex) => (
                      <li key={step.title} className={s.step}>
                        <span className={s.stepNumber}>{num(at(phaseStart, phaseIndex) + stepIndex + 1, locale)}</span>
                        <h3>{step.title}</h3>
                        <p>{step.body}</p>
                      </li>
                    ))}
                  </ol>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id={sectionIds.webMobile} className={cx(s.section, s.grey)} aria-labelledby="desktop-title">
          <div className={cx(s.container, s.split)}>
            <div className={s.splitCopy}>
              <SectionHead id="desktop-title" eyebrow={t.desktop.eyebrow} title={t.desktop.title} lead={t.desktop.lead} />
              <ul className={s.miniGrid}>
                {t.desktop.items.map((item, index) => (
                  <li key={item.title}>
                    <Icon name={at(DESKTOP_ICONS, index)} size={18} />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.body}</small>
                    </span>
                  </li>
                ))}
              </ul>
              <div className={s.ctaRow}>
                <a className={cx(s.btn, s.btnPrimary)} href={links.app}>
                  {t.cta.openApp}
                  <Icon name="arrowRight" size={18} />
                </a>
              </div>
            </div>
            <div className={s.splitVisual}>
              <OrdersPreview t={t} locale={locale} />
            </div>
          </div>
        </section>

        <section className={s.section} aria-labelledby="mobile-title">
          <div className={cx(s.container, s.split, s.splitMobile)}>
            <div className={s.phoneStage}>
              <PhonePreview t={t} locale={locale} />
            </div>
            <div className={s.splitCopy}>
              <SectionHead id="mobile-title" eyebrow={t.mobile.eyebrow} title={t.mobile.title} lead={t.mobile.lead} />
              <ul className={s.checkList}>
                {t.mobile.items.map((item) => (
                  <li key={item}>
                    <Icon name="check" size={15} />
                    {item}
                  </li>
                ))}
              </ul>
              <div className={s.storeRow}>
                <PlayButton t={t} />
                <p className={s.iosNote}>
                  <Glyph name="clock" size={16} />
                  {t.mobile.ios}
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className={cx(s.section, s.grey, s.tight)} aria-labelledby="together-title">
          <div className={s.container}>
            <div className={s.together}>
              <div className={s.togetherHead}>
                <h2 id="together-title">{t.together.title}</h2>
                <p>{t.together.lead}</p>
              </div>
              <ul className={s.togetherCols}>
                {t.together.cols.map((col, index) => (
                  <li key={col.title} className={index === 2 ? s.togetherBoth : undefined}>
                    <span className={s.togetherIcon}>
                      {index === 0 ? <Glyph name="monitor" size={22} /> : index === 1 ? <Glyph name="phone" size={22} /> : <Icon name="check" size={22} />}
                    </span>
                    <strong>{col.title}</strong>
                    <span>{col.body}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section id={sectionIds.couriers} className={s.section} aria-labelledby="couriers-title">
          <div className={s.container}>
            <SectionHead id="couriers-title" eyebrow={t.couriers.eyebrow} title={t.couriers.title} lead={t.couriers.lead} center />
            <ul className={s.courierRow}>
              {COURIERS.map((name) => (
                <li key={name}>
                  <span className={s.courierMark} aria-hidden="true">
                    {name.charAt(0)}
                  </span>
                  <span>
                    <strong>{name}</strong>
                    <small>{t.couriers.via}</small>
                  </span>
                  <Chip tone="good">{t.couriers.active}</Chip>
                </li>
              ))}
            </ul>
            <ul className={s.capGrid}>
              {t.couriers.capabilities.map((cap, index) => (
                <li key={cap.title}>
                  <Icon name={at(COURIER_ICONS, index)} size={20} />
                  <strong>{cap.title}</strong>
                  <span>{cap.body}</span>
                </li>
              ))}
            </ul>
            <p className={s.note}>{t.couriers.note}</p>
          </div>
        </section>

        <section id="money" className={cx(s.section, s.navy)} aria-labelledby="money-title">
          <div className={cx(s.container, s.split)}>
            <div className={s.splitCopy}>
              <SectionHead id="money-title" eyebrow={t.money.eyebrow} title={t.money.title} lead={t.money.lead} dark />
              <ul className={s.moneyList}>
                {t.money.items.map((item, index) => (
                  <li key={item.title}>
                    <Icon name={at(MONEY_ICONS, index)} size={18} />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.body}</small>
                    </span>
                  </li>
                ))}
              </ul>
              <p className={s.moneyNote}>{t.money.note}</p>
            </div>
            <div className={s.splitVisual}>
              <PayoutPreview t={t} locale={locale} />
            </div>
          </div>
        </section>

        <section id="insights" className={cx(s.section, s.grey)} aria-labelledby="insights-title">
          <div className={s.container}>
            <SectionHead id="insights-title" eyebrow={t.insights.eyebrow} title={t.insights.title} lead={t.insights.lead} center />
            <ul className={s.insightTags}>
              {t.insights.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <InsightsPreview t={t} locale={locale} />
          </div>
        </section>

        <section id="coming-soon" className={s.section} aria-labelledby="soon-title">
          <div className={s.container}>
            <div className={cx(s.sectionHead, s.center)}>
              <p className={s.soonBadge}>
                <Glyph name="clock" size={14} />
                {t.soon.badge}
              </p>
              <h2 id="soon-title">{t.soon.title}</h2>
              <p className={s.lead}>{t.soon.lead}</p>
            </div>
            <ul className={s.soonGrid}>
              {t.soon.channels.map((channel) => (
                <li key={channel.name}>
                  <strong>{channel.name}</strong>
                  <span>{channel.body}</span>
                  <Chip tone="neutral">{t.soon.chip}</Chip>
                </li>
              ))}
            </ul>
            <p className={s.soonToday}>
              <Icon name="inbox" size={18} />
              {t.soon.today}
            </p>
          </div>
        </section>

        <section id="security" className={cx(s.section, s.grey)} aria-labelledby="trust-title">
          <div className={s.container}>
            <SectionHead id="trust-title" eyebrow={t.trust.eyebrow} title={t.trust.title} lead={t.trust.lead} center />
            <ul className={s.trustGrid}>
              {t.trust.items.map((item, index) => (
                <li key={item.title}>
                  <span className={s.trustIcon}>
                    <AnyIcon name={at(TRUST_ICONS, index)} size={20} />
                  </span>
                  <strong>{item.title}</strong>
                  <span>{item.body}</span>
                </li>
              ))}
              <li className={s.trustLink}>
                <Link href={privacyPath[locale]}>
                  {t.trust.link}
                  <Icon name="arrowRight" size={16} />
                </Link>
              </li>
            </ul>
          </div>
        </section>

        <section id="free" className={s.section} aria-labelledby="free-title">
          <div className={s.container}>
            <div className={s.freeCard}>
              <div>
                <p className={s.eyebrow}>{t.free.eyebrow}</p>
                <h2 id="free-title">{t.free.title}</h2>
                <p className={s.lead}>{t.free.lead}</p>
                <div className={s.ctaRow}>
                  <a className={cx(s.btn, s.btnPrimary, s.btnLg)} href={links.start}>
                    {t.cta.start}
                    <Icon name="arrowRight" size={18} />
                  </a>
                </div>
              </div>
              <ul className={s.checkList}>
                {t.free.points.map((point) => (
                  <li key={point}>
                    <Icon name="check" size={15} />
                    {point}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section id={sectionIds.faq} className={cx(s.section, s.grey)} aria-labelledby="faq-title">
          <div className={cx(s.container, s.faqLayout)}>
            <div>
              <SectionHead id="faq-title" eyebrow={t.faq.eyebrow} title={t.faq.title} />
              <Link className={s.faqSupport} href={links.support}>
                {t.faq.support}
                <Icon name="arrowRight" size={16} />
              </Link>
            </div>
            <div className={s.faqList}>
              {t.faq.items.map((item) => (
                <details key={item.q}>
                  <summary>
                    {item.q}
                    <Icon name="chevronDown" size={18} />
                  </summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className={s.final} aria-labelledby="final-title">
          <div className={cx(s.container, s.finalInner)}>
            <h2 id="final-title">{t.final.title}</h2>
            <p>{t.final.lead}</p>
            <div className={s.ctaRow}>
              <a className={cx(s.btn, s.btnPrimary, s.btnLg)} href={links.start}>
                {t.cta.start}
                <Icon name="arrowRight" size={18} />
              </a>
              <a className={cx(s.btn, s.btnOnDark, s.btnLg)} href={links.signIn}>
                {t.cta.signIn}
              </a>
            </div>
            <ul className={s.finalPoints}>
              {t.hero.points.map((point) => (
                <li key={point}>
                  <Icon name="check" size={15} />
                  {point}
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      <footer className={s.footer}>
        <div className={cx(s.container, s.footerGrid)}>
          <div className={s.footerBrand}>
            <span className={s.brand}>
              <Logo size={30} />
              <span>
                <strong>ecomsbd</strong>
                <small>{t.footer.tagline}</small>
              </span>
            </span>
            {languageLink(s.footerLang)}
          </div>
          <nav aria-label={t.footer.product}>
            <p>{t.footer.product}</p>
            <ul>
              <li>
                <a href={`#${sectionIds.features}`}>{t.nav.features}</a>
              </li>
              <li>
                <a href={links.app}>{t.footer.webApp}</a>
              </li>
              <li>
                <a href={links.play}>{t.footer.android}</a>
              </li>
              <li>
                <a href={`#${sectionIds.couriers}`}>{t.nav.couriers}</a>
              </li>
            </ul>
          </nav>
          <nav aria-label={t.footer.resources}>
            <p>{t.footer.resources}</p>
            <ul>
              <li>
                <Link href={links.support}>{t.nav.support}</Link>
              </li>
              <li>
                <a href={`#${sectionIds.faq}`}>{t.nav.faq}</a>
              </li>
            </ul>
          </nav>
          <nav aria-label={t.footer.legal}>
            <p>{t.footer.legal}</p>
            <ul>
              <li>
                <Link href={privacyPath[locale]}>{t.footer.privacy}</Link>
              </li>
              <li>
                <Link href={deletionPath[locale]}>{t.footer.deletion}</Link>
              </li>
            </ul>
          </nav>
          <nav aria-label={t.footer.account}>
            <p>{t.footer.account}</p>
            <ul>
              <li>
                <a href={links.signIn}>{t.cta.signIn}</a>
              </li>
              <li>
                <a href={links.dashboard}>{t.cta.openDashboard}</a>
              </li>
            </ul>
          </nav>
        </div>
        <div className={cx(s.container, s.footerBottom)}>
          <span>
            © {year} ecomsbd. {t.footer.rights}
          </span>
          <span>ecomsbd.com</span>
        </div>
      </footer>
    </div>
  );
}
