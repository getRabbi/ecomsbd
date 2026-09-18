import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';

import { SessionProvider } from '@/lib/session';

import './globals.css';

export const metadata: Metadata = {
  title: 'ecomsbd — Seller workstation',
  description:
    'Desktop workstation for ecomsbd sellers: orders, couriers, money and reconciliation.',
  // A seller dashboard has nothing to gain from being indexed, and its URLs
  // are not public content.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
