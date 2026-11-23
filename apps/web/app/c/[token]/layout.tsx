import type { Metadata } from 'next';
import type { ReactNode } from 'react';

/**
 * The customer's confirm link (V4 WP2.3). The token in the address is the
 * whole credential, so nothing may carry it on: no indexing, and no referrer
 * sent to anything the page loads.
 */
export const metadata: Metadata = {
  title: 'অর্ডার কনফার্ম · Confirm order',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default function ConfirmLinkLayout({ children }: { children: ReactNode }) {
  return children;
}
