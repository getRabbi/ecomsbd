'use client';

import { useRef, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';

/**
 * The small-screen menu. A native `<details>`, so it opens and closes before
 * any JavaScript loads; this only closes it again once a link inside is
 * followed (an in-page anchor would otherwise leave it covering the section)
 * or Escape is pressed.
 */
export function MobileMenu({
  className,
  summaryClassName,
  label,
  icon,
  children,
}: {
  className: string | undefined;
  summaryClassName: string | undefined;
  label: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDetailsElement>(null);

  function onClick(event: MouseEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest('a')) {
      ref.current?.removeAttribute('open');
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    const menu = ref.current;
    if (event.key === 'Escape' && menu?.open) {
      menu.removeAttribute('open');
      menu.querySelector('summary')?.focus();
    }
  }

  return (
    <details ref={ref} className={className} onClick={onClick} onKeyDown={onKeyDown}>
      <summary className={summaryClassName} aria-label={label}>
        {icon}
      </summary>
      {children}
    </details>
  );
}
