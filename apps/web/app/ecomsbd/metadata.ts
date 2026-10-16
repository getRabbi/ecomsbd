import type { Metadata } from 'next';

import { copy, homeUrl, SITE_URL, type Locale } from './content';

const OG_IMAGE = '/brand/og-image.jpg';

/** Search and share metadata for the homepage in one language. */
export function homeMetadata(locale: Locale): Metadata {
  const t = copy[locale];
  const url = locale === 'en' ? SITE_URL : homeUrl.bn;
  return {
    metadataBase: new URL(SITE_URL),
    title: t.meta.title,
    description: t.meta.description,
    applicationName: 'ecomsbd',
    alternates: {
      canonical: url,
      languages: { en: SITE_URL, bn: homeUrl.bn, 'x-default': SITE_URL },
    },
    robots: { index: true, follow: true },
    openGraph: {
      type: 'website',
      url,
      siteName: 'ecomsbd',
      title: t.meta.title,
      description: t.meta.description,
      locale: locale === 'bn' ? 'bn_BD' : 'en_US',
      alternateLocale: locale === 'bn' ? ['en_US'] : ['bn_BD'],
      images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: t.meta.ogAlt }],
    },
    twitter: {
      card: 'summary_large_image',
      title: t.meta.title,
      description: t.meta.description,
      images: [{ url: OG_IMAGE, alt: t.meta.ogAlt }],
    },
  };
}
