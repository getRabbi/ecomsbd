import type { ReactNode } from 'react';

import { copy, links, type Locale } from './content';
import s from './store-links.module.css';

function Glyph({ children }: { children: ReactNode }) {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/**
 * Where to get the mobile app. Google Play is live and linked. The App Store is
 * shown as coming soon and deliberately is not a link: there is no public
 * listing to send anyone to yet.
 */
export function StoreLinks({ locale, compact = false, className }: { locale: Locale; compact?: boolean; className?: string }) {
  const t = copy[locale].stores;
  return (
    <div className={[s.stores, compact && s.compact, className].filter(Boolean).join(' ')}>
      <a className={s.play} href={links.play}>
        <Glyph>
          <path d="M12 3.5v12M7 10.5l5 5 5-5M4.5 20.5h15" />
        </Glyph>
        <span>
          <small>{t.playTop}</small>
          <strong>Google Play</strong>
        </span>
      </a>
      <span className={s.soon}>
        <Glyph>
          <rect x="6.5" y="2.5" width="11" height="19" rx="2.5" />
          <path d="M11 18.5h2" />
        </Glyph>
        <span>
          <small>{t.appStoreTop}</small>
          <strong>App Store</strong>
        </span>
      </span>
    </div>
  );
}
