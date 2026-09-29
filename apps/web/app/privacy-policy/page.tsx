import type { Metadata } from 'next';

import { PrivacyPolicy } from './policy';

export const metadata: Metadata = {
  title: 'Privacy Policy | ecomsbd',
  description: 'How ecomsbd handles account, customer, order and operational information, connected services, retention, security and deletion.',
  alternates: {
    canonical: 'https://scalemyprints.com/privacy-policy',
    languages: { en: 'https://scalemyprints.com/privacy-policy', bn: 'https://scalemyprints.com/privacy-policy/bn' },
  },
  robots: { index: true, follow: true },
};

export default function PrivacyPolicyPage() {
  return <PrivacyPolicy locale="en" />;
}
