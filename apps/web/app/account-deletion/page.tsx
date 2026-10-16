import type { Metadata } from 'next';
import { AccountDeletion } from './deletion';

export const metadata: Metadata = {
  title: 'Delete your account | ecomsbd',
  description: 'Request deletion of your ecomsbd account and associated data by email or in the app. Read what is removed and which records may be retained.',
  alternates: {
    canonical: 'https://ecomsbd.com/account-deletion',
    languages: { en: 'https://ecomsbd.com/account-deletion', bn: 'https://ecomsbd.com/account-deletion/bn' },
  },
  robots: { index: true, follow: true },
};

export default function AccountDeletionPage() {
  return <AccountDeletion locale="en" />;
}
