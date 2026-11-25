import type { Metadata } from 'next';

import { TermsOfService } from '../terms';

export const metadata: Metadata = {
  title: 'সেবার শর্তাবলি | ecomsbd',
  description: 'ecomsbd ব্যবহারের নিয়ম: ফ্রি লঞ্চ মোড, আপনার দোকানের তথ্য, আপনার নিজের কুরিয়ার অ্যাকাউন্ট, ক্রেতাকে পাঠানো মেসেজ এবং সেবার সীমা।',
  alternates: {
    canonical: 'https://ecomsbd.com/terms/bn',
    languages: { en: 'https://ecomsbd.com/terms', bn: 'https://ecomsbd.com/terms/bn' },
  },
  robots: { index: true, follow: true },
};

export default function BanglaTermsPage() {
  return <TermsOfService locale="bn" />;
}
