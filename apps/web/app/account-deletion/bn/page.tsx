import type { Metadata } from 'next';
import { AccountDeletion } from '../deletion';

export const metadata: Metadata = {
  title: 'অ্যাকাউন্ট মুছে ফেলার অনুরোধ | ecomsbd',
  description: 'ইমেইল বা অ্যাপের মাধ্যমে ecomsbd অ্যাকাউন্ট ও সংশ্লিষ্ট তথ্য মুছে ফেলার অনুরোধ করুন। কী সরানো হয় ও কোন রেকর্ড থাকতে পারে তা জানুন।',
  alternates: {
    canonical: 'https://scalemyprints.com/account-deletion/bn',
    languages: { en: 'https://scalemyprints.com/account-deletion', bn: 'https://scalemyprints.com/account-deletion/bn' },
  },
  robots: { index: true, follow: true },
};

export default function BanglaAccountDeletionPage() {
  return <AccountDeletion locale="bn" />;
}
