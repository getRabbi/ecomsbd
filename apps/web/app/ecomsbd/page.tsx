import type { Metadata } from 'next';

import { HomePage } from './home';
import { homeMetadata } from './metadata';

export const metadata: Metadata = homeMetadata('en');

/**
 * The public homepage: ecomsbd.com/ (rewritten here) and the App Store
 * Marketing URL. Static: no session, no API calls.
 */
export default function MarketingPage() {
  return <HomePage locale="en" />;
}
