import type { Metadata } from 'next';

import { PrivacyPolicy } from '../policy';

export const metadata: Metadata = {
  title: 'গোপনীয়তা নীতি | ecomsbd',
  description: 'ecomsbd কীভাবে অ্যাকাউন্ট, ক্রেতা, অর্ডার ও ব্যবসার তথ্য ব্যবহার করে এবং তথ্য সংরক্ষণ, নিরাপত্তা ও মুছে ফেলার ব্যবস্থা।',
  alternates: {
    canonical: 'https://scalemyprints.com/privacy-policy/bn',
    languages: { en: 'https://scalemyprints.com/privacy-policy', bn: 'https://scalemyprints.com/privacy-policy/bn' },
  },
  robots: { index: true, follow: true },
};

export default function BanglaPrivacyPolicyPage() {
  return <PrivacyPolicy locale="bn" />;
}
