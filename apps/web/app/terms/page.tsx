import type { Metadata } from 'next';

import { TermsOfService } from './terms';

export const metadata: Metadata = {
  title: 'Terms of Service | ecomsbd',
  description: 'The rules for using ecomsbd: Free Launch Mode, your shop data, your own courier accounts, messages to your customers, and the limits of the service.',
  alternates: {
    canonical: 'https://ecomsbd.com/terms',
    languages: { en: 'https://ecomsbd.com/terms', bn: 'https://ecomsbd.com/terms/bn' },
  },
  robots: { index: true, follow: true },
};

export default function TermsPage() {
  return <TermsOfService locale="en" />;
}
