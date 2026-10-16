import type { Metadata } from 'next';

import { HomePage } from '../home';
import { homeMetadata } from '../metadata';

export const metadata: Metadata = homeMetadata('bn');

/** The Bangla homepage: ecomsbd.com/bn (rewritten here). */
export default function BanglaMarketingPage() {
  return <HomePage locale="bn" />;
}
